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
