from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservations import services
from reservations.models import Booking, Flight


class BookSeatViewTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.url = reverse('book-seat', args=[self.flight.id])

    def test_books_every_seat_in_the_party(self) -> None:
        response = self.client.post(
            self.url,
            {'seat': ['12C', '12D'], 'passenger': ['Ada Lovelace', 'Alan Turing']},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.filter(flight=self.flight).count(), 2)
        self.assertContains(response, 'Booked 12C, 12D.')

    def test_returns_the_refreshed_map_and_an_oob_banner(self) -> None:
        response = self.client.post(self.url, {'seat': ['1A'], 'passenger': ['Ada Lovelace']})

        # The map fragment, not the whole page -- htmx swaps this into #seat-map.
        self.assertContains(response, 'id="seat-map"')
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'hx-swap-oob="true"')
        # The booked seat comes back disabled, so the cabin self-heals.
        self.assertContains(response, 'Seat 1A, taken')

    def test_the_header_count_is_refreshed_out_of_band(self) -> None:
        # It lives outside the swapped map, so without this it would keep
        # claiming the flight is empty after a booking.
        response = self.client.post(self.url, {'seat': ['1A'], 'passenger': ['Ada Lovelace']})

        self.assertContains(response, 'id="seats-remaining"')
        self.assertContains(response, '179 of 180 free')

    def test_a_lost_race_is_a_200_with_an_error_banner(self) -> None:
        services.book_seats(self.flight, [services.SeatRequest('1A', 'Ada Lovelace')])

        response = self.client.post(self.url, {'seat': ['1A'], 'passenger': ['Alan Turing']})

        # Not a 4xx: htmx does not swap error responses, so the passenger would
        # be left staring at an unchanged page.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seat 1A was just taken')
        self.assertContains(response, 'data-status="error"')
        self.assertEqual(Booking.objects.filter(flight=self.flight).count(), 1)

    def test_a_seat_outside_the_cabin_is_reported_precisely(self) -> None:
        response = self.client.post(self.url, {'seat': ['45A'], 'passenger': ['Ada Lovelace']})

        self.assertContains(response, 'There is no seat 45A')
        self.assertEqual(Booking.objects.count(), 0)

    def test_a_malformed_designation_is_reported_differently(self) -> None:
        response = self.client.post(self.url, {'seat': ['99ZZ'], 'passenger': ['Ada Lovelace']})

        self.assertContains(response, 'is not a seat designation')
        self.assertEqual(Booking.objects.count(), 0)

    def test_mismatched_seats_and_names_book_nothing(self) -> None:
        response = self.client.post(
            self.url, {'seat': ['1A', '1B'], 'passenger': ['Ada Lovelace']}
        )

        self.assertContains(response, 'Every seat needs a passenger name.')
        self.assertEqual(Booking.objects.count(), 0)

    def test_submitting_without_a_seat_is_refused(self) -> None:
        response = self.client.post(self.url, {})

        self.assertContains(response, 'Pick at least one seat first.')
        self.assertEqual(Booking.objects.count(), 0)

    def test_get_is_not_allowed(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 405)


class BookFirstViewTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.url = reverse('book-first', args=[self.flight.id])

    def test_assigns_the_lowest_free_seat(self) -> None:
        response = self.client.post(self.url, {'passenger': 'Ada Lovelace'})

        self.assertContains(response, 'Booked 1A.')
        self.assertEqual(Booking.objects.get().seat.designation, '1A')

    def test_skips_over_seats_already_taken(self) -> None:
        services.book_seats(self.flight, [services.SeatRequest('1A', 'Ada Lovelace')])

        self.client.post(self.url, {'passenger': 'Alan Turing'})

        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(Booking.objects.order_by('-id').first().seat.designation, '1B')

    def test_a_missing_name_is_refused(self) -> None:
        response = self.client.post(self.url, {'passenger': ''})

        self.assertContains(response, 'Every seat needs a passenger name.')
        self.assertEqual(Booking.objects.count(), 0)
