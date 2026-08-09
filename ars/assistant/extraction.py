"""Natural language -> validated SeatQuery.

Step 1 of the pipeline in ARCHITECTURE.md section 7. Values the model invents
are dropped and out-of-range rows rejected here, before any query runs.
"""

from __future__ import annotations

from assistant.schema import SeatQuery


def extract(prose: str) -> SeatQuery:
    """Turn a user's phrasing into a validated SeatQuery.

    Falls back to vocabulary lookup for phrases the model does not recognise.
    Raises ProviderUnavailable if Ollama is unreachable; callers degrade to the
    ordinary seat map rather than surfacing an error.
    """
    raise NotImplementedError


def _validate(raw: dict) -> SeatQuery:
    """Clamp/reject model output against the real cabin dimensions."""
    raise NotImplementedError
