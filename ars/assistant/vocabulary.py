"""Vector lookup over a small curated concept vocabulary.

The ONLY place vector search is used. Availability facts come from the ORM --
embedding booking rows would be stale on every write and cannot enforce
numeric constraints. See the design note in ARCHITECTURE.md section 7.

Maps loose phrasing ("by the porthole", "up front") onto canonical SeatQuery
field values. The corpus is authored and static.
"""

from __future__ import annotations

VEC_TABLE = 'assistant_concept_vec'


def resolve(phrase: str) -> tuple[str, str] | None:
    """Return (field_name, value) for a phrase, or None if nothing matches.

    Queries the vec0 virtual table with raw SQL -- it sits outside the ORM.
    """
    raise NotImplementedError


def reindex() -> int:
    """Rebuild the concept index. Returns the number of concepts indexed.

    Must be re-run if EMBEDDING_MODEL changes, since the vec0 table's dimension
    is fixed at creation.
    """
    raise NotImplementedError
