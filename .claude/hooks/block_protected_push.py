#!/usr/bin/env python3
"""PreToolUse/Bash guard: refuse pushes to protected branches.

Enforces rules 1 and 2 of CLAUDE.md -- work happens on feature branches and
reaches dev or main only through a pull request.

Written in Python rather than shell because jq is not installed on this
machine, and a hook that fails silently is worse than no hook at all.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

PROTECTED = ('main', 'dev')

IS_PUSH = re.compile(r'(^|[;&|]|\s)git\s+push\b')
# Requires a separator before the name so "feature/dev-tools" is not caught.
EXPLICIT_TARGET = re.compile(r'[\s:](main|dev)(\s|$)')


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


def current_branch() -> str:
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = payload.get('tool_input', {}).get('command', '')
    if not IS_PUSH.search(command):
        sys.exit(0)

    if EXPLICIT_TARGET.search(command):
        deny(
            'Blocked by CLAUDE.md: main and dev are protected branches and must never be '
            'pushed to directly. Push a feature branch and open a pull request instead.'
        )

    # A bare "git push" pushes whatever branch is checked out.
    branch = current_branch()
    if branch in PROTECTED:
        deny(
            f"Blocked by CLAUDE.md: the current branch is '{branch}', which is protected. "
            'Create a feature branch and open a pull request instead.'
        )

    sys.exit(0)


if __name__ == '__main__':
    main()
