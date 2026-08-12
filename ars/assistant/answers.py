"""Answers to questions about a flight.

The customer-service half of the assistant: "how many seats are available?",
"how many window seats are there?". The model classifies the question and
names the filter; **every number in the reply comes from the database**, never
from the model. A bot that invents a seat count is worse than no bot, and a
language model asked to count will invent one.

So the model never sees a total. It maps prose onto a SeatQuery, the ORM
counts, and the sentence is assembled here.
"""

from __future__ import annotations

from django.utils import timezone
from reservations import selectors
from reservations.models import Flight

from assistant.schema import SeatQuery, describe


def seats(count: int) -> str:
    return '1 seat' if count == 1 else f'{count} seats'


def answer(flight: Flight, query: SeatQuery) -> str:
    """A sentence answering `query` about `flight`."""
    if query.intent == 'status':
        return _status(flight)
    return _count(flight, query)


def _count(flight: Flight, query: SeatQuery) -> str:
    counts = selectors.count_seats(flight, query)

    # An unfiltered question is about the whole cabin, and "180 of 180 seats
    # match" is a silly way to say "the aeroplane".
    if query.is_empty:
        return f'{flight.number} has {counts.free} of {counts.matching} seats free.'

    described = describe(query)  # "window seats", "aisle seats from row 21 back"
    if counts.matching == 0:
        return f'{flight.number} has no {described}.'
    if counts.free == 0:
        return f'{flight.number} has {counts.matching} {described}, and none are free.'
    if counts.free == counts.matching:
        return f'All {counts.matching} {described} on {flight.number} are free.'
    return (
        f'{flight.number} has {counts.matching} {described}, '
        f'{counts.free} of them free.'
    )


def _status(flight: Flight) -> str:
    cabin = selectors.seat_map(flight)
    departs = timezone.localtime(flight.departs_at)
    when = departs.strftime('%a %d %b at %H:%M')
    verb = 'departed' if flight.departs_at <= timezone.now() else 'departs'

    fullness = (
        'and is full'
        if cabin.seats_available == 0
        else f'with {seats(cabin.seats_available)} of {cabin.seats_total} still free'
    )
    return (
        f'{flight.number} {verb} {when}, {flight.origin} to {flight.destination}, '
        f'{fullness}.'
    )
