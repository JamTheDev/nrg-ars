"""Natural language -> validated SeatQuery.

Step 1 of the pipeline in ARCHITECTURE.md section 7. Values the model invents
are dropped and out-of-range rows clamped here, before any query runs.
"""

from __future__ import annotations

from django.conf import settings

from assistant import providers, vocabulary
from assistant.schema import SeatQuery, seat_query_json_schema

POSITIONS = ('window', 'aisle', 'middle')

# The examples matter as much as the rules: without them a small model answers
# "near the front" with a row number in the trillions. See the plan doc.
SYSTEM_PROMPT = """\
Convert a passenger's seat request into filters for a cabin of {rows} rows, \
columns {first}-{last}.
Row 1 is the front, row {rows} is the back.
position: "window", "aisle", "middle", or null.
min_row/max_row: integers 1-{rows}, or null. "front" means max_row {front}. \
"back" means min_row {back}. "row 12" means min_row 12 and max_row 12.
Always output all three keys. Use null for anything the passenger did not ask for.
Examples:
"window near the front" -> {{"position":"window","min_row":null,"max_row":{front}}}
"aisle at the back" -> {{"position":"aisle","min_row":{back},"max_row":null}}
"anything" -> {{"position":null,"min_row":null,"max_row":null}}\
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

    # Only ask the vocabulary index about phrasing the model made nothing of.
    # It costs an embedding call, so it is a fallback, not a step.
    if query.is_empty:
        resolved = vocabulary.resolve(text)
        if resolved:
            field, value = resolved
            query = _validate({**raw, field: value})

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

    min_row = _clamp_row(raw.get('min_row'))
    max_row = _clamp_row(raw.get('max_row'))

    # A reversed range matches nothing, which reads as "no seats like that"
    # when the passenger simply said "between 20 and 10".
    if min_row is not None and max_row is not None and min_row > max_row:
        min_row, max_row = max_row, min_row

    return SeatQuery(position=position, min_row=min_row, max_row=max_row)
