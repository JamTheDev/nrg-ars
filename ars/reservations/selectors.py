"""Read-side queries. No HTTP, no writes -- see ARCHITECTURE.md section 8."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from reservations.exceptions import NotEnoughSeatsError, PartyTooLargeError
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
    """One seat as the cabin view needs it: the position plus its occupancy.

    `is_selected` is what the server renders as pressed. The client adopts it
    wholesale after every swap, so a booking response (nothing selected) clears
    the selection and a party pick (N selected) installs one, with no flag
    distinguishing the two.
    """

    seat: Seat
    is_taken: bool
    is_selected: bool = False

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
    # Order explicitly. Meta.ordering is ignored once a query groups for an
    # aggregate, so the annotate() below silently drops it -- the board looked
    # chronological only because the demo flights were seeded in that order.
    flights = Flight.objects.annotate(booked_count=Count('bookings')).order_by(
        'departs_at', 'number'
    )
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


def seat_map(flight: Flight, selected: Iterable[str] = ()) -> Cabin:
    """Every cabin position for `flight`, marked taken or free.

    Two queries: the seat catalog, and the seat ids booked on this flight --
    not one per seat. Occupancy is derived from `Booking`; there is no stored
    flag that could go stale.

    `selected` names the designations to render as pressed.
    """
    taken_ids = set(Booking.objects.filter(flight=flight).values_list('seat_id', flat=True))
    chosen = {designation.upper() for designation in selected}
    split = aisle_index()

    grouped: dict[int, list[SeatCell]] = {}
    for seat in Seat.objects.all():
        is_taken = seat.id in taken_ids
        grouped.setdefault(seat.row, []).append(
            SeatCell(
                seat=seat,
                is_taken=is_taken,
                is_selected=not is_taken and seat.designation in chosen,
            )
        )

    rows = [
        CabinRow(number=number, left=cells[:split], right=cells[split:])
        for number, cells in sorted(grouped.items())
    ]
    return Cabin(rows=rows)


# --- Party seating -------------------------------------------------------
#
# A 3 + 3 cabin means the longest unbroken run of seats is three, so a party of
# four can never be one block. "Together" is therefore a ladder, and the same
# row across the aisle counts -- to a family it plainly is. See
# docs/plans/06-party-size-auto-select.md.

Tier = Literal['block', 'row', 'adjacent-rows', 'scattered']


@dataclass(frozen=True)
class PartyPick:
    """Seats chosen for one party, and how well they ended up seated."""

    seats: list[Seat]
    tier: Tier

    @property
    def designations(self) -> list[str]:
        return [seat.designation for seat in self.seats]

    @property
    def is_together(self) -> bool:
        return self.tier in ('block', 'row')


def _blocks(cabin: Cabin) -> list[list[SeatCell]]:
    """Maximal runs of free seats within one side of one row, front to back.

    The aisle breaks a run: 12C and 12D are neighbours on the map but not to
    anyone trying to hold a conversation.
    """
    found = []
    for row in cabin.rows:
        for side in (row.left, row.right):
            run: list[SeatCell] = []
            for cell in side:
                if cell.is_taken:
                    if run:
                        found.append(run)
                    run = []
                else:
                    run.append(cell)
            if run:
                found.append(run)
    return found


def _cabin_order(cells: Iterable[SeatCell]) -> list[SeatCell]:
    return sorted(cells, key=lambda cell: (cell.seat.row, cell.seat.column))


def _take_from(blocks: list[list[SeatCell]], size: int) -> list[SeatCell]:
    """Fill `size` seats using as few separate groups as possible."""
    chosen: list[SeatCell] = []
    for block in sorted(blocks, key=lambda b: (-len(b), b[0].seat.row, b[0].seat.column)):
        if len(chosen) >= size:
            break
        chosen.extend(block[: size - len(chosen)])
    return chosen


def _classify(cells: Sequence[SeatCell]) -> Tier:
    """How well the party ended up seated, judged from the seats themselves."""
    rows = {cell.seat.row for cell in cells}
    columns = settings.CABIN_COLUMNS
    indexes = sorted(columns.index(cell.seat.column) for cell in cells)
    split = aisle_index()

    if len(rows) == 1:
        same_side = all(i < split for i in indexes) or all(i >= split for i in indexes)
        contiguous = indexes == list(range(indexes[0], indexes[0] + len(indexes)))
        return 'block' if same_side and contiguous else 'row'
    if len(rows) == 2 and max(rows) - min(rows) == 1:
        return 'adjacent-rows'
    return 'scattered'


def _nearest_to(kept: Sequence[SeatCell], free: Sequence[SeatCell], count: int) -> list[SeatCell]:
    """The `count` free seats closest to seats the passenger already chose.

    Distance is rows first, then whether it is the same side of the aisle, then
    columns -- 12C is nearer to 12B than 14B is, and a seat across the aisle in
    the same row beats one two rows back.
    """
    columns = settings.CABIN_COLUMNS
    split = aisle_index()

    def distance(cell: SeatCell) -> tuple[int, int, int, int, str]:
        index = columns.index(cell.seat.column)
        side = index >= split
        best = min(
            (
                abs(cell.seat.row - anchor.seat.row),
                int(side != (columns.index(anchor.seat.column) >= split)),
                abs(index - columns.index(anchor.seat.column)),
            )
            for anchor in kept
        )
        return (*best, cell.seat.row, cell.seat.column)

    return sorted(free, key=distance)[:count]


def pick_party_seats(
    flight: Flight, size: int, keep: Iterable[str] = ()
) -> PartyPick:
    """Choose `size` free seats for one party, seated together where possible.

    `keep` holds designations already chosen by hand; they are kept and the
    remainder picked as close to them as the cabin allows. Designations in
    `keep` that have since been booked by someone else are dropped -- the party
    lost that seat and needs another.

    Two queries, whatever the party size: this reuses seat_map().
    """
    maximum = settings.MAX_PARTY_SIZE
    if size < 1 or size > maximum:
        raise PartyTooLargeError(size, maximum)

    cabin = seat_map(flight)
    free = [cell for row in cabin.rows for cell in row.cells if not cell.is_taken]
    if len(free) < size:
        raise NotEnoughSeatsError(size, len(free))

    wanted = {designation.upper() for designation in keep}
    kept = [cell for cell in free if cell.designation in wanted][:size]

    if kept:
        remaining = size - len(kept)
        pool = [cell for cell in free if cell not in kept]
        chosen = kept + _nearest_to(kept, pool, remaining)
    else:
        chosen = _pick_fresh(cabin, free, size)

    ordered = _cabin_order(chosen)
    return PartyPick(seats=[cell.seat for cell in ordered], tier=_classify(ordered))


def position_columns(position: str) -> set[str]:
    """Which columns count as window, aisle or middle.

    Computed from CABIN_COLUMNS so a layout change cannot leave a stored flag
    stale -- see ARCHITECTURE.md section 7.
    """
    columns = settings.CABIN_COLUMNS
    split = aisle_index()
    window = {columns[0], columns[-1]}
    aisle = {columns[split - 1], columns[split]}
    return {
        'window': window,
        'aisle': aisle,
        'middle': set(columns) - window - aisle,
    }[position]


def side_columns(side: str) -> set[str]:
    """Which columns are on the left or the right of the aisle.

    Facing forward: A-C on the left, D-F on the right. Derived from the layout
    for the same reason the position sets are.
    """
    columns = settings.CABIN_COLUMNS
    split = aisle_index()
    return {'left': set(columns[:split]), 'right': set(columns[split:])}[side]


def _matches(cell: SeatCell, query, columns, side) -> bool:
    return (
        (columns is None or cell.seat.column in columns)
        and (side is None or cell.seat.column in side)
        and (query.min_row is None or cell.seat.row >= query.min_row)
        and (query.max_row is None or cell.seat.row <= query.max_row)
    )


@dataclass(frozen=True)
class SeatCounts:
    """How many seats a filter describes, and how many of those are free.

    Answering "how many window seats are there?" needs both numbers: the
    cabin's shape and today's availability are different questions, and a bot
    that conflates them is wrong twice a day.
    """

    matching: int
    free: int

    @property
    def taken(self) -> int:
        return self.matching - self.free


def count_seats(flight: Flight, query) -> SeatCounts:
    """Count the seats a SeatQuery describes. Two queries, like everything else."""
    cabin = seat_map(flight)
    columns = position_columns(query.position) if query.position else None
    side = side_columns(query.side) if query.side else None

    cells = [
        cell
        for row in cabin.rows
        for cell in row.cells
        if _matches(cell, query, columns, side)
    ]
    return SeatCounts(
        matching=len(cells),
        free=sum(1 for cell in cells if not cell.is_taken),
    )


def search_seats(
    flight: Flight, query, limit: int | None = None, rng: random.Random | None = None
) -> list[Seat]:
    """Free seats on `flight` matching a validated SeatQuery, in cabin order.

    The filter arrives already clamped by the assistant; this only applies it.
    Two queries, via seat_map().
    """
    cabin = seat_map(flight)
    columns = position_columns(query.position) if query.position else None
    side = side_columns(query.side) if query.side else None

    matches = [
        cell
        for row in cabin.rows
        for cell in row.cells
        if not cell.is_taken and _matches(cell, query, columns, side)
    ]

    # A group asked to sit together is asked for one thing, not `limit` things
    # that each satisfy the filter. Prefer a single row that can hold them all;
    # if none can, fall through and offer the closest the filter allows.
    if query.together and limit and limit > 1:
        by_row: dict[int, list[SeatCell]] = {}
        for cell in matches:
            by_row.setdefault(cell.seat.row, []).append(cell)
        for number in sorted(by_row):
            if len(by_row[number]) >= limit:
                return [cell.seat for cell in by_row[number][:limit]]

    # Bounds say which seats qualify; these say which of them to offer first.
    if query.is_random:
        # "a random seat at the back" is a random seat *at the back*. Shuffling
        # the whole cabin answers only half the sentence, so when an end is
        # named the draw is made from that third of it.
        if query.toward:
            third = max(1, settings.CABIN_ROWS // 3)
            if query.toward == 'back':
                in_band = [c for c in matches if c.seat.row > settings.CABIN_ROWS - third]
            else:
                in_band = [c for c in matches if c.seat.row <= third]
            # Fall back to the wider set rather than refusing: a full rear
            # third still means "somewhere at the back" to the passenger.
            matches = in_band or matches
        (rng or random.Random()).shuffle(matches)
    elif query.toward == 'back':
        # Without this, "as far back as possible" returns the front-most seat
        # of the back section -- filter right, answer backwards.
        matches.sort(key=lambda cell: (-cell.seat.row, cell.seat.column))

    seats = [cell.seat for cell in matches]
    return seats[:limit] if limit else seats


def _pick_fresh(cabin: Cabin, free: Sequence[SeatCell], size: int) -> list[SeatCell]:
    """Walk the preference ladder: one block, one row, two rows, anything."""
    blocks = _blocks(cabin)

    # 1. A single run. Take the smallest that fits, so a party of two does not
    #    consume the only run of three left on the aircraft.
    fitting = [block for block in blocks if len(block) >= size]
    if fitting:
        block = min(fitting, key=lambda b: (len(b), b[0].seat.row, b[0].seat.column))
        return block[:size]

    by_row: dict[int, list[SeatCell]] = {}
    for cell in free:
        by_row.setdefault(cell.seat.row, []).append(cell)

    # 2. One row, across the aisle.
    for number in sorted(by_row):
        if len(by_row[number]) >= size:
            return _take_from([b for b in blocks if b[0].seat.row == number], size)

    # 3. Two rows, back to back.
    for number in sorted(by_row):
        pair = by_row.get(number, []) + by_row.get(number + 1, [])
        if len(pair) >= size:
            return _take_from(
                [b for b in blocks if b[0].seat.row in (number, number + 1)], size
            )

    # 4. Wherever they fit, in as few groups as possible.
    return _take_from(blocks, size)
