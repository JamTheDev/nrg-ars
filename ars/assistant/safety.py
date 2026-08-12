"""Screening a message before it is treated as a request.

The deterministic boundary is still the one that matters: the model cannot
emit SQL, and its output is a validated SeatQuery, so an injection can at worst
produce a strange filter. This is the product layer on top of that -- someone
typing instructions at the kiosk gets a polite refusal rather than a seat map
and a shrug.

Three layers, cheapest first: phrases with no innocent reading are refused
outright, ordinary seat talk is allowed outright, and only what neither
recognises is put to the model. That keeps a second of latency off every
normal query and avoids the model's habit of calling "anything at the back" an
attack.

It fails open. If the model cannot be reached the message is allowed through,
because the boundary below it does not depend on this check, and a screening
step that takes the kiosk down when Ollama restarts is worse than the attack.
"""

from __future__ import annotations

import re

from assistant import prompts, providers

# Phrases with no innocent reading at a seat kiosk. Refused without asking the
# model, because these are the cases a model is least needed for.
MANIPULATION = re.compile(
    r'ignore\s+(all\s+|any\s+)?(previous|prior|your|the)\s+(instructions|rules|prompt)'
    r'|system\s+prompt'
    r'|you\s+are\s+now\b|pretend\s+(to\s+be|you)|act\s+as\s+(a|an)\b'
    r'|jailbreak|disregard\s+(all|your|previous)'
    r'|<\s*script|javascript:'
    r'|\b(drop|delete|update|insert|select)\b[^\n]*\b(table|from|into|database)\b'
    r'|--\s*$|;\s*--',
    re.IGNORECASE,
)

# Ordinary seat talk. Allowed without asking the model, which spends a second
# to answer and calls "anything at the back" manipulation often enough to
# matter.
SEAT_TALK = re.compile(
    r'\b(seat|seats|row|rows|window|aisle|middle|centre|center|front|back|rear'
    r'|left|right|side|section|available|free|full|empty|taken|booked|flight'
    r'|depart|leave|people|passenger|passengers|together|family|couple|anything'
    r'|anywhere'
    r'|random|surprise)\b',
    re.IGNORECASE,
)


class UnsafeRequest(Exception):
    """The message looked like an attempt to manipulate the assistant."""


def check(prose: str) -> None:
    """Raise UnsafeRequest if `prose` reads as an injection attempt."""
    text = (prose or '').strip()
    if not text:
        return

    if MANIPULATION.search(text):
        raise UnsafeRequest(text)

    # Ordinary seat talk needs no adjudication, and asking anyway costs a
    # second per query and produces false refusals.
    if SEAT_TALK.search(text):
        return

    try:
        verdict = providers.extract_json(
            text, prompts.INJECTION_SCHEMA, system=prompts.INJECTION_SYSTEM
        )
    except providers.ProviderUnavailable:
        return  # fail open; see the module docstring

    # A topic label, not a safety judgement. Asked "is this unsafe?" the model
    # answered yes to everything including "window seat near the front"; asked
    # "what is this about?" it sorts kiosk talk from everything else reliably.
    if verdict.get('topic') == 'other':
        raise UnsafeRequest(text)
