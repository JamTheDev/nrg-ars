# CLAUDE.md

Working rules for this repository. See [ARCHITECTURE.md](ARCHITECTURE.md) for the
technical design and [README.md](README.md) for setup.

---

## 1. Never push to `main`

Pushing to `main` is forbidden. `main` only ever advances through a merged pull
request that the repository owner approves.

This holds even when a change is trivial, urgent, or a one-line fix. The only
exception is an explicit, in-the-moment instruction from the owner saying to push
to `main` directly. A past approval does not carry over to a later change.

## 2. All work happens on a feature branch

```
feature/<short-name>  ──PR──>  dev  ──PR──>  main
```

- Branch off `dev`, never off `main`.
- Never commit or push directly to `dev` either. `dev` is protected exactly like
  `main` and advances only by merged pull request.
- When the change is done, push the feature branch and open a PR targeting `dev`.
- Promoting `dev` to `main` is a separate PR.

This applies to every change regardless of size — a typo fix follows the same
path as a new feature.

**Branch naming:** `feature/<short-kebab-name>`, e.g. `feature/seat-map-view`.

## 3. Every code change must pass ruff

```bash
uvx ruff check .
```

Must report **"All checks passed!"** before a PR is opened. Configuration lives
in `[tool.ruff]` in `pyproject.toml` — rule set `E`, `F`, `I`, `UP`, `B` at a
100-character line length, with migrations excluded.

Fix the underlying problem rather than silencing the rule. If a `# noqa` is
genuinely warranted, it must name the specific rule (`# noqa: E501`) and carry a
comment explaining why.

## 4. Keep files modular — 500 lines maximum

No hand-written **Python** file may exceed 500 lines. On approaching the limit,
split by responsibility rather than moving code to an arbitrary second file:

- models → one module per aggregate, or a `models/` package
- views → split by resource
- business rules → `services.py` (writes) and `selectors.py` (reads)

**Scope:** Python source only. Markdown, HTML templates, generated CSS, and
`uv.lock` are exempt — `ARCHITECTURE.md` deliberately runs longer.

---

## Enforcement

These rules are enforced automatically, not just documented. Hooks are configured
in [.claude/settings.json](.claude/settings.json):

| Hook | Event | Effect |
|---|---|---|
| [block_protected_push.py](.claude/hooks/block_protected_push.py) | `PreToolUse` on `Bash` | Denies any `git push` targeting `main` or `dev`, including a bare `git push` while one of them is checked out, and pushes chained behind `&&` |
| [check_python.py](.claude/hooks/check_python.py) | `PostToolUse` on `Write`/`Edit` | Runs `uvx ruff check` on the edited `.py` file and rejects it over 500 lines |

Both are written in Python rather than shell because `jq` is not installed on
this machine — a hook that fails silently is worse than no hook.

The push guard deliberately does not match branch names that merely *contain*
`main` or `dev`, so `feature/dev-tools` pushes normally.

---

## Project Conventions

- **Dependencies:** `uv add <package>`. Never `pip install` — it leaves
  `pyproject.toml` and `uv.lock` out of sync with the environment.
- **Secrets:** `SECRET_KEY` and anything like it come from `.env` via
  python-dotenv. `.env` is gitignored and must never be committed. Add new
  variables to `.env.example` with an empty value.
- **Database:** `db.sqlite3` is gitignored. Schema changes ship as migrations.
- **Tailwind:** edit `ars/static/src/input.css` and rebuild; never hand-edit the
  compiled `ars/static/css/tailwind.css`.
- **Business logic** belongs in `services.py`/`selectors.py`, not in views, so
  the htmx views and the management commands share one implementation.
