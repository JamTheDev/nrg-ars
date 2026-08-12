"""Boundary around the local Ollama server.

Every model call goes through here so swapping Ollama for a hosted provider
touches this file only. See ARCHITECTURE.md section 7.

It is also the seam the tests mock: nothing in the suite may require Ollama to
be running.
"""

from __future__ import annotations

import json

from django.conf import settings

# Measured on this machine (docs/plans/08-natural-language-search.md):
# qwen3:14b answers correctly but takes 6-7s warm, which reads as broken at a
# kiosk. qwen3:1.7b answers the same prompts in ~1s, and is just as accurate
# once the JSON schema requires its fields and bounds its rows.
GENERATION_MODEL = 'qwen3:1.7b'
EMBEDDING_MODEL = 'nomic-embed-text'  # 768 dims; must match the vec0 table
EMBEDDING_DIMENSIONS = 768
# Generous enough to survive a cold start. Ollama unloads an idle model, and
# the next call pays to load it again -- measured at over 8s, which the first
# version of this file treated as a failure. A warm call takes about a second,
# so this ceiling is only ever reached when something is genuinely wrong.
REQUEST_TIMEOUT_SECONDS = 30

# Keep the model resident between queries, so only the first one pays.
KEEP_ALIVE = '30m'

OLLAMA_HOST = getattr(settings, 'OLLAMA_HOST', 'http://localhost:11434')


class ProviderUnavailable(RuntimeError):
    """Ollama is not reachable. Callers must degrade to the plain seat map."""


def _client():
    # Imported lazily so a missing or broken ollama install degrades like an
    # unreachable server rather than breaking startup for the whole site.
    try:
        from ollama import Client
    except ImportError as exc:  # pragma: no cover - the package is a dependency
        raise ProviderUnavailable('the ollama client is not installed') from exc

    return Client(host=OLLAMA_HOST, timeout=REQUEST_TIMEOUT_SECONDS)


def embed(text: str) -> list[float]:
    """Return the embedding vector for `text`.

    Raises ProviderUnavailable if the Ollama server cannot be reached.
    """
    try:
        response = _client().embed(model=EMBEDDING_MODEL, input=text, keep_alive=KEEP_ALIVE)
        vectors = response['embeddings']
    except ProviderUnavailable:
        raise
    except Exception as exc:
        raise ProviderUnavailable(f'embedding failed: {exc}') from exc

    if not vectors:
        raise ProviderUnavailable('the embedding response was empty')
    return list(vectors[0])


def write(system: str, prompt: str) -> str:
    """Free-text generation, used only to phrase facts we already hold.

    Nothing here is trusted: the caller checks the wording against the facts
    before it reaches a passenger.
    """
    try:
        response = _client().chat(
            model=GENERATION_MODEL,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': prompt},
            ],
            options={'temperature': 0.3},
            think=False,
            keep_alive=KEEP_ALIVE,
        )
        return (response['message']['content'] or '').strip()
    except ProviderUnavailable:
        raise
    except Exception as exc:
        raise ProviderUnavailable(f'phrasing failed: {exc}') from exc


def extract_json(prompt: str, json_schema: dict, system: str = '') -> dict:
    """Run constrained generation, returning JSON conforming to `json_schema`.

    `think=False` is not a tuning knob. qwen3 reasons before answering by
    default, and the same call that returns in about a second without thinking
    was still running after sixty seconds with it.

    Raises ProviderUnavailable if the Ollama server cannot be reached.
    """
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    try:
        response = _client().chat(
            model=GENERATION_MODEL,
            messages=messages,
            format=json_schema,
            options={'temperature': 0},
            think=False,
            keep_alive=KEEP_ALIVE,
        )
        content = response['message']['content']
    except ProviderUnavailable:
        raise
    except Exception as exc:
        raise ProviderUnavailable(f'generation failed: {exc}') from exc

    try:
        decoded = json.loads(content)
    except (TypeError, ValueError) as exc:
        # Constrained decoding should make this impossible; treat it as the
        # provider misbehaving rather than letting it reach the query layer.
        raise ProviderUnavailable('the model returned something that is not JSON') from exc

    if not isinstance(decoded, dict):
        raise ProviderUnavailable('the model returned JSON that is not an object')
    return decoded
