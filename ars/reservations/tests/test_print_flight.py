from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from reservations import services
from reservations.models import Flight

# What follows a row label: the 3-space gap, then 3 + aisle + 3 free seats.
EMPTY_ROW = '   .  .  .   .  .  .'


def run(*args: str) -> list[str]:
    out = StringIO()
    call_command('print_flight', *args, stdout=out)
    return out.getvalue().splitlines()


class PrintFlightTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
        )

    def book(self, *designations: str) -> None:
        services.book_seats(
            self.flight,
            [services.SeatRequest(d, f'Passenger {d}') for d in designations],
        )

    def test_renders_the_cabin_exactly(self) -> None:
        self.book('1C', '2A', '2B')
        lines = run('PR101')

        # The format is the deliverable, so it is what gets asserted.
        self.assertEqual(lines[0], 'PR101  MNL → CEB  2026-08-14 09:30')
        self.assertEqual(lines[1], '')
        self.assertEqual(lines[2], '      A  B  C   D  E  F')
        self.assertEqual(lines[3], '  1   .  .  X   .  .  .')
        self.assertEqual(lines[4], '  2   X  X  .   .  .  .')
        self.assertEqual(lines[5], '  3   .  .  .   .  .  .')
        self.assertEqual(lines[-3], '')
        self.assertEqual(lines[-2], '  . = available    X = taken')
        self.assertEqual(lines[-1], '  177 of 180 available')

    def test_every_row_is_printed_and_aligned(self) -> None:
        lines = run('PR101')

        rows = lines[3:-3]
        self.assertEqual(len(rows), 30)
        # Row 30's label is wider than row 1's; the seats must still line up.
        self.assertEqual(rows[0], '  1' + EMPTY_ROW)
        self.assertEqual(rows[-1], ' 30' + EMPTY_ROW)
        self.assertEqual(len({len(row) for row in rows}), 1)

    def test_the_count_agrees_with_what_is_drawn(self) -> None:
        self.book('5A', '5B')
        lines = run('PR101')

        drawn_free = sum(line.count('.') for line in lines[3:-3])
        self.assertEqual(drawn_free, 178)
        self.assertEqual(lines[-1], '  178 of 180 available')

    def test_the_number_is_matched_case_insensitively(self) -> None:
        self.assertEqual(run('pr101')[0], run('PR101')[0])

    def test_available_only_lists_designations_and_nothing_else(self) -> None:
        self.book('1A', '1B')
        lines = run('PR101', '--available-only')

        self.assertEqual(len(lines), 178)
        self.assertNotIn('1A', lines)
        self.assertNotIn('1B', lines)
        self.assertEqual(lines[0], '1C')
        self.assertEqual(lines[-1], '30F')

    def test_an_unknown_flight_names_the_ones_that_exist(self) -> None:
        with self.assertRaises(CommandError) as caught:
            run('XX999')

        message = str(caught.exception)
        self.assertIn("'XX999'", message)
        self.assertIn('PR101', message)

    def test_an_empty_database_points_at_the_seed_command(self) -> None:
        Flight.objects.all().delete()

        with self.assertRaises(CommandError) as caught:
            run('PR101')

        self.assertIn('seed_flights', str(caught.exception))

    def test_no_query_per_row(self) -> None:
        # One lookup for the flight, then the two the cabin costs -- never one
        # per row, which would be invisible in the output.
        with self.assertNumQueries(3):
            run('PR101')
