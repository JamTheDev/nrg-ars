"""Validated filter object produced from natural-language input.

The LLM never emits SQL. Its only output is a SeatQuery, which is validated
here before any database access. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.conf import settings

Position = Literal['window', 'aisle', 'middle']
Toward = Literal['front', 'back']
Side = Literal['left', 'right']
# What the passenger wants done. "How many window seats are there?" and "give
# me a window seat" describe the same seats and want opposite things back.
Intent = Literal['find', 'count', 'status']


@dataclass(frozen=True)
class SeatQuery:
    intent: Intent = 'find'
    flight_number: str | None = None
    destination: str | None = None
    position: Position | None = None
    min_row: int | None = None
    max_row: int | None = None
    # Which end of the cabin the passenger is drawn to. Bounds say which seats
    # are acceptable; this says which of them to offer first, and it is the
    # difference between "at the back" and "as far back as possible".
    toward: Toward | None = None
    # Which side of the aisle, facing forward: A-C left, D-F right. "Aisle
    # seat" alone spans both sides, so without this "aisle on the right" can
    # only ever be answered with the left one.
    side: Side | None = None
    # How many seats to select. The stepper says this too; a phrase like
    # "window seats for 6 people" says it in one breath instead.
    party: int | None = None
    # Seat the party as a group rather than wherever the filter happens to
    # match first. A family of six spread over three rows satisfies the filter
    # and not the request.
    together: bool = False
    # The passenger does not mind which seat they get. Offering them 1A every
    # time is a defensible reading of "random" and a poor answer to it.
    is_random: bool = False

    @property
    def is_empty(self) -> bool:
        """Nothing was asked for -- no filter to apply."""
        return (
            self.position is None
            and self.min_row is None
            and self.max_row is None
            and self.toward is None
            and self.side is None
            and self.party is None
            and not self.together
            and not self.is_random
        )


def describe(query: SeatQuery) -> str:
    """A short human reading of a filter, so the passenger can see what was
    understood rather than guessing from which seats lit up."""
    if query.is_empty:
        return 'any free seat'

    unconstrained = (
        query.position is None
        and query.min_row is None
        and query.max_row is None
        and query.toward is None
        and query.side is None
    )
    if query.is_random and unconstrained and not query.party:
        return 'any free seat, at random'

    noun = f'{query.position} seats' if query.position else 'seats'
    parts = [f'random {noun}' if query.is_random else noun]
    if query.side:
        parts.append(f'on the {query.side}')
    # Only worth saying when no bound already says it: "as far forward as
    # possible up to row 10" tells the passenger the same thing twice.
    if query.toward and query.min_row is None and query.max_row is None:
        parts.append(
            'as far back as possible'
            if query.toward == 'back'
            else 'as far forward as possible'
        )
    if query.min_row and query.max_row:
        if query.min_row == query.max_row:
            parts.append(f'in row {query.min_row}')
        else:
            parts.append(f'in rows {query.min_row}-{query.max_row}')
    elif query.min_row:
        parts.append(f'from row {query.min_row} back')
    elif query.max_row:
        parts.append(f'up to row {query.max_row}')
    if query.party and query.party > 1:
        parts.append(f'for {query.party} passengers')
    if query.together:
        parts.append('together')
    return ' '.join(parts)


def seat_query_json_schema() -> dict:
    """The shape Ollama is constrained to, built from the real cabin size.

    Three things here are load-bearing, learned by measurement rather than
    guessed (see docs/plans/08-natural-language-search.md):

    * `required` -- without it the model omits keys it is unsure about, and
      `position` went missing on half the phrasings.
    * `minimum`/`maximum` -- without bounds a 1.7B model answered "near the
      front" with max_row 1000000000000000.
    * only the within-flight fields are offered. `flight_number` and
      `destination` stay on the dataclass for a future search across flights,
      but asking for fields this release cannot use invites invented values.
    """
    return {
        'type': 'object',
        'properties': {
            'position': {
                'type': ['string', 'null'],
                'enum': ['window', 'aisle', 'middle', None],
            },
            'min_row': {
                'type': ['integer', 'null'],
                'minimum': 1,
                'maximum': settings.CABIN_ROWS,
            },
            'max_row': {
                'type': ['integer', 'null'],
                'minimum': 1,
                'maximum': settings.CABIN_ROWS,
            },
            'toward': {
                'type': ['string', 'null'],
                'enum': ['front', 'back', None],
            },
            'side': {
                'type': ['string', 'null'],
                'enum': ['left', 'right', None],
            },
            'party': {
                'type': ['integer', 'null'],
                'minimum': 1,
                'maximum': settings.MAX_PARTY_SIZE,
            },
            'intent': {
                'type': 'string',
                'enum': ['find', 'count', 'status'],
            },
            'together': {'type': 'boolean'},
            'random': {'type': 'boolean'},
        },
        'required': [
            'intent',
            'position',
            'min_row',
            'max_row',
            'toward',
            'side',
            'party',
            'together',
            'random',
        ],
        'additionalProperties': False,
    }
