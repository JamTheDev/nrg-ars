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

        self.assertEqual(schema['required'], ['position', 'min_row', 'max_row'])
        self.assertEqual(schema['properties']['max_row']['maximum'], 30)
        self.assertFalse(schema['additionalProperties'])

    def test_descriptions_read_like_english(self) -> None:
        for query, expected in [
            (SeatQuery(), 'any free seat'),
            (SeatQuery(position='window', max_row=10), 'window seats up to row 10'),
            (SeatQuery(position='aisle', min_row=21), 'aisle seats from row 21 back'),
            (SeatQuery(min_row=12, max_row=12), 'seats in row 12'),
            (SeatQuery(position='middle', min_row=5, max_row=9), 'middle seats in rows 5-9'),
        ]:
            with self.subTest(query=query):
                self.assertEqual(describe(query), expected)
