from __future__ import annotations

from unittest import mock

from django.test import TestCase

from assistant import extraction
from assistant.providers import ProviderUnavailable
from assistant.schema import SeatQuery, describe, seat_query_json_schema


def model_returns(payload: dict):
    """Stub the provider, not the HTTP client: the boundary is one file so the
    suite never needs Ollama running."""
    return mock.patch.object(extraction.providers, 'extract_json', return_value=payload)


class ExtractTests(TestCase):
    def test_a_normal_answer_becomes_a_query(self) -> None:
        with model_returns({'position': 'window', 'min_row': None, 'max_row': 10}):
            query = extraction.extract('window seat near the front')

        self.assertEqual(query, SeatQuery(position='window', max_row=10))

    def test_empty_prose_never_reaches_the_model(self) -> None:
        with mock.patch.object(extraction.providers, 'extract_json') as called:
            self.assertEqual(extraction.extract('   '), SeatQuery())
        called.assert_not_called()

    def test_the_provider_being_down_propagates(self) -> None:
        # Views degrade; extraction does not pretend it succeeded.
        with mock.patch.object(
            extraction.providers, 'extract_json', side_effect=ProviderUnavailable('down')
        ):
            with self.assertRaises(ProviderUnavailable):
                extraction.extract('window seat')


class ValidationTests(TestCase):
    """The model is not trusted. These are the cases that reached the ORM
    before the schema required its fields and bounded its rows."""

    def test_an_absurd_row_is_clamped_to_the_cabin(self) -> None:
        query = extraction._validate({'position': None, 'min_row': None, 'max_row': 10**15})
        self.assertEqual(query.max_row, 30)

    def test_a_row_below_the_cabin_is_clamped(self) -> None:
        self.assertEqual(extraction._validate({'min_row': -5}).min_row, 1)

    def test_a_reversed_range_is_swapped_rather_than_matching_nothing(self) -> None:
        query = extraction._validate({'min_row': 20, 'max_row': 10})
        self.assertEqual((query.min_row, query.max_row), (10, 20))

    def test_an_invented_position_is_dropped(self) -> None:
        self.assertIsNone(extraction._validate({'position': 'cockpit'}).position)

    def test_non_integer_rows_are_dropped(self) -> None:
        query = extraction._validate({'min_row': 'front', 'max_row': True})
        self.assertIsNone(query.min_row)
        self.assertIsNone(query.max_row)

    def test_a_direction_is_kept_and_a_nonsense_one_dropped(self) -> None:
        self.assertEqual(extraction._validate({'toward': 'back'}).toward, 'back')
        self.assertIsNone(extraction._validate({'toward': 'sideways'}).toward)

    def test_random_must_be_the_boolean_it_was_asked_for(self) -> None:
        self.assertTrue(extraction._validate({'random': True}).is_random)
        for answer in ('yes', 1, 'true', None):
            with self.subTest(answer=answer):
                self.assertFalse(extraction._validate({'random': answer}).is_random)

    def test_a_side_the_passenger_never_said_is_dropped(self) -> None:
        # The model asserts "right" for "aisle seat at the back" often enough
        # that this has to be enforced rather than requested.
        invented = extraction._drop_unsaid_side(SeatQuery(side='right'), 'aisle seat at the back')
        self.assertIsNone(invented.side)

    def test_a_side_the_passenger_did_say_survives(self) -> None:
        for prose in ['aisle on the right', 'starboard window', 'RIGHT side please']:
            with self.subTest(prose=prose):
                kept = extraction._drop_unsaid_side(SeatQuery(side='right'), prose)
                self.assertEqual(kept.side, 'right')

    def test_the_guard_does_not_match_a_word_that_merely_contains_a_side(self) -> None:
        # "alright" is not a request for the right-hand aisle.
        self.assertIsNone(
            extraction._drop_unsaid_side(SeatQuery(side='right'), 'alright anywhere').side
        )

    def test_an_invented_front_band_is_dropped(self) -> None:
        # The model reaches for "up to row 10" on requests that never
        # mentioned the front.
        query = extraction._drop_unsaid_band(
            SeatQuery(position='window', max_row=10), 'random window seat on the left'
        )
        self.assertIsNone(query.max_row)

    def test_a_band_the_passenger_asked_for_survives(self) -> None:
        for prose in ['window near the front', 'seat at the nose end', 'up ahead please']:
            with self.subTest(prose=prose):
                query = extraction._drop_unsaid_band(SeatQuery(max_row=10), prose)
                self.assertEqual(query.max_row, 10)

    def test_an_invented_back_band_is_dropped(self) -> None:
        query = extraction._drop_unsaid_band(SeatQuery(min_row=21), 'a window seat')
        self.assertIsNone(query.min_row)

    def test_explicit_rows_are_never_second_guessed(self) -> None:
        # "rows 5 to 9" is the passenger's, whatever words surround it.
        query = extraction._drop_unsaid_band(SeatQuery(min_row=21), 'seat in row 21')
        self.assertEqual(query.min_row, 21)

    def test_a_party_nobody_counted_out_loud_is_dropped(self) -> None:
        # Selecting six seats for someone who asked for one is the loudest way
        # this can be wrong.
        self.assertIsNone(
            extraction._drop_unsaid_party(SeatQuery(party=6), 'a window seat').party
        )

    def test_a_party_the_passenger_counted_survives(self) -> None:
        prose_with_counts = [
            'window seats for 6 people',
            'seats for six',
            'for the two of us',
            'a seat for a couple',
        ]
        for prose in prose_with_counts:
            with self.subTest(prose=prose):
                kept = extraction._drop_unsaid_party(SeatQuery(party=2), prose)
                self.assertEqual(kept.party, 2)

    def test_a_party_of_one_needs_no_counting(self) -> None:
        self.assertEqual(extraction._drop_unsaid_party(SeatQuery(party=1), 'a seat').party, 1)

    def test_the_party_is_clamped_to_the_cap(self) -> None:
        self.assertEqual(extraction._validate({'party': 99}).party, 6)
        self.assertEqual(extraction._validate({'party': 0}).party, 1)
        self.assertIsNone(extraction._validate({'party': 'six'}).party)

    def test_an_unsaid_direction_is_dropped(self) -> None:
        # "window seats for 6 people" announced "as far forward as possible".
        dropped = extraction._drop_unsaid_toward(
            SeatQuery(toward='front'), 'window seats for 6 people'
        )
        self.assertIsNone(dropped.toward)

    def test_a_direction_the_passenger_leaned_survives(self) -> None:
        leaning = [
            ('furthest back window', 'back'),
            ('as far forward as you can', 'front'),
            ('the very last row', 'back'),
        ]
        for prose, toward in leaning:
            with self.subTest(prose=prose):
                kept = extraction._drop_unsaid_toward(SeatQuery(toward=toward), prose)
                self.assertEqual(kept.toward, toward)

    def test_invented_fields_do_not_survive(self) -> None:
        query = extraction._validate({'position': 'aisle', 'discount': 90, 'seat': '1A'})
        self.assertEqual(query, SeatQuery(position='aisle'))


