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
FRONT_WORDS = r'\b(front|forward|nose|ahead|beginning)\b'
BACK_WORDS = r'\b(back|rear|tail|behind|end)\b'

# The examples matter as much as the rules: without them a small model answers
# "near the front" with a row number in the trillions. See the plan doc.
SYSTEM_PROMPT = """\
Convert a passenger's seat request into filters for a cabin of {rows} rows, \
columns {first}-{last}.
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
random: true when the passenger does not mind which of the matching seats they \
get ("random", "any seat", "surprise me", "you pick"), otherwise false. random \
never cancels a constraint: if they also say where they want to sit, keep the \
rows and position AND set random true.
Always output all six keys. Use null for anything the passenger did not ask for.
Examples:
"window near the front" -> \
{{"position":"window","min_row":null,"max_row":{front},"toward":null,"side":null,"random":false}}
"aisle at the back" -> \
{{"position":"aisle","min_row":{back},"max_row":null,"toward":null,"side":null,"random":false}}
"furthest back window seat" -> \
{{"position":"window","min_row":null,"max_row":null,"toward":"back","side":null,"random":false}}
"seat in row 12" -> \
{{"position":null,"min_row":12,"max_row":12,"toward":null,"side":null,"random":false}}
"middle seat" -> \
{{"position":"middle","min_row":null,"max_row":null,"toward":null,"side":null,"random":false}}
"a random seat in the middle of the plane" -> \
{{"position":null,"min_row":{mid_start},"max_row":{mid_end},"toward":null,"side":null,"random":true}}
"middle of the aircraft near a window" -> \
{{"position":"window","min_row":{mid_start},"max_row":{mid_end},"toward":null,"side":null,"random":false}}
"surprise me" -> \
{{"position":null,"min_row":null,"max_row":null,"toward":null,"side":null,"random":true}}
"random seat at the back of the plane" -> \
{{"position":null,"min_row":{back},"max_row":null,"toward":null,"side":null,\
"random":true}}
"any window seat up front" -> \
{{"position":"window","min_row":null,"max_row":{front},"toward":null,"side":null,\
"random":true}}
"anything" -> \
{{"position":null,"min_row":null,"max_row":null,"toward":null,"side":null,"random":false}}
"aisle seat on the right" -> \
{{"position":"aisle","min_row":null,"max_row":null,"toward":null,"side":"right",\
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
    query = _drop_unsaid_band(_drop_unsaid_side(_validate(raw), text), text)

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
    position = raw.get('position')
    if position not in POSITIONS:
        position = None

    toward = raw.get('toward')
    if toward not in ('front', 'back'):
        toward = None

    side = raw.get('side')
    if side not in ('left', 'right'):
        side = None

    # `is True` rather than truthiness: a model that answers "yes" or 1 has not
    # answered the boolean it was asked for.
    is_random = raw.get('random') is True

    min_row = _clamp_row(raw.get('min_row'))
    max_row = _clamp_row(raw.get('max_row'))

    # A reversed range matches nothing, which reads as "no seats like that"
    # when the passenger simply said "between 20 and 10".
    if min_row is not None and max_row is not None and min_row > max_row:
        min_row, max_row = max_row, min_row

    return SeatQuery(
        position=position,
        min_row=min_row,
        max_row=max_row,
        toward=toward,
        side=side,
        is_random=is_random,
    )
