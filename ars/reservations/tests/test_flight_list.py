from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reservations import selectors
from reservations.models import Booking, Flight, Passenger, Seat


class FlightRowsTests(TestCase):
    def setUp(self) -> None:
        now = timezone.now()
        # Numbers deliberately sort the opposite way to the departures: the
        # first version of this test used PR101/PR205, which read the same
        # either way and passed while the board was sorted by nothing at all.
        self.later = Flight.objects.create(
            number='AA999', origin='MNL', destination='DVO', departs_at=now + timedelta(days=2)
        )
        self.sooner = Flight.objects.create(
            number='ZZ001', origin='MNL', destination='CEB', departs_at=now + timedelta(hours=3)
        )

    def test_seats_are_seeded_from_the_cabin_layout(self) -> None:
        # 30 rows x 6 columns, seeded by migration 0002.
        self.assertEqual(Seat.objects.count(), 180)

    def test_rows_are_ordered_by_departure(self) -> None:
        numbers = [row.flight.number for row in selectors.flight_rows()]
        self.assertEqual(numbers, ['ZZ001', 'AA999'])

    def test_availability_is_derived_from_bookings(self) -> None:
        passenger = Passenger.objects.create(full_name='Ada Lovelace')
        for seat in Seat.objects.all()[:3]:
            Booking.objects.create(flight=self.sooner, seat=seat, passenger=passenger)

        rows = {row.flight.number: row for row in selectors.flight_rows()}
        self.assertEqual(rows['ZZ001'].seats_available, 177)
        # Seats are a shared catalog, so booking one flight cannot affect another.
        self.assertEqual(rows['AA999'].seats_available, 180)

    def test_flight_list_is_two_queries_regardless_of_flight_count(self) -> None:
        # One for the catalog size, one for the annotated flights -- no N+1.
        with self.assertNumQueries(2):
            selectors.flight_rows()


class FlightListViewTests(TestCase):
    def test_renders_scheduled_flights(self) -> None:
        Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )
        response = self.client.get(reverse('flight-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PR101')
        self.assertContains(response, '180 seats left')

    def test_no_template_comment_leaks_into_the_page(self) -> None:
        response = self.client.get(reverse('flight-list'))
        self.assertNotContains(response, '{#')

    def test_renders_empty_state_without_flights(self) -> None:
        response = self.client.get(reverse('flight-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No flights are scheduled.')
