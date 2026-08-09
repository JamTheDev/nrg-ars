#!/usr/bin/env python3
"""PostToolUse/Write|Edit check: lint Python files and enforce the line cap.

Enforces rules 3 and 4 of CLAUDE.md. Findings are returned as a blocking
reason so they are fixed in the same turn rather than surfacing at commit time.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MAX_LINES = 500


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    raw = payload.get('tool_response', {}).get('filePath') or payload.get(
        'tool_input', {}
    ).get('file_path', '')
    if not raw or not raw.endswith('.py'):
        sys.exit(0)

    path = Path(raw)
    if not path.is_file():
        sys.exit(0)

    problems: list[str] = []

    line_count = len(path.read_text(encoding='utf-8', errors='replace').splitlines())
    if line_count > MAX_LINES:
        problems.append(
            f'{path} is {line_count} lines, over the {MAX_LINES}-line limit in '
            'CLAUDE.md. Split it into modules.'
        )

    try:
        result = subprocess.run(
            ['uvx', 'ruff', 'check', str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append(f'Could not run ruff: {exc}')
    else:
        if result.returncode != 0:
            problems.append(f'ruff check failed:\n{result.stdout}{result.stderr}')

    if problems:
        json.dump({'decision': 'block', 'reason': '\n'.join(problems)}, sys.stdout)

    sys.exit(0)


if __name__ == '__main__':
    main()
