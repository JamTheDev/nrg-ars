from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from reservations import services
from reservations.exceptions import (
    FlightFullError,
    InvalidPassengerError,
    InvalidSeatError,
    NothingSelectedError,
    PartyTooLargeError,
    SeatNotFoundError,
    SeatTakenError,
)
from reservations.models import Booking, Flight, Passenger, Seat


def seat_request(designation: str, name: str = 'Ada Lovelace') -> services.SeatRequest:
    return services.SeatRequest(designation=designation, passenger_name=name)


class ParseDesignationTests(TestCase):
    def test_accepts_the_shapes_a_kiosk_produces(self) -> None:
        for raw, expected in [
            ('1A', (1, 'A')),
            ('12c', (12, 'C')),
            (' 7 D ', (7, 'D')),
            ('30f', (30, 'F')),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(services.parse_designation(raw), expected)

    def test_rejects_anything_that_is_not_a_designation(self) -> None:
        for raw in ['99ZZ', '', 'A1', '1', 'row 12 seat C', '123A']:
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidSeatError):
                    services.parse_designation(raw)

    def test_well_formed_but_outside_the_cabin_is_a_different_failure(self) -> None:
        # "45A" parses fine; it just does not exist. Telling the passenger
        # "not a seat designation" here would be wrong.
        with self.assertRaises(SeatNotFoundError):
            services.resolve_seat('45A')


class BookSeatsTests(TestCase):
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

    def test_books_the_requested_seats(self) -> None:
        bookings = services.book_seats(
            self.flight,
            [seat_request('12C', 'Ada Lovelace'), seat_request('12d', 'Alan Turing')],
        )

        self.assertEqual([b.seat.designation for b in bookings], ['12C', '12D'])
        self.assertEqual(
            sorted(b.passenger.full_name for b in bookings), ['Ada Lovelace', 'Alan Turing']
        )
        self.assertEqual(Booking.objects.filter(flight=self.flight).count(), 2)

    def test_the_same_seat_on_another_flight_is_untouched(self) -> None:
        services.book_seats(self.flight, [seat_request('1A')])
        services.book_seats(self.other, [seat_request('1A')])

        self.assertEqual(Booking.objects.count(), 2)

    def test_a_taken_seat_blocks_the_request(self) -> None:
        services.book_seats(self.flight, [seat_request('1A')])

        with self.assertRaises(SeatTakenError) as caught:
            services.book_seats(self.flight, [seat_request('1A', 'Alan Turing')])

        self.assertEqual(caught.exception.designation, '1A')
        self.assertEqual(Booking.objects.filter(flight=self.flight).count(), 1)

    def test_a_party_is_all_or_nothing(self) -> None:
        services.book_seats(self.flight, [seat_request('2B')])

        with self.assertRaises(SeatTakenError):
            services.book_seats(
                self.flight,
                [
                    seat_request('2A', 'Ada'),
                    seat_request('2B', 'Alan'),
                    seat_request('2C', 'Grace'),
                ],
            )

        # 2A landed before 2B failed; it must not survive the rollback.
        booked = set(
            Booking.objects.filter(flight=self.flight).values_list('seat__row', 'seat__column')
        )
        self.assertEqual(booked, {(2, 'B')})

    def test_a_blank_name_books_nothing(self) -> None:
        with self.assertRaises(InvalidPassengerError):
            services.book_seats(
                self.flight, [seat_request('3A', 'Ada'), seat_request('3B', '   ')]
            )

        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Passenger.objects.count(), 0)

    def test_an_empty_request_is_refused(self) -> None:
        with self.assertRaises(NothingSelectedError):
            services.book_seats(self.flight, [])

    def test_a_party_beyond_the_cap_is_refused_server_side(self) -> None:
        # The UI caps this twice over, but the cap is a rule about bookings.
        with self.assertRaises(PartyTooLargeError):
            services.book_seats(
                self.flight, [seat_request(f'1{c}', f'P{c}') for c in 'ABCDEF']
                + [seat_request('2A', 'Extra')]
            )

        self.assertEqual(Booking.objects.count(), 0)

    def test_exactly_the_cap_is_allowed(self) -> None:
        bookings = services.book_seats(
            self.flight, [seat_request(f'1{c}', f'P{c}') for c in 'ABCDEF']
        )
        self.assertEqual(len(bookings), 6)


class AssignFirstAvailableTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )

    def test_first_is_lowest_row_then_lowest_column(self) -> None:
        booking = services.assign_first_available(self.flight, 'Ada Lovelace')
        self.assertEqual(booking.seat.designation, '1A')

        second = services.assign_first_available(self.flight, 'Alan Turing')
        self.assertEqual(second.seat.designation, '1B')

    def test_skips_seats_taken_on_this_flight(self) -> None:
        services.book_seats(self.flight, [seat_request('1A'), seat_request('1B')])

        booking = services.assign_first_available(self.flight, 'Grace Hopper')
        self.assertEqual(booking.seat.designation, '1C')

    def test_losing_a_race_takes_the_next_seat_rather_than_failing(self) -> None:
        # The passenger asked for *a* seat, not for 1A, so a lost race is not
        # a refusal -- the deliberate exception to "the loser is blocked".
        original = Booking.objects.create
        attempts = {'count': 0}

        def flaky(**kwargs):
            attempts['count'] += 1
            if attempts['count'] == 1:
                raise IntegrityError('UNIQUE constraint failed: reservations_booking')
            return original(**kwargs)

        with mock.patch.object(Booking.objects, 'create', side_effect=flaky):
            booking = services.assign_first_available(self.flight, 'Ada Lovelace')

        self.assertEqual(attempts['count'], 2)
        self.assertEqual(booking.seat.designation, '1A')
        # The passenger row from the abandoned attempt is cleaned up.
        self.assertEqual(Passenger.objects.count(), 1)

    def test_a_full_flight_says_so(self) -> None:
        passenger = Passenger.objects.create(full_name='Ada Lovelace')
        Booking.objects.bulk_create(
            [
                Booking(flight=self.flight, seat=seat, passenger=passenger)
                for seat in Seat.objects.all()
            ]
        )

        with self.assertRaises(FlightFullError):
            services.assign_first_available(self.flight, 'Alan Turing')

    def test_a_blank_name_is_refused(self) -> None:
        with self.assertRaises(InvalidPassengerError):
            services.assign_first_available(self.flight, '  ')
