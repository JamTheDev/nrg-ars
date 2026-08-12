# Natural-language seat search

Status: **built**, PR #12. Written as a plan, rewritten as an as-built record
once the thing existed and started being wrong in instructive ways.

Type *"window seats for 6 people"* into the seat map and six window seats come
back selected, with the camera flown to them.

---

## 1. What it does

One phrase in, a seat selection out. It is a **searcher, not a chatbot**: there
is no dialogue state, no memory between queries, and no conversation to
maintain.

Phrases it handles today, each verified by hand against the live model:

| Phrase | Result |
|---|---|
| `window seat near the front` | `1A` |
| `farthest back seat near the aisle on the right` | `30D` |
| `random seat in the middle of the plane` | a different seat in rows 11–20 each time |
| `window seats for 6 people` | `1A 1F 2A 2F 3A 3F`, stepper set to 6 |
| `6 seats for a family in one row` | a whole free row |
| `by the porthole` | `1A` — resolved through the vector vocabulary |

---

## 2. How it works

```
prose
  │
  ▼
qwen3:1.7b, decoding constrained to the SeatQuery JSON schema      (~1s)
  │
  ▼
_validate()      clamp rows, drop invented fields and enum values
  │
  ▼
guards           drop claims the passenger never made               (§4)
  │
  ├── query still empty? ──► sqlite-vec vocabulary lookup           (~1.4s)
  ▼
SeatQuery  →  selectors.search_seats()  →  the same seat-map fragment
                                            everything else uses
```

**It is one more parameter, not a new feature surface.** `?q=` sits beside
`?party=` and `?keep=` on the existing `seat-map` endpoint, so a match arrives
as a selection, the client adopts it exactly as it adopts a party pick, and the
camera focuses it. No new template, no second selection mechanism.

**The LLM never emits SQL and never sees the database.** Its only output is a
`SeatQuery`. That is the security boundary: a prompt injection can at worst
produce a strange-but-valid filter. There is a test asserting the database
survives `'; DROP TABLE reservations_booking; --` and still books afterwards.

### Where vector search is, and is not

Availability comes from the ORM. Similarity cannot enforce "row under 10", and
an index over seats would be stale on every booking. The embedded corpus is
**authored, static, and consulted only for phrasing the model made nothing of**
— it costs an embedding call, so it is a fallback, not a pipeline step.

---

## 3. The problem log

Everything below was found by using the feature, not by testing it. They are
recorded because the fixes are not obvious from the code alone, and because the
same mistakes are available to anyone who changes this.

### 3.1 Thinking models: a 60-second answer

**Symptom.** Extraction never returned; the request timed out.

**Cause.** qwen3 reasons before answering by default. The same call that
returns in about a second with `think=False` was still running after sixty.

**Fix.** `think=False` on every call. This is not a tuning knob — without it
the feature does not work at all.

### 3.2 The model was not the problem; the schema was

**Symptom.** `qwen3:1.7b` answered *"window seat near the front"* with
`{"max_row": 1000000000000000}` and no `position` at all. `qwen3:14b` answered
correctly but took **6–7 seconds warm**, which reads as broken at a kiosk.

**Cause.** The JSON schema listed its properties but did not `require` them and
did not bound them. A small model omits keys it is unsure about and invents
magnitudes it has no reason to.

**Fix.** `required: [...]` for every field, `minimum`/`maximum` on rows from
`CABIN_ROWS`, and worked examples in the system prompt. The same 1.7B model
then answered every test phrase correctly, in about a second.

> Worth internalising: the instinct was "the small model is too weak". The
> measurement said the schema was too loose. Reach for the schema first.

The validation layer stays regardless. A schema constrains a well-behaved
model; it does not guarantee one.

### 3.3 sqlite-vec measures L2, not cosine

**Symptom.** `vocabulary.resolve()` returned `None` for phrases it obviously
should have matched.

**Cause.** The threshold was 0.55, calibrated for a cosine similarity in
0–1. `vec0` defaults to **L2 distance**, which is not on that scale.

