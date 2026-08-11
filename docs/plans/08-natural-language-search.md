# Plan — Natural-language seat search

Status: **proposed**, not started.
Target branch: `feature/nl-seat-search` → PR into `dev`.

Implements ARCHITECTURE.md §7, which is currently designed in full and built as
four modules of `NotImplementedError`.

---

## 1. What exists today

```
assistant/
├── schema.py       SeatQuery dataclass + JSON schema   ← real
├── apps.py         connection_created → load sqlite-vec ← real
├── providers.py    embed(), extract_json()             ← raise NotImplementedError
├── extraction.py   extract(), _validate()              ← raise NotImplementedError
└── vocabulary.py   resolve(), reindex()                ← raise NotImplementedError
```

Nothing imports the app, there is no view, no URL, and `assistant/migrations/`
holds only `__init__.py` — so the `vec0` virtual table does not exist either.

### The environment is further along than the doc says

Verified on this machine:

| Prerequisite | State |
|---|---|
| Ollama server | **running** |
| `qwen3:14b` (extraction) | **installed** |
| `nomic-embed-text` (embedding) | **installed** — ARCHITECTURE §7 still says it is not |
| `sqlite_vec` package | installed |
| `sqlite3.enable_load_extension` | available |

So the "not yet installed" note in §7 is stale and should be corrected as part
of this work. Nothing blocks the build.

---

## 2. The design being implemented, restated

This is **not** RAG over seat rows, and building it that way would make it
wrong: similarity cannot enforce "row under 10", availability changes on every
booking so an index is stale immediately, and the database already answers
these questions exactly.

**Facts go through the ORM. Vector search is used only where meaning is
genuinely fuzzy** — mapping loose phrasing onto the system's own vocabulary.

```
"window seat near the front"
        │
        ▼
qwen3:14b, constrained by SeatQuery's JSON schema
        │
        ├── unknown phrase ──► sqlite-vec concept lookup ──┐
        ▼                                                  │
SeatQuery(position='window', max_row=10)  ◄────────────────┘
        │
        ▼
Django ORM over the seat catalog and this flight's bookings
        │
        ▼
ranked free seats ──► the existing seat-map fragment, matches pre-selected
```

**The LLM never emits SQL and never sees the database.** Its only output is a
validated `SeatQuery`. That is the security boundary: a prompt injection can at
worst produce a strange-but-valid filter, never arbitrary SQL.

---

## 3. Where it plugs into what already exists

The seat map already has everything needed to *show* a result:

- `GET /flights/<id>/map/` returns the cabin fragment with chosen seats pressed
- the client adopts whatever the server rendered as pressed, after any swap
- the camera flies to the selection

So natural-language search is **another way to drive the same endpoint**, not a
new rendering path. `?q=window+seat+near+the+front` sits beside `?party=` and
`?keep=`, and everything downstream is already built and verified.

That is the whole integration. No new template, no new selection mechanism, no
new camera behaviour.

### UI

A single text input in the seat-map header:

```
[ window seat near the front            ] ⏎
```

`hx-get` to the same `seat-map` route, `hx-target="#seat-map"`. On success the
matching seats come back pressed and the camera flies to them. On failure the
map returns unchanged with a banner.

---

## 4. Modules to fill in

### `providers.py` — the only place that talks to Ollama

```python
def embed(text: str) -> list[float]        # nomic-embed-text, 768 dims
def extract_json(prompt: str, schema: dict) -> dict   # qwen3:14b, format=schema
```

Both take a hard timeout (5s) and raise `ProviderUnavailable` on anything —
connection refused, timeout, malformed response. One narrow boundary, so
swapping Ollama for a hosted API later touches one file.

### `extraction.py` — prose to a validated filter

`extract(prose)` calls `extract_json` with `SEAT_QUERY_JSON_SCHEMA` as
`format=`, so decoding is constrained to the shape rather than parsed hopefully
out of prose. Then `_validate()`:

- drops fields the model invented
- clamps `min_row`/`max_row` into `1..CABIN_ROWS`, and swaps them if reversed
- rejects a `position` outside `window|aisle|middle`
- upper-cases `destination`, and discards it if it is not three letters

**Validation is not optional politeness.** A model that returns `max_row: 300`
must not reach the ORM as a filter that silently matches everything.

### `vocabulary.py` — the one legitimate use of an index

A small curated corpus of concept phrases, each mapping to a field and value:

| Phrase | → |
|---|---|
| "by the porthole", "with a view" | `position=window` |
| "up front", "near the cockpit" | `max_row=10` |
| "near the loo", "at the back" | `min_row=25` |

`reindex()` embeds each phrase and writes it to the `vec0` table.
`resolve(phrase)` embeds the unknown phrase and returns the nearest concept
above a similarity floor, or `None`.

This corpus is **small, static and authored** — it changes when the vocabulary
changes, never when someone books a seat. That is what makes an index
appropriate here and inappropriate for the seats themselves.

