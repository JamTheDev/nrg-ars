"""Write-side booking rules. No HTTP here -- see ARCHITECTURE.md section 8.

The concurrency policy (section 5) is: let every request try, and let the
database decide. Nothing is checked for availability up front, because a check
that passes proves nothing by the time the INSERT runs. The winner is whoever
commits first; the loser gets `SeatTakenError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction

from reservations.exceptions import (
    CouldNotAssignError,
    FlightFullError,
    InvalidPassengerError,
    InvalidSeatError,
    NothingSelectedError,
    SeatNotFoundError,
    SeatTakenError,
)
from reservations.models import Booking, Flight, Passenger, Seat

SEAT_RE = re.compile(r'^\s*(?P<row>\d{1,2})\s*(?P<column>[A-Za-z])\s*$')

# Auto-assignment walks to the next free seat when it loses a race. Three
# attempts is plenty: each loss means another kiosk committed in the gap.
MAX_ASSIGN_RETRIES = 3


@dataclass(frozen=True)
class SeatRequest:
    """One seat and the passenger who wants it."""

    designation: str
    passenger_name: str


def parse_designation(raw: str) -> tuple[int, str]:
    """'12c' -> (12, 'C'). Raises InvalidSeatError on anything else."""
    match = SEAT_RE.match(raw or '')
    if not match:
        raise InvalidSeatError(raw)
    return int(match['row']), match['column'].upper()


def resolve_seat(raw: str) -> Seat:
    """Find the cabin position named by `raw`.

    Malformed input and a well-formed seat that does not exist are different
    failures, and the passenger deserves to be told which.
    """
    row, column = parse_designation(raw)
    try:
        return Seat.objects.get(row=row, column=column)
    except Seat.DoesNotExist:
        raise SeatNotFoundError(
            f'{row}{column}', settings.CABIN_ROWS, settings.CABIN_COLUMNS
        ) from None


def _clean_name(raw: str) -> str:
    name = (raw or '').strip()
    if not name:
        raise InvalidPassengerError()
    return name


def book_seats(flight: Flight, requests: list[SeatRequest]) -> list[Booking]:
    """Book every requested seat, or none of them.

    All-or-nothing: a party that asked for three seats together is not served
    by handing them two and an apology, and a partial success would leave the
    passenger paying for seats they did not agree to.

    Each insert runs in its own savepoint so an IntegrityError can be caught
    and named -- after one, Django marks the transaction for rollback and any
    further query in the same block raises TransactionManagementError.
    """
    if not requests:
        raise NothingSelectedError()

    bookings = []
    with transaction.atomic():
        for request in requests:
            seat = resolve_seat(request.designation)
            name = _clean_name(request.passenger_name)
            passenger = Passenger.objects.create(full_name=name)
            try:
                with transaction.atomic():
                    bookings.append(
                        Booking.objects.create(flight=flight, seat=seat, passenger=passenger)
                    )
            except IntegrityError as exc:
                # Rolls the whole booking back, including the seats that did land.
                raise SeatTakenError(seat.designation) from exc

    return bookings


def assign_first_available(flight: Flight, passenger_name: str) -> Booking:
    """Book the lowest-numbered free seat: row order, then column order.

    Losing the race here is not a failure. The passenger asked for *a* seat,
    not for 1A, so a lost seat means taking the next one rather than refusing
    them -- the one deliberate exception to "the loser is blocked".
    """
    name = _clean_name(passenger_name)

    for _ in range(MAX_ASSIGN_RETRIES):
        seat = Seat.objects.exclude(bookings__flight=flight).order_by('row', 'column').first()
        if seat is None:
            raise FlightFullError(flight.number)

        passenger = Passenger.objects.create(full_name=name)
        try:
            with transaction.atomic():
                return Booking.objects.create(flight=flight, seat=seat, passenger=passenger)
        except IntegrityError:
            passenger.delete()
            continue

    raise CouldNotAssignError(flight.number)