**Fix.** Measured the corpus with `nomic-embed-text` and cut in the gap:

| | distance |
|---|---|
| genuine rephrasing | 0.60 – 0.65 |
| related but wrong | 0.91 – 0.98 |
| nonsense | 1.09 + |

`MAX_DISTANCE = 0.80`. Those numbers belong to the embedding model — changing
it means re-measuring, not guessing.

### 3.4 "Farthest back" returned row 21

**Symptom.** *"farthest back seat near the window"* selected `21A`.

**Two causes, stacked.**

1. The prompt taught that *"back"* means `min_row 21`, so a superlative came
   back as `min_row 21` **and** `max_row 21` — pinning the passenger to the row
   where the back merely begins, and excluding row 30 entirely.
2. Ranking was always front-to-back, so even a correct rows-21-to-30 filter
   would have offered `21A`: the front-most seat of the back section.

**Fix.** `SeatQuery.toward`. Bounds say which seats *qualify*; `toward` says
which end to offer *first*. That distinction is the whole difference between
"at the back" and "as far back as possible".

### 3.5 "Middle" is two different words

**Symptom.** *"a random seat in the middle of the plane"* returned `1B`. Worse,
*"middle of the aircraft near a window"* returned a middle seat and **dropped
"window" entirely**.

**Cause.** In a cabin, *middle* means both a seat between two others and the
centre of the aircraft. The model collapsed them into the seat type.

**Fix.** The prompt separates the senses and gives the row band for the
positional one, so "middle seat" stays a B/E seat while "middle of the plane"
becomes rows 11–20 — and the two compose.

### 3.6 There was no notion of a side

**Symptom.** *"aisle on the right side"* returned the left-hand aisle seat.

**Cause.** "Aisle" is columns C **and** D — opposite sides of the same aisle —
and column order always returned C. Nothing in `SeatQuery` could express a side.

**Fix.** `SeatQuery.side`, narrowing to A–C or D–F, derived from
`CABIN_COLUMNS` like the position sets.

### 3.7 "Random" cancelled the request

**Symptom.** *"a random seat at the back of the plane"* shuffled the whole
cabin and returned row 12. The seats were random, and nothing about them was at
the back.

**Two causes.** The prompt let the model drop the rows once it set `random`;
and the ranking discarded `toward` whenever it shuffled.

**Fix.** The prompt states that random never cancels a constraint, with worked
examples carrying both. The draw is made from the named third of the cabin,
falling back to the wider set rather than refusing.

### 3.8 The model invents claims nobody made

This is the pattern behind most of the rest, and the most useful thing on this
page.

| It invented | On a phrase like |
|---|---|
| `side: right` | "aisle seat at the back" |
| `max_row: 10` | "random window seat on the left" |
| `toward: front` | "window seats for 6 people" |
| `position: middle` | "6 seats for a family in one row" |
| `party: 6` | anything nearby that mentioned six |

Asking it not to does not work. It was told explicitly to set a side only when
asked, and given a counter-example — and **the counter-example made it worse**,
because it ended in "at the back" and taught exactly the association it was
meant to break.

**So the fix is deterministic, not conversational: a claim is dropped unless
the passenger's own words support it.**

```python
_drop_unsaid_side(query, prose)      # left|port, right|starboard
_drop_unsaid_band(query, prose)      # front|forward|nose…, back|rear|tail…
_drop_unsaid_toward(query, prose)    # same word sets
_drop_unsaid_party(query, prose)     # a digit, or one|two|…|couple|pair
_drop_unsaid_position(query, prose)  # window|porthole|view|aisle|middle…
_drop_unsaid_together(query, prose)  # together|one row|next to|family…
```

Two properties make this safe:

- **They only ever remove.** A guard that added a constraint could invent one
  itself, and negation ("not at the back") would defeat it.
- **They cover closed word sets.** Naming a side, a count, an end or a grouping
  takes one of a handful of words. Explicit rows are never second-guessed —
  "rows 5 to 9" is the passenger's, whatever surrounds it.