### Migration

`assistant/migrations/0001_initial.py`:

- a normal `Concept` model (phrase, field, value) — ordinary ORM
- `migrations.RunSQL` creating the `vec0` virtual table, which has no Django
  model mapped onto it and is read through raw SQL

```sql
CREATE VIRTUAL TABLE assistant_concept_vec USING vec0(
    concept_id INTEGER PRIMARY KEY,
    embedding  FLOAT[768]
);
```

The dimension is baked in. **Changing the embedding model means dropping and
rebuilding the table** — a migration, not a config edit.

### `selectors.search_seats(flight, query: SeatQuery)`

Turns the validated filter into seats, reusing `seat_map()`:

- `position` → column sets derived from `CABIN_COLUMNS`, not stored flags:
  window `{A,F}`, aisle `{C,D}`, middle `{B,E}`
- `min_row`/`max_row` → row bounds
- ranked by cabin order, capped at `MAX_PARTY_SIZE`

---

## 5. Failure behaviour

Ollama is a separate process that may be stopped. **The three core
requirements must never depend on it.**

| Condition | Response |
|---|---|
| Ollama unreachable or slow (>5s) | Map returns unchanged, banner: smart search unavailable |
| Extraction returns nothing usable | Banner asking for a rephrase, map unchanged |
| Valid query, no matching free seat | Banner saying so, map unchanged |
| Valid query with matches | Seats pressed, camera flies to them |

Every path returns **200 with the map**, for the same reason booking failures
do: htmx does not swap error responses.

---

## 6. Work breakdown

| # | Step | Independently mergeable? |
|---|---|---|
| 1 | `providers.py` against the live Ollama, with timeouts | yes |
| 2 | `extraction.py` + validation, provider mocked in tests | yes |
| 3 | `Concept` model, `vec0` migration, `reindex` command | yes |
| 4 | `vocabulary.py` resolve, over the seeded corpus | yes |
| 5 | `selectors.search_seats` | yes |
| 6 | `?q=` on the `seat-map` route + the header input | last |
| 7 | ARCHITECTURE §7 corrections (models installed, status) | with 6 |

Steps 1–5 are all testable without a browser. Step 6 is small precisely
because the seat map already does the hard parts.

---

## 7. Tests

**No test may require Ollama to be running.** The provider is mocked at the
`providers.py` boundary throughout, which is exactly why that boundary is one
file.

- **Extraction**: fixed prose → expected `SeatQuery`, with the model response
  stubbed. Table-driven over the phrasings the corpus is meant to cover.
- **Validation**: `max_row: 300` clamps; reversed bounds swap; invented fields
  vanish; a bogus `position` is dropped rather than passed through.
- **Vocabulary**: `resolve()` against a stubbed embedding returns the nearest
  concept; a nonsense phrase below the floor returns `None`.
- **Search**: seeded cabin, `SeatQuery(position='window', max_row=10)` returns
  exactly the expected designations; query count fixed.
- **Degradation**: provider raising `ProviderUnavailable` → the view still
  returns the map, 200, with the unavailable banner. This is the test that
  protects the core requirements.
- **Injection**: prose like `"; DROP TABLE reservations_booking; --"` produces
  either a harmless filter or nothing, and the table still exists afterwards.

One **manual, non-CI** check against the live Ollama, recorded in the PR: a
handful of real phrasings and what they extracted. Model behaviour is not
something to assert in a test suite, but it is something to have looked at.

---

## 8. Risks

| Risk | Handling |
|---|---|
| A 14B model is slow enough to feel broken | 5s timeout, and the input says "smart search" so expectations are set; measure real latency in step 1 and reconsider the model if it is bad |
| Constrained decoding still returns nonsense | Validation clamps rather than trusts; unusable results degrade to a rephrase prompt |
| The vocabulary corpus is guesswork | It is authored and versioned; add phrases when a real one fails, and the reindex is one command |
| sqlite-vec unavailable on a contributor's Python | Already handled: `apps.py` skips loading for non-SQLite, and README documents the interpreter requirement |
| Scope creep into "chat with your booking" | Out of scope, explicitly. This maps a phrase onto a filter and stops |

---

## 9. Open questions

1. **Does this feature belong in the deliverable at all?** All three core
   requirements will be met without it. It is the most interesting part of the
   architecture and the least required. Proposal: build it after
   [07-print-flight](07-print-flight.md), and only if there is appetite.
2. **Should a search pre-select the matches, or only highlight them?**
   Pre-selecting reuses everything already built. Highlighting needs new state
   and new CSS. Proposal: pre-select, capped at the party size.
3. **Flight-level phrasing** — `SeatQuery` carries `flight_number` and
   `destination`, which imply searching *across* flights from the flight list,
   not only within one cabin. Proposal: keep this release inside one flight,
   and leave those two fields unused rather than removing them.
