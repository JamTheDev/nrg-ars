#!/usr/bin/env python3
"""PreToolUse/Bash guard: keep AI attribution out of git history.

Enforces rule 5 of CLAUDE.md -- commit messages and pull request descriptions
carry no `Co-Authored-By: Claude ...` trailer and no "Generated with Claude
Code" footer. The git history is the repository owner's authorship record.

Only commands that *write* a commit or a pull request are inspected, so
reading history (`git log | grep Co-Authored-By`) is left alone.

Written in Python rather than shell because jq is not installed on this
machine, and a hook that fails silently is worse than no hook at all.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

# Commands that put text into history or onto a pull request.
WRITES_HISTORY = re.compile(
    r"""(^|[;&|]|\s)
        ( git \s+ (commit|merge|tag)\b
        | gh \s+ pr \s+ (create|edit)\b
        | gh \s+ (api|issue) \b .* \b (pulls|--body)
        )""",
    re.VERBOSE,
)

# Matches the attribution as it is actually emitted, not as it is discussed.
# A real trailer carries an email in angle brackets and the real footer carries
# its emoji, markdown link, or URL; prose quoting either one carries neither.
# This rule has to be describable in a commit message or a PR body without
# tripping its own guard -- both earlier drafts of this hook failed that test.
ATTRIBUTION = re.compile(
    r"""co-authored-by: \s* claude [^\n]{0,40} [<@]
        | 🤖 \s* generated \s+ with
        | generated \s+ with \s+ \[claude \s+ code\]
        | claude\.com/claude-code""",
    re.VERBOSE | re.IGNORECASE,
)

# Flags whose value is a path holding the message or PR body.
FILE_FLAGS = ('-F', '--file', '--body-file', '-f', '--message-file')

REASON = (
    'Blocked by CLAUDE.md rule 5: commit messages and pull request bodies must not carry AI '
    'attribution. Remove the "Co-Authored-By: Claude ..." trailer and any "Generated with '
    'Claude Code" footer, then run the command again.'
)


def deny(reason: str) -> None:
    json.dump(
        {
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def referenced_files(command: str) -> list[Path]:
    """Paths passed via -F/--body-file, including gh's `-F body=@path` form.

    A heredoc makes the command unparseable for shlex; that is harmless, since
    a heredoc body is already part of the command text and gets scanned there.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []

    paths = []
    for flag, value in zip(tokens, tokens[1:], strict=False):
        if flag not in FILE_FLAGS:
            continue
        candidate = value.split('=@', 1)[-1] if '=@' in value else value
        if candidate == '-':
            continue
        path = Path(candidate)
        if path.is_file():
            paths.append(path)
    return paths


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = payload.get('tool_input', {}).get('command', '')
    if not WRITES_HISTORY.search(command):
        sys.exit(0)

    if ATTRIBUTION.search(command):
        deny(REASON)

    for path in referenced_files(command):
        try:
            content = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if ATTRIBUTION.search(content):
            deny(f'{REASON} (found in {path})')

    sys.exit(0)


if __name__ == '__main__':
    main()
