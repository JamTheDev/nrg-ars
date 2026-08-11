from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservations.models import Booking, Flight, Passenger, Seat


def pressed_seats(response) -> list[str]:
    """Designations the server rendered as already selected."""
    # [^>]* cannot cross the tag boundary, so this only pairs a designation
    # with an aria-pressed on the same button.
    return re.findall(
        r'data-seat="([^"]+)"[^>]*aria-pressed="true"', response.content.decode()
    )


class SeatMapViewTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.url = reverse('seat-map', args=[self.flight.id])
        self.filler = Passenger.objects.create(full_name='Filler')

    def occupy(self, designations: Iterable[str]) -> None:
        """Shape the cabin. Bulk-created: these are fixtures, not bookings, and
        booking them through services would hit the party cap."""
        wanted = {d.upper() for d in designations}
        Booking.objects.bulk_create(
            [
                Booking(flight=self.flight, seat=seat, passenger=self.filler)
                for seat in Seat.objects.all()
                if seat.designation in wanted
            ]
        )

    def test_without_a_party_nothing_is_selected(self) -> None:
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="seat-map"')
        self.assertNotContains(response, '<html')
        self.assertEqual(pressed_seats(response), [])

    def test_a_party_comes_back_seated_together(self) -> None:
        response = self.client.get(self.url, {'party': 3})

        self.assertEqual(pressed_seats(response), ['1A', '1B', '1C'])
        # Seated together needs no explanation.
        self.assertNotContains(response, 'as close together')

    def test_a_split_party_says_so(self) -> None:
        # One seat free per row: four passengers cannot sit together.
        self.occupy(f'{row}{column}' for row in range(1, 31) for column in 'ABCDE')

        response = self.client.get(self.url, {'party': 4})

        self.assertEqual(pressed_seats(response), ['1F', '2F', '3F', '4F'])
        self.assertContains(response, 'as close together as the cabin allows')

    def test_hand_picked_seats_are_kept_when_the_party_grows(self) -> None:
        response = self.client.get(self.url, {'party': 3, 'keep': '12B,12C'})

        seats = pressed_seats(response)
        self.assertIn('12B', seats)
        self.assertIn('12C', seats)
        self.assertEqual(len(seats), 3)

    def test_keep_alone_just_re_renders_the_selection(self) -> None:
        response = self.client.get(self.url, {'keep': '4D,4E'})

        self.assertEqual(pressed_seats(response), ['4D', '4E'])

    def test_a_party_beyond_the_limit_is_refused_and_keeps_the_selection(self) -> None:
        response = self.client.get(self.url, {'party': 7, 'keep': '1A'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'limited to 6 passengers')
        self.assertContains(response, 'data-status="error"')
        # The refusal must not throw away what was already chosen.
        self.assertEqual(pressed_seats(response), ['1A'])

    def test_a_party_larger_than_the_cabin_has_left(self) -> None:
        self.occupy([f'{row}{c}' for row in range(1, 31) for c in 'ABCDEF'][:178])

        response = self.client.get(self.url, {'party': 3})

        self.assertContains(response, 'Only 2 seats left')
        self.assertEqual(pressed_seats(response), [])

    def test_a_party_that_is_not_a_number(self) -> None:
        response = self.client.get(self.url, {'party': 'three'})

        self.assertContains(response, 'not a number of passengers')

    def test_the_header_count_rides_along(self) -> None:
        response = self.client.get(self.url, {'party': 2})
        self.assertContains(response, 'id="seats-remaining"')

    def test_post_is_not_allowed(self) -> None:
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_unknown_flight_is_404(self) -> None:
        response = self.client.get(reverse('seat-map', args=[9999]), {'party': 2})
        self.assertEqual(response.status_code, 404)
