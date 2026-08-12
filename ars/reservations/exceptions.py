"""Booking failures, one class per distinct cause.

Each carries the sentence the passenger should see. Collapsing these into a
single "invalid seat" error makes the UI meaningfully worse -- see
ARCHITECTURE.md section 4.
"""

from __future__ import annotations


class BookingError(Exception):
    """Base for every booking failure. Views translate these into a banner."""


class InvalidSeatError(BookingError):
    """The string is not a seat designation at all."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        super().__init__(f'{raw!r} is not a seat designation like "12C".')


class SeatNotFoundError(BookingError):
    """Well-formed, but no such seat in this cabin."""

    def __init__(self, designation: str, rows: int, columns: str) -> None:
        self.designation = designation
        super().__init__(
            f'There is no seat {designation}: this aircraft has rows 1-{rows}, '
            f'columns {columns[0]}-{columns[-1]}.'
        )


class SeatTakenError(BookingError):
    """Lost the race for this seat. See ARCHITECTURE.md section 5."""

    def __init__(self, designation: str) -> None:
        self.designation = designation
        super().__init__(f'Seat {designation} was just taken. Please pick another.')


class FlightFullError(BookingError):
    """No seat left to auto-assign."""

    def __init__(self, number: str) -> None:
        self.number = number
        super().__init__(f'Flight {number} is full.')


class CouldNotAssignError(BookingError):
    """Auto-assignment kept losing races and gave up."""

    def __init__(self, number: str) -> None:
        self.number = number
        super().__init__(
            f'Could not assign a seat on {number} just now -- the cabin is filling up. '
            'Please try again.'
        )


class InvalidPassengerError(BookingError):
    """A booking needs a name to put on it."""

    def __init__(self) -> None:
        super().__init__('Every seat needs a passenger name.')


class NothingSelectedError(BookingError):
    """Submitted without choosing a seat."""

    def __init__(self) -> None:
        super().__init__('Pick at least one seat first.')


class PartyTooLargeError(BookingError):
    """More passengers than one booking will take."""

    def __init__(self, size: int, maximum: int) -> None:
        self.size = size
        super().__init__(f'Parties are limited to {maximum} passengers at a time.')


class NotEnoughSeatsError(BookingError):
    """The party is larger than what is left on the flight."""

    def __init__(self, size: int, available: int) -> None:
        self.size = size
        self.available = available
        seats = 'seat' if available == 1 else 'seats'
        super().__init__(
            f'Only {available} {seats} left on this flight, and you asked for {size}.'
        )
