"""Validated filter object produced from natural-language input.

The LLM never emits SQL. Its only output is a SeatQuery, which is validated
here before any database access. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Position = Literal['window', 'aisle', 'middle']


@dataclass(frozen=True)
class SeatQuery:
    flight_number: str | None = None
    destination: str | None = None
    position: Position | None = None
    min_row: int | None = None
    max_row: int | None = None


# Passed to Ollama as `format=` so decoding is constrained to this shape rather
# than parsed out of prose after the fact.
SEAT_QUERY_JSON_SCHEMA: dict = {
    'type': 'object',
    'properties': {
        'flight_number': {'type': ['string', 'null']},
        'destination': {'type': ['string', 'null'], 'maxLength': 3},
        'position': {'type': ['string', 'null'], 'enum': ['window', 'aisle', 'middle', None]},
        'min_row': {'type': ['integer', 'null']},
        'max_row': {'type': ['integer', 'null']},
    },
    'additionalProperties': False,
}