`position` is the awkward one: its synonyms are genuinely open-ended, which is
why the vector vocabulary exists. Its guard is deliberately wide, and anything
stranger than the list falls through to an empty query, which is exactly what
sends it to the index.

### 3.9 A group is one request, not N requests

**Symptom.** *"6 seats for a family one row"* returned six middle seats
scattered down the cabin.

**Cause.** Besides the invented position (§3.8), a filter matching six seats is
not the same request as six seats *together*, and nothing expressed the
difference.

**Fix.** `SeatQuery.together`. The search prefers a single row that can hold the
whole party, skips a row with a seat already taken, and falls back rather than
refusing when the filter makes one row impossible — as six window seats always
will.

---

## 4. Failure behaviour

The three core requirements never depend on a model being up.

| Condition | Response |
|---|---|
| Ollama unreachable or slow (>30s) | Map unchanged, existing selection intact, "smart search is unavailable" |
| Nothing matches | Map unchanged, selection intact, "No free window seats up to row 1" |
| A match | Seats selected, camera flies, banner names what was understood |

Every path returns **200 with the map**, for the same reason booking failures
do: htmx does not swap error responses, so a 4xx would leave the passenger
staring at an unchanged page.

The banner always states the filter in English — *"Aisle seats on the right as
far back as possible."* That is not decoration. It is the only way a passenger
can tell a misunderstanding from an empty cabin.

---

## 5. Testing

**No test requires Ollama.** The provider is one file precisely so the suite can
mock that seam.

Covered:

- validation: absurd rows clamped, reversed ranges swapped, invented fields and
  enum values dropped, `random` accepted only as a real boolean
- every guard, including the word-boundary case — "alright" is not a request
  for the right-hand aisle
- search: position sets, side sets, row bounds, `toward` ordering, `together`
  row preference and its fallback, seeded randomness, two queries per search
- degradation: model down, map still renders, selection survives
- injection: the database survives and still books

**Not covered, and cannot be:** whether the model extracts a given phrase
correctly. Asserting that would mean requiring Ollama in CI, which this plan
rules out. Every phrase in §1 was checked by hand against the live model, and
that is the honest status.

If you change the prompt or the model, re-run those phrases. A quick way:

```bash
uv run ars/manage.py shell -c "
from assistant import extraction
from assistant.schema import describe
for p in ['window seat near the front', 'farthest back window',
          '6 seats for a family in one row', 'aisle on the right at the back']:
    print(p, '->', describe(extraction.extract(p)))
"
```

---

## 6. Tuning knobs, and where they live

| Knob | Where | Note |
|---|---|---|
| `GENERATION_MODEL` | `assistant/providers.py` | `qwen3:1.7b`. Bigger is slower, not obviously better — see §3.2 |
| `EMBEDDING_MODEL` | `assistant/providers.py` | Changing it means a migration **and** re-measuring §3.3 |
| `REQUEST_TIMEOUT_SECONDS` | `assistant/providers.py` | 30s, sized for a cold start, not a warm call |
| `MAX_DISTANCE` | `assistant/vocabulary.py` | L2, not cosine |
| `CONCEPTS` | `assistant/vocabulary.py` | Add a phrase when a real one fails, then `reindex_concepts` |
| Prompt and guards | `assistant/extraction.py` | Row bands derive from `CABIN_ROWS` |

---

## 7. Limits, and why each one stands

- **An invented position survives.** *"seats for six"* comes back as window
  seats. The guard's word list cannot be tightened without breaking the open
  phrasing the vocabulary exists for. Revisit by consulting the index before
  dropping, if it grates.
- **Searching is within one flight.** `SeatQuery` carries `flight_number` and
  `destination` for a future search across flights; they are unused.
- **The vocabulary is ten phrases.** It earns its place as a fallback, but the
  model now handles most of what it covers.
- **First query after an idle period is slower** — Ollama reloads the model.
