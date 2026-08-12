"""Natural language -> validated SeatQuery.

Step 1 of the pipeline in ARCHITECTURE.md section 7. Values the model invents
are dropped and out-of-range rows clamped here, before any query runs.
"""

from __future__ import annotations

import re
from dataclasses import replace

from django.conf import settings

from assistant import prompts, providers, safety, vocabulary
from assistant.schema import SeatQuery, seat_query_json_schema

POSITIONS = ('window', 'aisle', 'middle')

# A side is the one field the passenger's own words can be checked against:
# unlike "window", which has endless synonyms, naming a side takes one of a
# closed set of words. The model asserts "right" for "aisle seat at the back"
# often enough that asking it more nicely is not the fix.
SIDE_WORDS = {
    'left': r'\b(left|port)\b',
    'right': r'\b(right|starboard)\b',
}

# The same closed-set trick for the two default row bands. The model reaches
# for "up to row 10" on requests that never mentioned the front at all.
FRONT_WORDS = r'\b(front|forward|nose|ahead|beginning|first)\b'
BACK_WORDS = r'\b(back|rear|tail|behind|end|last|furthest|farthest)\b'

# A party size the passenger did not state is the most disruptive thing the
# model can invent -- it selects seats for people who do not exist. Counting
# words are a closed set, so this is checkable the same way a side is.
# Position is the last field to get a guard, and it needed one: "6 seats for
# a family in one row" came back as middle seats, every time. The list is
# wider than the three canonical words because the phrasing is; anything
# stranger than this falls through to the vector vocabulary.
POSITION_WORDS = (
    r'\b(window|windows|porthole|view|outside|aisle|corridor|walkway|passage'
    r'|middle|centre|center|between)\b'
)

# Questions look like questions. The model muddles "how many window seats are
# there?" with "give me a window seat" often enough to check the words.
# "what seats are in the middle section" wants the seats named, not counted.
# "the middle section" is a place in the cabin; "a middle seat" is a seat type.
# The model collapses them however the prompt is worded, so the distinction is
# enforced here.
MIDDLE_SECTION = (
    r'\bmiddle\s+(section|part|third)\b'
    r'|\b(middle|centre|center)\s+of\s+the\s+(plane|aircraft|cabin)\b'
    r'|\bhalfway\s+down\b'
)

LIST_WORDS = r'\b(what|which|list|show me|name)\b[^?]*\bseats?\b'

QUESTION_WORDS = (
    r'(\?|\b(how many|how much|how full|what|when|where|which|is there|are there'
    r'|do you have|any left|status|available)\b)'
)

TOGETHER_WORDS = (
    r'\b(together|one row|same row|single row|next to|beside|side by side'
    r'|adjacent|family|group|party|as a unit)\b'
)

COUNT_WORDS = (
    r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten'
    r'|couple|pair|both|twins?)\b'
)

def extract(prose: str) -> SeatQuery:
    """Turn a passenger's phrasing into a validated SeatQuery.

    Raises ProviderUnavailable if Ollama is unreachable, and UnsafeRequest if
    the message reads as an injection attempt. Callers degrade to the ordinary
    seat map rather than surfacing either as an error.
    """
    text = (prose or '').strip()
    if not text:
        return SeatQuery()

    # Screened before it is treated as a request at all.
    safety.check(text)

    raw = providers.extract_json(
        text, seat_query_json_schema(), system=prompts.extraction_system()
    )
    query = _validate(raw)
    query = _drop_unsaid_side(query, text)
    query = _drop_unsaid_band(query, text)
    query = _drop_unsaid_toward(query, text)
    query = _drop_unsaid_position(query, text)
    query = _drop_unsaid_together(query, text)
    query = _settle_intent(query, text)
    query = _middle_means_rows(query, text)
    query = _drop_unsaid_party(query, text)

    # Only ask the vocabulary index about phrasing the model made nothing of.
    # It costs an embedding call, so it is a fallback, not a step.
    if query.is_empty:
        resolved = vocabulary.resolve(text)
        if resolved:
            field, value = resolved
            query = _validate({**raw, field: value})

    return query


def _drop_unsaid_side(query: SeatQuery, prose: str) -> SeatQuery:
    """Refuse a side the passenger never mentioned.

    Answering "aisle seat at the back" with the right-hand aisle is not a
    worse guess than the left one -- it is an answer to a question that was
    not asked, and it hides half the cabin for no reason.
    """
    if not query.side:
        return query
    if re.search(SIDE_WORDS[query.side], prose, re.IGNORECASE):
        return query
    return replace(query, side=None)


