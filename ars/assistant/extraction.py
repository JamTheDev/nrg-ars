"""Natural language -> validated SeatQuery.

Step 1 of the pipeline in ARCHITECTURE.md section 7. Values the model invents
are dropped and out-of-range rows clamped here, before any query runs.
"""

from __future__ import annotations

import re
from dataclasses import replace

from django.conf import settings

from assistant import providers, vocabulary
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

# The examples matter as much as the rules: without them a small model answers
# "near the front" with a row number in the trillions. See the plan doc.
SYSTEM_PROMPT = """\
Convert what a passenger says into filters for a cabin of {rows} rows, \
columns {first}-{last}.
intent: "count" when they are asking how many seats there are ("how many window \
seats are there?", "any aisle seats left?"), "status" when they ask about the \
flight itself ("is it full?", "when does it leave?"), otherwise "find", which \
means pick seats for them.
Row 1 is the front, row {rows} is the back.
position: "window", "aisle", "middle", or null. "middle" means a seat with a \
passenger on each side. It does NOT mean the centre of the aircraft.
"the middle of the plane", "the centre of the cabin", "halfway down" describe \
rows, not a seat type: min_row {mid_start}, max_row {mid_end}, and position \
stays null unless a seat type is also named.
min_row/max_row: integers 1-{rows}, or null. Set both only for an explicit row \
or range, like "row 12" or "rows 5 to 9".
"front" means max_row {front}. "back" means min_row {back}.
toward: "front" or "back" when the passenger wants to be as near that end as \
possible ("furthest back", "as far forward as you can", "the very last row"), \
otherwise null.
side: "left" or "right" ONLY when the passenger says so. Never infer it. \
Facing forward, columns {first}-{mid_col} are the left side, \
{next_col}-{last} the right.
party: how many seats they need, 1-{max_party}, when they say so \
("for 6 people", "seats for the two of us"), otherwise null.
together: true when they want the seats as a group ("one row", "together", \
"next to each other", "for a family"), otherwise false. It is not a seat type: \
never answer it with position "middle".
random: true when the passenger does not mind which of the matching seats they \
get ("random", "any seat", "surprise me", "you pick"), otherwise false. random \
never cancels a constraint: if they also say where they want to sit, keep the \
rows and position AND set random true.
Always output all nine keys. Use null for anything the passenger did not ask for.
Examples:
"window near the front" -> \
{{"intent":"find","position":"window","min_row":null,"max_row":{front},"toward":null,"side":null,"party":null,"together":false,"random":false}}
"aisle at the back" -> \
{{"intent":"find","position":"aisle","min_row":{back},"max_row":null,"toward":null,"side":null,"party":null,"together":false,"random":false}}
"furthest back window seat" -> \
{{"intent":"find","position":"window","min_row":null,"max_row":null,"toward":"back","side":null,"party":null,"together":false,"random":false}}
"seat in row 12" -> \
{{"intent":"find","position":null,"min_row":12,"max_row":12,"toward":null,"side":null,"party":null,"together":false,"random":false}}
"middle seat" -> \
{{"intent":"find","position":"middle","min_row":null,"max_row":null,"toward":null,"side":null,"party":null,"together":false,"random":false}}
"a random seat in the middle of the plane" -> \
{{"intent":"find","position":null,"min_row":{mid_start},"max_row":{mid_end},"toward":null,"side":null,"party":null,"together":false,"random":true}}
"middle of the aircraft near a window" -> \
{{"intent":"find","position":"window","min_row":{mid_start},"max_row":{mid_end},"toward":null,"side":null,"party":null,"together":false,"random":false}}
"surprise me" -> \
{{"intent":"find","position":null,"min_row":null,"max_row":null,"toward":null,"side":null,"party":null,"together":false,"random":true}}
"random seat at the back of the plane" -> \
{{"intent":"find","position":null,"min_row":{back},"max_row":null,"toward":null,"side":null,"party":null,"together":false,\
"random":true}}
"any window seat up front" -> \
{{"intent":"find","position":"window","min_row":null,"max_row":{front},"toward":null,"side":null,"party":null,"together":false,\
"random":true}}
"anything" -> \
{{"intent":"find","position":null,"min_row":null,"max_row":null,"toward":null,\
"side":null,"party":null,"together":false,"random":false}}
"how many seats are available?" -> \
{{"intent":"count","position":null,"min_row":null,"max_row":null,"toward":null,\
"side":null,"party":null,"together":false,"random":false}}
"how many window seats are there?" -> \
{{"intent":"count","position":"window","min_row":null,"max_row":null,"toward":null,\
"side":null,"party":null,"together":false,"random":false}}
"is this flight full?" -> \
{{"intent":"status","position":null,"min_row":null,"max_row":null,"toward":null,\
"side":null,"party":null,"together":false,"random":false}}
"window seats for 6 people" -> \
{{"intent":"find","position":"window","min_row":null,"max_row":null,"toward":null,"side":null,\
"party":6,"together":false,"random":false}}
"6 seats for a family in one row" -> \
{{"intent":"find","position":null,"min_row":null,"max_row":null,"toward":null,"side":null,\
"party":6,"together":true,"random":false}}
"aisle seat on the right" -> \
{{"intent":"find","position":"aisle","min_row":null,"max_row":null,"toward":null,"side":"right","party":null,\
"random":false}}\
"""


def system_prompt() -> str:
    rows = settings.CABIN_ROWS
    columns = settings.CABIN_COLUMNS
    return SYSTEM_PROMPT.format(
        rows=rows,
        first=columns[0],
        last=columns[-1],
        front=max(1, rows // 3),
        back=rows - rows // 3 + 1,
        mid_start=rows // 3 + 1,
        mid_end=rows - rows // 3,
        mid_col=columns[len(columns) // 2 - 1],
        next_col=columns[len(columns) // 2],
        max_party=settings.MAX_PARTY_SIZE,
    )


def extract(prose: str) -> SeatQuery:
    """Turn a passenger's phrasing into a validated SeatQuery.

    Raises ProviderUnavailable if Ollama is unreachable; callers degrade to the
    ordinary seat map rather than surfacing an error.
    """
    text = (prose or '').strip()
    if not text:
        return SeatQuery()

    raw = providers.extract_json(text, seat_query_json_schema(), system=system_prompt())
    query = _validate(raw)
    query = _drop_unsaid_side(query, text)
    query = _drop_unsaid_band(query, text)
    query = _drop_unsaid_toward(query, text)
    query = _drop_unsaid_position(query, text)
    query = _drop_unsaid_together(query, text)
    query = _settle_intent(query, text)
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


def _settle_intent(query: SeatQuery, prose: str) -> SeatQuery:
    """Decide question from request by how it was phrased.

    Getting this backwards is the rudest failure available: answering "give me
    a window seat" with a head count, or selecting a seat for someone who only
    asked how many were left. Both directions are corrected, because the words
    are a better signal than the model's own classification.
    """
    asked = re.search(QUESTION_WORDS, prose, re.IGNORECASE) is not None

    if asked and query.intent == 'find':
        return replace(query, intent='count')
    if not asked and query.intent in ('count', 'status'):
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
    if intent not in ('find', 'count', 'status'):
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
