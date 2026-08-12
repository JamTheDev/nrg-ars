"""Render a flight's cabin as text -- the literal "print" of core requirement 1.

The web seat map covers displaying available seating; a pannable, colour-coded
canvas is not a print. This is the other half, and it shares `seat_map()` with
the web view rather than recomputing availability, so the two renderings cannot
disagree about which seats are free.

    uv run ars/manage.py print_flight PR101
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reservations import selectors
from reservations.models import Flight

AVAILABLE = '.'
TAKEN = 'X'

# Row labels are right-aligned in this width, then a gap, so the column header
# and every row line share one prefix.
LABEL_WIDTH = 3
LABEL_GAP = '   '
SEAT_GAP = '  '
AISLE_GAP = '   '


def _line(label: str, left: list[str], right: list[str]) -> str:
    return (
        f'{label:>{LABEL_WIDTH}}{LABEL_GAP}'
        + SEAT_GAP.join(left)
        + AISLE_GAP
        + SEAT_GAP.join(right)
    )


class Command(BaseCommand):
    help = "Print a flight's seat map as text."

    def add_arguments(self, parser) -> None:
        parser.add_argument('number', help='Flight number, e.g. PR101')
        parser.add_argument(
            '--available-only',
            action='store_true',
            help='List free seat designations one per line, for piping.',
        )

    def handle(self, *args, **options) -> None:
        flight = self._find(options['number'])
        cabin = selectors.seat_map(flight)

        if not cabin.rows:
            raise CommandError(
                'The seat catalog is empty. Run: uv run ars/manage.py migrate'
            )

        if options['available_only']:
            for row in cabin.rows:
                for cell in row.cells:
                    if not cell.is_taken:
                        self.stdout.write(cell.designation)
            return

        for line in self._render(flight, cabin):
            self.stdout.write(line)

    def _find(self, number: str) -> Flight:
        try:
            return Flight.objects.get(number__iexact=number.strip())
        except Flight.DoesNotExist:
            # Only on the failure path, so the happy path stays at one lookup.
            known = list(Flight.objects.values_list('number', flat=True))
            if not known:
                raise CommandError(
                    'No flights exist. Run: uv run ars/manage.py seed_flights'
                ) from None
            raise CommandError(
                f'No flight numbered {number!r}. Known flights: {", ".join(known)}'
            ) from None

    def _render(self, flight: Flight, cabin: selectors.Cabin) -> list[str]:
        departs = timezone.localtime(flight.departs_at).strftime('%Y-%m-%d %H:%M')
        columns_left = list(cabin.columns_left)
        columns_right = list(cabin.columns_right)

        lines = [
            f'{flight.number}  {flight.origin} → {flight.destination}  {departs}',
            '',
            _line('', columns_left, columns_right),
        ]

        for row in cabin.rows:
            marks = [TAKEN if cell.is_taken else AVAILABLE for cell in row.cells]
            split = len(columns_left)
            lines.append(_line(str(row.number), marks[:split], marks[split:]))

        # The footer is prose, not seats, so it is indented plainly rather than
        # threaded through the seat layout.
        lines += [
            '',
            f'  {AVAILABLE} = available    {TAKEN} = taken',
            f'  {cabin.seats_available} of {cabin.seats_total} available',
        ]
        return lines