def _middle_means_rows(query: SeatQuery, prose: str) -> SeatQuery:
    """"The middle section" is a place, not a seat type."""
    if not re.search(MIDDLE_SECTION, prose, re.IGNORECASE):
        return query

    rows = settings.CABIN_ROWS
    changes = {}
    if query.position == 'middle':
        changes['position'] = None
    if query.min_row is None and query.max_row is None:
        changes |= {'min_row': rows // 3 + 1, 'max_row': rows - rows // 3}
    return replace(query, **changes) if changes else query


def _settle_intent(query: SeatQuery, prose: str) -> SeatQuery:
    """Decide question from request by how it was phrased.

    Getting this backwards is the rudest failure available: answering "give me
    a window seat" with a head count, or selecting a seat for someone who only
    asked how many were left. Both directions are corrected, because the words
    are a better signal than the model's own classification.
    """
    asked = re.search(QUESTION_WORDS, prose, re.IGNORECASE) is not None
    wants_names = re.search(LIST_WORDS, prose, re.IGNORECASE) is not None

    if wants_names:
        return replace(query, intent='list')
    if asked and query.intent == 'find':
        return replace(query, intent='count')
    if not asked and query.intent in ('count', 'list', 'status'):
        return replace(query, intent='find')
    return query


def _drop_unsaid_position(query: SeatQuery, prose: str) -> SeatQuery:
    """Refuse a seat type the passenger never named.

    "6 seats for a family in one row" is not a request for middle seats, and
    the model insists otherwise. Phrasing stranger than this word list leaves
    the query empty, which is what sends it to the vector vocabulary.
    """
    if not query.position:
        return query
    if re.search(POSITION_WORDS, prose, re.IGNORECASE):
        return query
    return replace(query, position=None)


def _drop_unsaid_together(query: SeatQuery, prose: str) -> SeatQuery:
    """Grouping is a request, not an assumption."""
    if not query.together:
        return query
    if re.search(TOGETHER_WORDS, prose, re.IGNORECASE):
        return query
    return replace(query, together=False)


def _drop_unsaid_party(query: SeatQuery, prose: str) -> SeatQuery:
    """Refuse a party size nobody counted out loud.

    Selecting six seats for a passenger who asked for one is the loudest way
    this can be wrong, so a party above one needs a number, or a word that
    stands for one, in the passenger's own sentence.
    """
    if not query.party or query.party == 1:
        return query
    if re.search(COUNT_WORDS, prose, re.IGNORECASE):
        return query
    return replace(query, party=None)


def _drop_unsaid_toward(query: SeatQuery, prose: str) -> SeatQuery:
    """Refuse an end the passenger never leaned towards.

    Same closed set as the bands. Left alone, "window seats for 6 people" comes
    back announcing "as far forward as possible", which is a preference nobody
    expressed.
    """
    if not query.toward:
        return query
    words = FRONT_WORDS if query.toward == 'front' else BACK_WORDS
    if re.search(words, prose, re.IGNORECASE):
        return query
    return replace(query, toward=None)


def _drop_unsaid_band(query: SeatQuery, prose: str) -> SeatQuery:
    """Refuse a front/back band the passenger never asked for.

    Only the two defaults from the prompt are second-guessed, and only when
    the passenger named no rows at all -- an explicit "rows 5 to 9" is theirs,
    whatever words surround it.
    """
    if re.search(r'\d', prose):
        return query

    rows = settings.CABIN_ROWS
    front_default = max(1, rows // 3)
    back_default = rows - rows // 3 + 1

    if (
        query.max_row == front_default
        and query.min_row is None
        and not re.search(FRONT_WORDS, prose, re.IGNORECASE)
    ):
        query = replace(query, max_row=None)

    if (
        query.min_row == back_default
        and query.max_row is None
        and not re.search(BACK_WORDS, prose, re.IGNORECASE)
    ):
        query = replace(query, min_row=None)

    return query


def _clamp_row(value: object) -> int | None:
    """Row numbers the cabin actually has, or nothing."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(1, min(settings.CABIN_ROWS, value))


def _validate(raw: dict) -> SeatQuery:
    """Clamp and reject model output against the real cabin.

    This is not politeness. A model that answers "near the front" with
    max_row 1000000000000000 -- which a 1.7B model did, before the schema
    bounded it -- must not reach the ORM as a filter that matches everything.
    """
    intent = raw.get('intent')
    if intent not in ('find', 'count', 'list', 'status'):
        intent = 'find'

    position = raw.get('position')
    if position not in POSITIONS:
        position = None

    toward = raw.get('toward')
    if toward not in ('front', 'back'):
        toward = None

    side = raw.get('side')
    if side not in ('left', 'right'):
        side = None

    party = raw.get('party')
    if isinstance(party, bool) or not isinstance(party, int):
        party = None
    else:
        party = max(1, min(settings.MAX_PARTY_SIZE, party))

    # `is True` rather than truthiness: a model that answers "yes" or 1 has not
    # answered the boolean it was asked for.
    is_random = raw.get('random') is True
    together = raw.get('together') is True

    min_row = _clamp_row(raw.get('min_row'))
    max_row = _clamp_row(raw.get('max_row'))

    # A reversed range matches nothing, which reads as "no seats like that"
    # when the passenger simply said "between 20 and 10".
    if min_row is not None and max_row is not None and min_row > max_row:
        min_row, max_row = max_row, min_row

    return SeatQuery(
        intent=intent,
        position=position,
        min_row=min_row,
        max_row=max_row,
        toward=toward,
        side=side,
        party=party,
        together=together,
        is_random=is_random,
    )
