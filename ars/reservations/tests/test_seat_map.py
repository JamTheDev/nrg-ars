from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservations import selectors
from reservations.models import Booking, Flight, Passenger, Seat


class SeatMapTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        self.other = Flight.objects.create(
            number='PR205',
            origin='MNL',
            destination='DVO',
            departs_at=timezone.now() + timedelta(days=1),
        )
        self.passenger = Passenger.objects.create(full_name='Ada Lovelace')

    def book(self, flight: Flight, designation: str) -> Booking:
        row, column = int(designation[:-1]), designation[-1]
        seat = Seat.objects.get(row=row, column=column)
        return Booking.objects.create(flight=flight, seat=seat, passenger=self.passenger)

    def test_cabin_covers_every_row_split_at_the_aisle(self) -> None:
        cabin = selectors.seat_map(self.flight)

        self.assertEqual(len(cabin.rows), 30)
        self.assertEqual([cell.designation for cell in cabin.rows[0].left], ['1A', '1B', '1C'])
        self.assertEqual([cell.designation for cell in cabin.rows[0].right], ['1D', '1E', '1F'])
        self.assertEqual(cabin.columns_left, 'ABC')
        self.assertEqual(cabin.columns_right, 'DEF')

    def test_occupancy_comes_from_this_flights_bookings_only(self) -> None:
        self.book(self.flight, '1A')
        self.book(self.other, '1B')

        cells = {cell.designation: cell for cell in selectors.seat_map(self.flight).rows[0].cells}
        self.assertTrue(cells['1A'].is_taken)
        self.assertFalse(cells['1B'].is_taken)

    def test_counts_are_derived_from_the_grid(self) -> None:
        self.book(self.flight, '12C')
        cabin = selectors.seat_map(self.flight)

        self.assertEqual(cabin.seats_total, 180)
        self.assertEqual(cabin.seats_taken, 1)
        self.assertEqual(cabin.seats_available, 179)

    def test_seat_map_is_two_queries_across_180_seats(self) -> None:
        # The catalog and the taken ids -- never one query per seat.
        with self.assertNumQueries(2):
            selectors.seat_map(self.flight)


class FlightDetailViewTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )

    def test_renders_the_cabin_and_the_reserve_bar(self) -> None:
        response = self.client.get(reverse('flight-detail', args=[self.flight.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-seat="1A"')
        self.assertContains(response, 'data-seat="30F"')
        self.assertContains(response, 'id="seat-map-viewport"')
        self.assertContains(response, 'Reserve for')

    def test_taken_seats_render_disabled(self) -> None:
        passenger = Passenger.objects.create(full_name='Ada Lovelace')
        Booking.objects.create(
            flight=self.flight, seat=Seat.objects.get(row=1, column='A'), passenger=passenger
        )
        response = self.client.get(reverse('flight-detail', args=[self.flight.id]))

        self.assertContains(response, 'Seat 1A, taken')
        self.assertContains(response, 'Seat 1B, available')

    def test_renders_the_reservation_panel_with_pricing(self) -> None:
        response = self.client.get(reverse('flight-detail', args=[self.flight.id]))

        self.assertContains(response, 'id="reserve-panel"')
        self.assertContains(response, 'data-fare="50.00"')
        self.assertContains(response, 'PlaneServe')
        self.assertContains(response, 'js/seat-selection.js')

    def test_available_seats_are_selectable_and_taken_seats_are_not(self) -> None:
        passenger = Passenger.objects.create(full_name='Ada Lovelace')
        Booking.objects.create(
            flight=self.flight, seat=Seat.objects.get(row=1, column='A'), passenger=passenger
        )
        response = self.client.get(reverse('flight-detail', args=[self.flight.id]))
        html = response.content.decode()

        taken = html.split('data-seat="1A"')[1].split('</button>')[0]
        available = html.split('data-seat="1B"')[1].split('</button>')[0]
        self.assertNotIn('aria-pressed', taken)
        self.assertIn('aria-pressed="false"', available)

    def test_no_template_comment_leaks_into_the_page(self) -> None:
        # Django's {# #} comments are single-line only; a multi-line one renders
        # as visible text instead of disappearing.
        response = self.client.get(reverse('flight-detail', args=[self.flight.id]))
        self.assertNotContains(response, '{#')

    def test_unknown_flight_is_404(self) -> None:
        response = self.client.get(reverse('flight-detail', args=[9999]))
        self.assertEqual(response.status_code, 404)

    def test_flight_list_links_to_the_seat_map(self) -> None:
        response = self.client.get(reverse('flight-list'))
        self.assertContains(response, reverse('flight-detail', args=[self.flight.id]))
