from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

from assistant.providers import ProviderUnavailable
from assistant.schema import SeatQuery
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservations import selectors, services
from reservations.models import Flight


def pressed_seats(response) -> list[str]:
    return re.findall(
        r'data-seat="([^"]+)"[^>]*aria-pressed="true"', response.content.decode()
    )


class SearchSeatsTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )

    def search(self, **kwargs) -> list[str]:
        seats = selectors.search_seats(self.flight, SeatQuery(**kwargs))
        return [seat.designation for seat in seats]

    def test_positions_are_derived_from_the_layout(self) -> None:
        self.assertEqual(selectors.position_columns('window'), {'A', 'F'})
        self.assertEqual(selectors.position_columns('aisle'), {'C', 'D'})
        self.assertEqual(selectors.position_columns('middle'), {'B', 'E'})

    def test_a_window_seat_near_the_front(self) -> None:
        self.assertEqual(self.search(position='window', max_row=2), ['1A', '1F', '2A', '2F'])

    def test_a_row_range_is_inclusive(self) -> None:
        rows = {int(d[:-1]) for d in self.search(min_row=5, max_row=7)}
        self.assertEqual(rows, {5, 6, 7})

    def test_taken_seats_never_match(self) -> None:
        services.book_seats(self.flight, [services.SeatRequest('1A', 'Ada Lovelace')])
        self.assertNotIn('1A', self.search(position='window', max_row=2))

    def test_an_empty_query_matches_every_free_seat(self) -> None:
        self.assertEqual(len(self.search()), 180)

    def test_toward_back_offers_the_rearmost_seats_first(self) -> None:
        # "as far back as possible" must not return the front of the back
        # section, which is what plain cabin order gives.
        self.assertEqual(self.search(position='window', toward='back')[:2], ['30A', '30F'])

    def test_toward_back_still_respects_the_bounds(self) -> None:
        seats = self.search(position='window', min_row=10, max_row=20, toward='back')
        self.assertEqual(seats[:2], ['20A', '20F'])

    def test_toward_front_is_ordinary_cabin_order(self) -> None:
        self.assertEqual(self.search(position='window', toward='front')[:2], ['1A', '1F'])

    def test_a_side_narrows_the_aisle_to_one_of_its_two_columns(self) -> None:
        self.assertEqual(selectors.side_columns('left'), {'A', 'B', 'C'})
        self.assertEqual(selectors.side_columns('right'), {'D', 'E', 'F'})
        # "aisle" alone spans both sides; with a side it is one column.
        self.assertEqual(self.search(position='aisle', side='right')[:2], ['1D', '2D'])
        self.assertEqual(self.search(position='aisle', side='left')[:2], ['1C', '2C'])

    def test_side_and_direction_compose(self) -> None:
        seats = self.search(position='aisle', side='right', toward='back')
        self.assertEqual(seats[0], '30D')

    def test_random_shuffles_within_the_matches(self) -> None:
        import random as random_module

        query = SeatQuery(position='window', min_row=1, max_row=5, is_random=True)
        picked = selectors.search_seats(
            self.flight, query, limit=3, rng=random_module.Random(7)
        )
        designations = [seat.designation for seat in picked]

        # Still only matching seats, but not simply the first three.
        self.assertTrue(all(d[-1] in 'AF' and int(d[:-1]) <= 5 for d in designations))
        self.assertNotEqual(designations, ['1A', '1F', '2A'])

    def test_random_at_an_end_draws_only_from_that_end(self) -> None:
        import random as random_module

        # "a random seat at the back" is a random seat *at the back*.
        query = SeatQuery(toward='back', is_random=True)
        picked = selectors.search_seats(
            self.flight, query, limit=8, rng=random_module.Random(3)
        )
        rows = [seat.row for seat in picked]

        self.assertTrue(all(row > 20 for row in rows), rows)
        self.assertGreater(len(set(rows)), 1)  # genuinely spread, not just row 30

    def test_random_toward_the_front_draws_from_the_front(self) -> None:
        import random as random_module

        picked = selectors.search_seats(
            self.flight,
            SeatQuery(toward='front', is_random=True),
            limit=8,
            rng=random_module.Random(3),
        )
        self.assertTrue(all(seat.row <= 10 for seat in picked))

    def test_random_is_reproducible_for_a_given_seed(self) -> None:
        import random as random_module

        query = SeatQuery(is_random=True)
        first = selectors.search_seats(self.flight, query, limit=4, rng=random_module.Random(1))
        second = selectors.search_seats(self.flight, query, limit=4, rng=random_module.Random(1))
        self.assertEqual([s.designation for s in first], [s.designation for s in second])

    def test_searching_is_two_queries(self) -> None:
        with self.assertNumQueries(2):
            selectors.search_seats(self.flight, SeatQuery(position='window'))


class SearchViewTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.url = reverse('seat-map', args=[self.flight.id])

    def extraction_returns(self, query: SeatQuery):
        return mock.patch('reservations.views.extraction.extract', return_value=query)

    def test_a_match_comes_back_selected_with_what_was_understood(self) -> None:
        with self.extraction_returns(SeatQuery(position='window', max_row=10)):
            response = self.client.get(self.url, {'q': 'window seat near the front'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pressed_seats(response), ['1A'])
        self.assertContains(response, 'Window seats up to row 10.')

    def test_a_party_selects_that_many_matches(self) -> None:
        with self.extraction_returns(SeatQuery(position='aisle')):
            response = self.client.get(self.url, {'q': 'aisle seats', 'party': 3})

        self.assertEqual(pressed_seats(response), ['1C', '1D', '2C'])

    def test_the_party_cap_still_applies(self) -> None:
        with self.extraction_returns(SeatQuery()):
            response = self.client.get(self.url, {'q': 'anything', 'party': 99})

        self.assertEqual(len(pressed_seats(response)), 6)

    def test_ollama_being_down_leaves_the_map_and_the_selection_alone(self) -> None:
        # The core requirements must never depend on a model being up.
        with mock.patch(
            'reservations.views.extraction.extract', side_effect=ProviderUnavailable('down')
        ):
            response = self.client.get(self.url, {'q': 'window seat', 'keep': '4D,4E'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Smart search is unavailable')
        self.assertContains(response, 'data-status="error"')
        self.assertEqual(pressed_seats(response), ['4D', '4E'])

    def test_no_match_says_so_and_keeps_the_selection(self) -> None:
        services.book_seats(
            self.flight,
            [services.SeatRequest(f'1{c}', f'P{c}') for c in 'ABCDEF'],
        )
        with self.extraction_returns(SeatQuery(position='window', max_row=1)):
            response = self.client.get(self.url, {'q': 'window at the very front', 'keep': '9B'})

        self.assertContains(response, 'No free window seats up to row 1')
        self.assertEqual(pressed_seats(response), ['9B'])

    def test_a_blank_query_is_not_a_search(self) -> None:
        with mock.patch('reservations.views.extraction.extract') as extract:
            response = self.client.get(self.url, {'q': '   '})

        extract.assert_not_called()
        self.assertEqual(pressed_seats(response), [])

    def test_prompt_injection_cannot_reach_the_database(self) -> None:
        # The model's only output is a validated SeatQuery, so the worst a
        # crafted phrase can do is describe a strange seat.
        with self.extraction_returns(SeatQuery(position='window')):
            response = self.client.get(
                self.url, {'q': "'; DROP TABLE reservations_booking; --"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Flight.objects.exists())
        services.book_seats(self.flight, [services.SeatRequest('2A', 'Still works')])
