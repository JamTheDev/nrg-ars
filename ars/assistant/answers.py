"""Answers to questions about a flight.

The customer-service half of the assistant: "how many seats are available?",
"what seats are in the middle section?", "is this flight full?".

**Facts are counted, then phrased.** The ORM produces the numbers; the model is
handed those numbers and asked to write a sentence. It is never asked to count,
because a language model asked to count will invent, and a bot that invents
seat counts is worse than no bot.

The phrasing is then checked back against the facts: every number and every
seat code in the reply must be one we supplied. If it drifts, or the model is
unavailable, the plainly composed sentence is used instead. That is the whole
trade -- natural wording when it can be trusted, a stiff sentence when it
cannot, and never a confident wrong number.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.utils import timezone
from reservations import selectors
from reservations.models import Flight

from assistant import prompts, providers
from assistant.schema import SeatQuery, describe

SEAT_CODE = re.compile(r'\b(\d{1,2})([A-Za-z])\b')
NUMBER = re.compile(r'\d+')


def seats(count: int) -> str:
    return '1 seat' if count == 1 else f'{count} seats'


def answer(flight: Flight, query: SeatQuery) -> str:
    """A sentence answering `query` about `flight`."""
    facts = _facts(flight, query)
    plain = _plain(flight, query, facts)
    return _phrase(facts, plain)


# --- the facts -----------------------------------------------------------


def _facts(flight: Flight, query: SeatQuery) -> dict:
    departs = timezone.localtime(flight.departs_at)
    facts = {
        'flight': flight.number,
        'route': f'{flight.origin} to {flight.destination}',
        'departs': departs.strftime('%a %d %b at %H:%M'),
        'has_departed': flight.departs_at <= timezone.now(),
        'question': query.intent,
    }

    if query.intent == 'status':
        cabin = selectors.seat_map(flight)
        facts |= {
            'seats_in_cabin': cabin.seats_total,
            'seats_free': cabin.seats_available,
            'seats_taken': cabin.seats_taken,
        }
        return facts

    counts = selectors.count_seats(flight, query)
    facts |= {
        'describing': describe(query),
        'seats_matching': counts.matching,
        'seats_free': counts.free,
        'seats_taken': counts.taken,
        'seats_in_cabin': settings.CABIN_ROWS * len(settings.CABIN_COLUMNS),
    }
    if counts.first:
        facts |= {
            'first_seat': counts.first,
            'last_seat': counts.last,
            'rows': f'{counts.first_row} to {counts.last_row}',
            'columns': ', '.join(counts.columns),
        }
    return facts


# --- the plain sentence, used when phrasing cannot be trusted ------------


def _plain(flight: Flight, query: SeatQuery, facts: dict) -> str:
    if query.intent == 'status':
        verb = 'departed' if facts['has_departed'] else 'departs'
        fullness = (
            'and is full'
            if facts['seats_free'] == 0
            else f'with {seats(facts["seats_free"])} of {facts["seats_in_cabin"]} still free'
        )
        return f'{flight.number} {verb} {facts["departs"]}, {facts["route"]}, {fullness}.'

    matching, free = facts['seats_matching'], facts['seats_free']
    described = facts['describing']

    if matching == 0:
        return f'{flight.number} has no {described}.'

    if query.intent == 'list':
        return (
            f'{described.capitalize()} run from {facts["first_seat"]} to '
            f'{facts["last_seat"]} — {matching} seats, {free} of them free.'
        )

    if query.is_empty:
        return f'{flight.number} has {free} of {matching} seats free.'
    if free == 0:
        return f'{flight.number} has {matching} {described}, and none are free.'
    if free == matching:
        return f'All {matching} {described} on {flight.number} are free.'
    return f'{flight.number} has {matching} {described}, {free} of them free.'


# --- the phrasing, and the check that it stayed honest -------------------


def _phrase(facts: dict, plain: str) -> str:
    lines = '\n'.join(f'{key}: {value}' for key, value in facts.items())
    prompt = f'Facts:\n{lines}\n\nThe passenger asked about this flight. Answer them.'

    try:
        reply = providers.write(prompts.PHRASING_SYSTEM, prompt)
    except providers.ProviderUnavailable:
        return plain

    reply = ' '.join(reply.split())
    return reply if reply and _is_grounded(reply, facts) else plain


def _grounded_numbers(facts: dict) -> set[str]:
    """Numbers the reply is allowed to contain.

    The fact values themselves, plus every row inside the matching range: a
    reply that names row 14 of rows 11 to 20 is telling the truth.
    """
    allowed = {str(value) for value in facts.values() if isinstance(value, int)}

    first, last = facts.get('first_seat'), facts.get('last_seat')
    if first and last:
        low = SEAT_CODE.match(first)
        high = SEAT_CODE.match(last)
        if low and high:
            allowed |= {str(row) for row in range(int(low[1]), int(high[1]) + 1)}

    for text in (facts.get('departs'), facts.get('rows')):
        if text:
            allowed |= set(NUMBER.findall(text))
    return allowed


def _is_grounded(reply: str, facts: dict) -> bool:
    """Every number and seat code in the reply must be one we supplied."""
    allowed = _grounded_numbers(facts)

    for code in SEAT_CODE.finditer(reply):
        row, column = code[1], code[2].upper()
        if row not in allowed or column not in settings.CABIN_COLUMNS:
            return False

    # Seat codes are legitimate numbers; check what is left over.
    remainder = SEAT_CODE.sub(' ', reply)
    return all(number in allowed for number in NUMBER.findall(remainder))
