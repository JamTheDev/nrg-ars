"""Vector lookup over a small curated concept vocabulary.

The ONLY place vector search is used. Availability facts come from the ORM --
embedding booking rows would be stale on every write and cannot enforce
numeric constraints. See the design note in ARCHITECTURE.md section 7.

Maps loose phrasing ("by the porthole", "up front") onto canonical SeatQuery
field values. The corpus is authored and static.
"""

from __future__ import annotations

from django.db import connection

from assistant import providers
from assistant.models import Concept

VEC_TABLE = 'assistant_concept_vec'

# vec0 measures L2 distance by default, not cosine, so this is not a 0-1
# similarity. Calibrated against nomic-embed-text on the corpus below:
#
#   genuine rephrasing      0.60 - 0.65   ("i want to get off the plane first")
#   related but wrong       0.91 - 0.98
#   nonsense                1.09 +        ("purple monkey dishwasher")
#
# 0.80 sits in the gap. Re-measure if the embedding model changes; the numbers
# above are properties of that model, not of the phrases.
MAX_DISTANCE = 0.80

# The authored corpus. Seeded by `reindex`, and the place to add a phrase when
# a real passenger's wording fails.
CONCEPTS: list[tuple[str, str, str]] = [
    ('by the porthole', 'position', 'window'),
    ('next to the window with a view outside', 'position', 'window'),
    ('on the corridor so I can get out easily', 'position', 'aisle'),
    ('next to the walkway', 'position', 'aisle'),
    ('between two other passengers', 'position', 'middle'),
    ('up front near the cockpit', 'max_row', '10'),
    ('first off the plane when we land', 'max_row', '5'),
    ('at the very back near the toilets', 'min_row', '25'),
    ('towards the rear of the aircraft', 'min_row', '21'),
    ('over the wings where it is smoothest', 'min_row', '11'),
]


def resolve(phrase: str) -> tuple[str, str] | None:
    """Return (field_name, value) for a phrase, or None if nothing matches.

    Queries the vec0 virtual table with raw SQL -- it sits outside the ORM.
    """
    text = (phrase or '').strip()
    if not text or not Concept.objects.exists():
        return None

    embedding = providers.embed(text)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT concept_id, distance
            FROM {VEC_TABLE}
            WHERE embedding MATCH %s AND k = 1
            ORDER BY distance
            """,
            [_serialize(embedding)],
        )
        row = cursor.fetchone()

    if not row:
        return None

    concept_id, distance = row
    if distance > MAX_DISTANCE:
        return None

    concept = Concept.objects.filter(pk=concept_id).first()
    if concept is None:
        return None

    value: str | int = concept.value
    if concept.field in ('min_row', 'max_row'):
        value = int(concept.value)
    return concept.field, value


def reindex() -> int:
    """Rebuild the concept index. Returns the number of concepts indexed.

    Must be re-run if EMBEDDING_MODEL changes, since the vec0 table's dimension
    is fixed at creation.
    """
    Concept.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM {VEC_TABLE}')  # noqa: S608 - constant table name

    for phrase, field, value in CONCEPTS:
        concept = Concept.objects.create(phrase=phrase, field=field, value=value)
        embedding = providers.embed(phrase)
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {VEC_TABLE} (concept_id, embedding) VALUES (%s, %s)',
                [concept.pk, _serialize(embedding)],
            )

    return len(CONCEPTS)


def _serialize(vector: list[float]) -> bytes:
    """sqlite-vec takes vectors as raw little-endian float32."""
    import struct

    return struct.pack(f'{len(vector)}f', *vector)
