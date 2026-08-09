"""Boundary around the local Ollama server.

Every model call goes through here so swapping Ollama for a hosted provider
touches this file only. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

GENERATION_MODEL = 'qwen3:14b'
EMBEDDING_MODEL = 'nomic-embed-text'   # 768 dims; must match the vec0 table
EMBEDDING_DIMENSIONS = 768
REQUEST_TIMEOUT_SECONDS = 5


class ProviderUnavailable(RuntimeError):
    """Ollama is not reachable. Callers must degrade to the plain seat map."""


def embed(text: str) -> list[float]:
    """Return the embedding vector for `text`.

    Raises ProviderUnavailable if the Ollama server cannot be reached.
    """
    raise NotImplementedError


def extract_json(prompt: str, json_schema: dict) -> dict:
    """Run constrained generation, returning JSON conforming to `json_schema`.

    Raises ProviderUnavailable if the Ollama server cannot be reached.
    """
    raise NotImplementedError
