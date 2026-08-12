from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from reservations import services
from reservations.models import Booking, Flight, Passenger, Seat

from assistant import answers, extraction
from assistant.schema import SeatQuery


class AnswerTests(TestCase):
    def setUp(self) -> None:
        self.flight = Flight.objects.create(
            number='PR101',
            origin='MNL',
            destination='CEB',
            departs_at=timezone.now() + timedelta(hours=3),
        )

    def ask(self, **kwargs) -> str:
        return answers.answer(self.flight, SeatQuery(intent='count', **kwargs))

    def test_how_many_seats_are_available(self) -> None:
        services.book_seats(self.flight, [services.SeatRequest('1A', 'Ada Lovelace')])
        self.assertEqual(self.ask(), 'PR101 has 179 of 180 seats free.')

    def test_how_many_window_seats_are_there(self) -> None:
        # The cabin's shape and today's availability are different questions,
        # and the answer carries both.
        services.book_seats(self.flight, [services.SeatRequest('1A', 'Ada Lovelace')])
        self.assertEqual(
            self.ask(position='window'),
            'PR101 has 60 window seats, 59 of them free.',
        )

    def test_all_free_reads_naturally(self) -> None:
        self.assertEqual(
            self.ask(position='window'),
            'All 60 window seats on PR101 are free.',
        )

    def test_none_free_is_said_plainly(self) -> None:
        services.book_seats(
            self.flight, [services.SeatRequest(f'1{c}', f'P{c}') for c in 'ABCDEF']
        )
        self.assertEqual(
            self.ask(min_row=1, max_row=1),
            'PR101 has 6 seats in row 1, and none are free.',
        )

    def test_a_filter_matching_nothing(self) -> None:
        # There is no such thing as a middle seat in the aisle columns.
        answer = answers.answer(
            self.flight, SeatQuery(intent='count', position='middle', side='left', min_row=99)
        )
        self.assertIn('no ', answer)

    def test_status_covers_when_where_and_how_full(self) -> None:
        self.flight.departs_at = datetime(2026, 8, 14, 9, 30, tzinfo=UTC)
        self.flight.save()

        answer = answers.answer(self.flight, SeatQuery(intent='status'))
        self.assertIn('PR101', answer)
        self.assertIn('MNL to CEB', answer)
        self.assertIn('180 still free', answer)

    def test_a_full_flight_says_so(self) -> None:
        passenger = Passenger.objects.create(full_name='Filler')
        Booking.objects.bulk_create(
            [
                Booking(flight=self.flight, seat=seat, passenger=passenger)
                for seat in Seat.objects.all()
            ]
        )
        self.assertIn('is full', answers.answer(self.flight, SeatQuery(intent='status')))

    def test_counting_costs_two_queries(self) -> None:
        with self.assertNumQueries(2):
            self.ask(position='window')


class IntentTests(TestCase):
    """Question or request is decided by the words, not by the model alone.

    Getting it backwards is the rudest failure available: answering "give me a
    window seat" with a head count, or selecting a seat for someone who only
    asked how many were left.
    """

    def settle(self, intent: str, prose: str) -> str:
        return extraction._settle_intent(SeatQuery(intent=intent), prose).intent

    def test_a_question_is_a_question_even_if_the_model_says_find(self) -> None:
        for prose in [
            'How many seats are available?',
            'how many window seats are there',
            'are there any aisle seats left',
            'is this flight full?',
        ]:
            with self.subTest(prose=prose):
                self.assertIn(self.settle('find', prose), ('count', 'status'))

    def test_a_request_is_a_request_even_if_the_model_says_count(self) -> None:
        for prose in ['window seat near the front', 'give me 2 seats together']:
            with self.subTest(prose=prose):
                self.assertEqual(self.settle('count', prose), 'find')

    def test_status_survives_a_question(self) -> None:
        self.assertEqual(self.settle('status', 'is this flight full?'), 'status')

    def test_the_model_never_supplies_a_number(self) -> None:
        # The reply is composed from the ORM; the model only says what to count.
        flight = Flight.objects.create(
            number='PR205',
            origin='MNL',
            destination='DVO',
            departs_at=timezone.now() + timedelta(days=1),
        )
        with mock.patch.object(
            extraction.providers,
            'extract_json',
            return_value={'intent': 'count', 'position': 'window'},
        ):
            query = extraction.extract('how many window seats are there?')

        self.assertEqual(
            answers.answer(flight, query), 'All 60 window seats on PR205 are free.'
        )
