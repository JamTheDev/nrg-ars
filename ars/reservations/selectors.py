"""Read-side queries. No HTTP, no writes -- see ARCHITECTURE.md section 8."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from reservations.models import Booking, Flight, Seat


@dataclass(frozen=True)
class FlightRow:
    """A flight plus its derived seat availability, ready to render."""

    flight: Flight
    seats_total: int
    seats_available: int

    @property
    def is_full(self) -> bool:
        return self.seats_available <= 0

    @property
    def has_departed(self) -> bool:
        return self.flight.departs_at <= timezone.now()


@dataclass(frozen=True)
class SeatCell:
    """One seat as the cabin view needs it: the position plus its occupancy."""

    seat: Seat
    is_taken: bool

    @property
    def designation(self) -> str:
        return self.seat.designation


@dataclass(frozen=True)
class CabinRow:
    """A row split at the aisle, so the template stays free of layout logic."""

    number: int
    left: list[SeatCell]
    right: list[SeatCell]

    @property
    def cells(self) -> list[SeatCell]:
        return self.left + self.right


@dataclass(frozen=True)
class Cabin:
    """The whole seat map for one flight."""

    rows: list[CabinRow]

    @property
    def seats_total(self) -> int:
        return sum(len(row.cells) for row in self.rows)

    @property
    def seats_taken(self) -> int:
        return sum(1 for row in self.rows for cell in row.cells if cell.is_taken)

    @property
    def seats_available(self) -> int:
        return self.seats_total - self.seats_taken

    @property
    def columns_left(self) -> str:
        return settings.CABIN_COLUMNS[: aisle_index()]

    @property
    def columns_right(self) -> str:
        return settings.CABIN_COLUMNS[aisle_index() :]


def cabin_capacity() -> int:
    """Size of the shared seat catalog -- the same for every flight."""
    return Seat.objects.count()


def flight_rows() -> list[FlightRow]:
    """Every flight in departure order, with seats remaining.

    Two queries regardless of flight count: one for the catalog size, one for
    the flights with their booking counts annotated. Availability is derived
    from `Booking`, so it can never drift out of sync.
    """
    capacity = cabin_capacity()
    flights = Flight.objects.annotate(booked_count=Count('bookings'))
    return [
        FlightRow(
            flight=flight,
            seats_total=capacity,
            seats_available=capacity - flight.booked_count,
        )
        for flight in flights
    ]


def aisle_index() -> int:
    """Where the aisle falls in CABIN_COLUMNS -- 'ABCDEF' splits 3 | 3.

    Derived from the layout constant rather than hardcoded, so the same value
    defines both this split and the window/aisle/middle sets in the assistant.
    """
    return len(settings.CABIN_COLUMNS) // 2


def seat_map(flight: Flight) -> Cabin:
    """Every cabin position for `flight`, marked taken or free.

    Two queries: the seat catalog, and the seat ids booked on this flight --
    not one per seat. Occupancy is derived from `Booking`; there is no stored
    flag that could go stale.
    """
    taken_ids = set(Booking.objects.filter(flight=flight).values_list('seat_id', flat=True))
    split = aisle_index()

    grouped: dict[int, list[SeatCell]] = {}
    for seat in Seat.objects.all():
        grouped.setdefault(seat.row, []).append(SeatCell(seat=seat, is_taken=seat.id in taken_ids))

    rows = [
        CabinRow(number=number, left=cells[:split], right=cells[split:])
        for number, cells in sorted(grouped.items())
    ]
    return Cabin(rows=rows)
