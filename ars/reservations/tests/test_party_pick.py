from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from reservations import selectors
from reservations.exceptions import NotEnoughSeatsError, PartyTooLargeError
from reservations.models import Booking, Flight, Passenger, Seat


class PickPartySeatsTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.passenger = Passenger.objects.create(full_name='Ada Lovelace')

    def occupy(self, *designations: str) -> None:
        """Shape the cabin. Bulk-created: these are fixtures, not bookings."""
        wanted = {d.upper() for d in designations}
        seats = [seat for seat in Seat.objects.all() if seat.designation in wanted]
        Booking.objects.bulk_create(
            [Booking(flight=self.flight, seat=seat, passenger=self.passenger) for seat in seats]
        )

    def occupy_columns(self, columns: str) -> None:
        """Take the same columns in every row, to cap how many any row can hold."""
        self.occupy(*[f'{row}{c}' for row in range(1, 31) for c in columns])

    def pick(self, size: int, keep: tuple[str, ...] = ()) -> selectors.PartyPick:
        return selectors.pick_party_seats(self.flight, size, keep)

    # --- tier 1: one block ------------------------------------------------

    def test_a_party_of_three_takes_the_front_row_block(self) -> None:
        result = self.pick(3)

        self.assertEqual(result.designations, ['1A', '1B', '1C'])
        self.assertEqual(result.tier, 'block')
        self.assertTrue(result.is_together)

    def test_prefers_an_exact_fit_over_breaking_up_a_longer_run(self) -> None:
        # Row 2's left side has exactly two free seats; row 1 is wide open.
        self.occupy('2C')

        result = self.pick(2)

        # Taking 1A+1B would spend a run of three on a party of two and leave
        # nowhere for the next party of three.
        self.assertEqual(result.designations, ['2A', '2B'])
        self.assertEqual(result.tier, 'block')

    # --- tier 2: one row, across the aisle --------------------------------

    def test_a_party_of_four_sits_in_one_row_across_the_aisle(self) -> None:
        # No run of four exists in a 3+3 cabin, so this is the common case.
        result = self.pick(4)

        self.assertEqual(result.designations, ['1A', '1B', '1C', '1D'])
        self.assertEqual(result.tier, 'row')
        self.assertTrue(result.is_together)

    def test_a_full_row_is_still_one_row(self) -> None:
        result = self.pick(6)

        self.assertEqual(result.designations, ['1A', '1B', '1C', '1D', '1E', '1F'])
        self.assertEqual(result.tier, 'row')

    # --- tier 3: adjacent rows --------------------------------------------

    def test_falls_back_to_the_row_behind(self) -> None:
        # Every row down to three free seats, so no single row can hold four.
        self.occupy_columns('DEF')

        result = self.pick(4)

        self.assertEqual(result.tier, 'adjacent-rows')
        self.assertEqual({s.row for s in result.seats}, {1, 2})
        self.assertEqual(result.designations, ['1A', '1B', '1C', '2A'])
        self.assertFalse(result.is_together)

    # --- tier 4: scattered ------------------------------------------------

    def test_a_fragmented_cabin_gives_the_fewest_groups_it_can(self) -> None:
        # One free seat per row, rows 1..30: nothing can be adjacent.
        self.occupy_columns('ABCDE')

        result = self.pick(3)

        self.assertEqual(result.designations, ['1F', '2F', '3F'])
        self.assertEqual(result.tier, 'scattered')
        self.assertFalse(result.is_together)

    # --- keeping hand-picked seats ----------------------------------------

    def test_keeps_a_hand_picked_seat_and_extends_beside_it(self) -> None:
        result = self.pick(3, keep=('12B',))

        self.assertIn('12B', result.designations)
        self.assertEqual(result.designations, ['12A', '12B', '12C'])
        self.assertEqual(result.tier, 'block')

    def test_extends_across_the_aisle_before_reaching_another_row(self) -> None:
        result = self.pick(4, keep=('12B',))

        self.assertEqual({s.row for s in result.seats}, {12})
        self.assertEqual(result.tier, 'row')

    def test_a_kept_seat_taken_in_the_meantime_is_replaced(self) -> None:
        self.occupy('12B')

        result = self.pick(2, keep=('12B', '12C'))

        self.assertNotIn('12B', result.designations)
        self.assertIn('12C', result.designations)
        self.assertEqual(len(result.seats), 2)

    def test_case_and_whitespace_in_kept_designations(self) -> None:
        result = self.pick(2, keep=('12b',))
        self.assertIn('12B', result.designations)

    # --- refusals ---------------------------------------------------------

    def test_a_party_larger_than_the_cabin_allows(self) -> None:
        with self.assertRaises(PartyTooLargeError):
            self.pick(7)

    def test_a_party_of_zero_is_not_a_party(self) -> None:
        with self.assertRaises(PartyTooLargeError):
            self.pick(0)

    def test_more_passengers_than_seats_left(self) -> None:
        free = ['30E', '30F']
        taken = [
            seat.designation
            for seat in Seat.objects.all()
            if seat.designation not in free
        ]
        self.occupy(*taken)

        with self.assertRaises(NotEnoughSeatsError) as caught:
            self.pick(3)

        self.assertEqual(caught.exception.available, 2)
        self.assertIn('Only 2 seats left', str(caught.exception))

    def test_takes_the_last_seats_when_they_exactly_fit(self) -> None:
        free = ['30E', '30F']
        taken = [
            seat.designation
            for seat in Seat.objects.all()
            if seat.designation not in free
        ]
        self.occupy(*taken)

        self.assertEqual(self.pick(2).designations, free)

    # --- cost -------------------------------------------------------------

    def test_picking_is_two_queries_whatever_the_party_size(self) -> None:
        with self.assertNumQueries(2):
            selectors.pick_party_seats(self.flight, 6)