class VocabularyFallbackTests(TestCase):
    def test_an_unrecognised_phrase_falls_back_to_the_index(self) -> None:
        with model_returns({'position': None, 'min_row': None, 'max_row': None}):
            with mock.patch.object(
                extraction.vocabulary, 'resolve', return_value=('position', 'window')
            ) as resolve:
                query = extraction.extract('by the porthole')

        resolve.assert_called_once()
        self.assertEqual(query.position, 'window')

    def test_the_index_is_not_consulted_when_the_model_understood(self) -> None:
        # It costs an embedding call, so it is a fallback, not a step.
        with model_returns({'position': 'aisle', 'min_row': None, 'max_row': None}):
            with mock.patch.object(extraction.vocabulary, 'resolve') as resolve:
                extraction.extract('aisle please')

        resolve.assert_not_called()


class SchemaTests(TestCase):
    def test_the_schema_requires_its_fields_and_bounds_its_rows(self) -> None:
        # Both were learned the hard way: without `required` the model omitted
        # position, and without bounds it answered with rows in the trillions.
        schema = seat_query_json_schema()

        self.assertEqual(
            schema['required'],
            ['position', 'min_row', 'max_row', 'toward', 'side', 'party', 'random'],
        )
        self.assertEqual(schema['properties']['max_row']['maximum'], 30)
        self.assertFalse(schema['additionalProperties'])

    def test_descriptions_read_like_english(self) -> None:
        for query, expected in [
            (SeatQuery(), 'any free seat'),
            (SeatQuery(position='window', max_row=10), 'window seats up to row 10'),
            (SeatQuery(position='aisle', min_row=21), 'aisle seats from row 21 back'),
            (SeatQuery(min_row=12, max_row=12), 'seats in row 12'),
            (SeatQuery(position='middle', min_row=5, max_row=9), 'middle seats in rows 5-9'),
            (
                SeatQuery(position='window', toward='back'),
                'window seats as far back as possible',
            ),
            (SeatQuery(toward='front'), 'seats as far forward as possible'),
            (SeatQuery(is_random=True), 'any free seat, at random'),
            (
                SeatQuery(position='window', party=6),
                'window seats for 6 passengers',
            ),
            (
                SeatQuery(position='aisle', side='right', min_row=21),
                'aisle seats on the right from row 21 back',
            ),
            (
                SeatQuery(position='window', is_random=True, min_row=11, max_row=20),
                'random window seats in rows 11-20',
            ),
        ]:
            with self.subTest(query=query):
                self.assertEqual(describe(query), expected)
