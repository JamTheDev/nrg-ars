# Architecture

Technical design for the Airline Reservation System (ARS). This document is the
reference for how the three core requirements are met and how the natural-language
seat search is structured.

- **Stack:** Django 6.1, SQLite, htmx, Tailwind CSS v4
- **Auth:** none — kiosk-style, a passenger name is typed at booking time
- **Scope:** many flights, one shared cabin layout, single class

---

## 1. Domain Model

Four models in a single `reservations` app.

```
Flight  ──┐
          ├──< Booking >──  Passenger
Seat    ──┘
```

### Seat is a shared catalog, not per-flight

Because every flight uses the same cabin layout, `Seat` holds each **position**
in the cabin exactly once (180 rows total for a 30×6 cabin), and *all* flights
reference that same catalog. Seat occupancy is a property of `Booking`, not of
`Seat`.

The alternative is copying 180 `Seat` rows per flight — was rejected: it
multiplies storage by the flight count and makes "seat 12C" ambiguous across
flights for no benefit while the layout is uniform.

> **If a second aircraft layout is ever introduced**, this decision reverses:
> `Seat` gains an `Aircraft` foreign key and `Flight` points at an `Aircraft`.
> That is the single most likely future migration.

```python
class Seat(models.Model):
    row = models.PositiveSmallIntegerField()          # 1..30
    column = models.CharField(max_length=1)           # 'A'..'F'

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['row', 'column'], name='unique_seat_position'),
        ]
        ordering = ['row', 'column']                  # defines "first available"

    @property
    def designation(self) -> str:
        return f'{self.row}{self.column}'             # "1A"


class Flight(models.Model):
    number = models.CharField(max_length=10, unique=True)   # "PR101"
    origin = models.CharField(max_length=3)                 # IATA, "MNL"
    destination = models.CharField(max_length=3)            # "CEB"
    departs_at = models.DateTimeField()


class Passenger(models.Model):
    full_name = models.CharField(max_length=120)


class Booking(models.Model):
    flight = models.ForeignKey(Flight, on_delete=models.CASCADE, related_name='bookings')
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name='bookings')
    passenger = models.ForeignKey(Passenger, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['flight', 'seat'], name='unique_seat_per_flight'),
        ]
```

**`UniqueConstraint(flight, seat)` is the system's correctness guarantee.**
Everything else in the booking path is a nicety layered on top of it.

### Cabin layout configuration

```python
# settings.py
CABIN_ROWS = 30
CABIN_COLUMNS = 'ABCDEF'
```

A data migration seeds the `Seat` catalog from these values. They are *not* read
at request time — changing them after seeding requires a new migration, since the
seeded rows are the source of truth.

### Availability

Availability is derived, never stored — there is no `is_taken` flag to fall out
of sync:

```python
def available_seats(flight):
    return Seat.objects.exclude(bookings__flight=flight)
```

---

## 2. Core Requirement 1 — Display available seating

Rendered two ways from one source of truth.

### Web seat map

`GET /flights/<id>/` renders a Tailwind grid. The view builds a row-major
structure so the template stays logic-free:

```python
taken = set(
    Booking.objects.filter(flight=flight).values_list('seat_id', flat=True)
)
grid = [
    [(seat, seat.id in taken) for seat in row_seats]
    for row_seats in grouped_by_row
]
```

A single query for seats and a single query for taken IDs — no N+1 across 180
seats. The aisle is a rendering concern: a gap is inserted after column C via
`{% if seat.column == 'C' %}`, not modeled in the database.

### Text rendering — the literal "print"

The requirement says *print*, so a management command renders the same data as
text, sharing the availability query with the web view:

```bash
uv run ars/manage.py print_flight PR101
```

```
PR101  MNL → CEB  2026-08-14 09:30
     A  B  C   D  E  F
 1   .  .  X   .  .  .
 2   X  X  .   .  .  .
        . = available    X = taken
```

---

## 3. Core Requirement 2 — Assign the first available seat

"First" is defined by `Seat.Meta.ordering = ['row', 'column']`: lowest row
number, then lowest column letter. Seat 1A is first; 30F is last.

```python
def assign_first_available(flight, passenger):
    with transaction.atomic():
        seat = (
            Seat.objects
            .exclude(bookings__flight=flight)
            .order_by('row', 'column')
            .first()
        )
        if seat is None:
            raise FlightFullError(flight)
        return Booking.objects.create(flight=flight, seat=seat, passenger=passenger)
```

The gap between the `SELECT` and the `INSERT` is a genuine race. Section 5
covers how it is closed.

---

## 4. Core Requirement 3 — Assign a specific seat

Input is a designation string like `"1A"`, `"12c"`, or `"7 D"`, which must be
parsed and validated before it reaches the database.

```python
SEAT_RE = re.compile(r'^\s*(?P<row>\d{1,2})\s*(?P<column>[A-Za-z])\s*$')

def parse_designation(raw: str) -> tuple[int, str]:
    match = SEAT_RE.match(raw)
    if not match:
        raise InvalidSeatError(f'{raw!r} is not a seat designation like "12C".')
    return int(match['row']), match['column'].upper()
```

Four distinct failure modes, each with its own message — collapsing them into one
"invalid seat" error makes the UI meaningfully worse:

| Condition | Response |
|---|---|
| Malformed string (`"99ZZ"`) | `InvalidSeatError` — "not a seat designation" |
| Well-formed but outside the cabin (`"45A"`) | `SeatNotFoundError` — "this aircraft has rows 1–30" |
| Exists but already booked | `SeatTakenError` — "12C was just taken" |
| Available | `Booking` created |

---

## 5. Concurrency

Two kiosks requesting 1A at the same instant must not both succeed.

### Policy: let both try, first commit wins

Neither request is turned away up front. A and B both reach the database; the
one whose `INSERT` commits first owns the seat, and the other is blocked with
"12C was just taken". Nothing is reserved in advance — there is no lock queue,
no seat hold, and no "checking availability…" step that could leave a seat
frozen behind an abandoned session.

The consequence to accept: **losing is discovered at commit, not at click.** A
passenger can tap a free-looking seat and still be refused a moment later, so
the error path is a normal outcome to design for, not an edge case. The seat map
self-heals on the next swap (§6), which keeps the window small.

Ordering is decided by SQLite's write lock, not by when a request arrived at the
server. Under contention the "first" request is the first to commit, which for a
kiosk-scale system is the only ordering worth guaranteeing.

> **First-available is the deliberate exception.** Blocking the loser is right
> when the passenger asked for *1A* specifically. When they asked for "any
> seat", failing them because someone else took 1A serves nobody — that path
> retries onto the next free seat instead (point 3 below).

### `select_for_update()` is not available here

Django's SQLite backend does **not** implement `SELECT … FOR UPDATE`. It inherits
`has_select_for_update = False`, and the query compiler skips the locking branch
entirely — no SQL is emitted **and no error is raised**. A `select_for_update()`
call on this stack silently does nothing while appearing to protect the code.

This is why the design does not depend on it.

### What actually protects the booking

1. **`UniqueConstraint(flight, seat)`** — enforced by SQLite itself. The losing
   transaction gets an `IntegrityError`. This is the guarantee; it cannot be
   raced past.
2. **Catch and translate**, rather than pre-checking:

```python
def book_seat(flight, seat, passenger):
    try:
        with transaction.atomic():
            return Booking.objects.create(flight=flight, seat=seat, passenger=passenger)
    except IntegrityError:
        raise SeatTakenError(seat)
```

3. **Retry for auto-assignment.** A specific-seat request should fail loudly —
the passenger asked for *1A*. First-available should simply take the next seat:

```python
for _ in range(MAX_ASSIGN_RETRIES):        # 3
    try:
        return assign_first_available(flight, passenger)
    except SeatTakenError:
        continue
raise CouldNotAssignError(flight)
```

The `transaction.atomic()` block must wrap the `create()` so a failed insert does
not poison an outer transaction — after an `IntegrityError`, Django marks the
transaction for rollback and any further query in that block raises
`TransactionManagementError`.

### SQLite settings

WAL mode lets the seat map be read while a booking is being written, and a busy
timeout absorbs brief write contention instead of failing instantly:

```python
DATABASES['default']['OPTIONS'] = {
    'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
    'timeout': 20,          # seconds to wait on a locked DB
}
```

SQLite serializes writers at the database level, so throughput is bounded by one
writer at a time. For a kiosk-scale system this is a non-issue; it is the first
thing to revisit if this ever becomes a real service.

---

## 6. HTTP & htmx Interaction Design

Full page loads only on navigation. Every booking action swaps a fragment.

| Method & path | Name | Returns |
|---|---|---|
| `GET /` | `flight-list` | Flight list |
| `GET /flights/<id>/` | `flight-detail` | Full page: seat map + booking form |
| `POST /flights/<id>/book/` | `book-seat` | **Fragment** — updated seat map |
| `POST /flights/<id>/book/first/` | `book-first` | **Fragment** — updated seat map |
| `GET /flights/<id>/map/` | `seat-map` | **Fragment** — seat map, optionally with seats already chosen: `?party=4&keep=12A`, or `?q=window+seat+near+the+front` |

Templates split so the fragment and the full page render the identical markup:

```
templates/reservations/
├── flight_list.html        departures board
├── flight_detail.html      {% include "reservations/_seat_map.html" %}
├── _seat_map.html          ← returned directly by booking POSTs
├── _seat.html              one seat button
└── _booking_result.html    ← success / error banner
```

### The kiosk camera

The seat map is drag/pinch/scroll navigable (`static/js/seatmap.js`, vanilla
Pointer Events — no library, nothing from a CDN). The pan/zoom transform is
applied to `#seat-map-canvas`, one level **above** the `#seat-map` fragment:

```
#seat-map-viewport   clips, owns the gestures
└── #seat-map-canvas transform: translate(...) scale(...)
    └── #seat-map    ← the swappable fragment
```

Putting the transform outside the fragment is what lets a booking swap refresh
the cabin without throwing away where the user was looking.

**Seat taps do not come from `click`.** The camera calls `setPointerCapture()`
so a pan that leaves the viewport keeps tracking, and capture retargets the
compatibility mouse events that follow — the real `click` is delivered to the
viewport, not to the seat under the finger. The camera therefore measures the
gesture itself and publishes a `seatmap:tap` CustomEvent carrying the
`pointerdown` target, which is the last honest answer available. A press that
travelled more than a few pixels was a pan and publishes nothing, so dragging
across the cabin never selects a seat.

Keyboard activation still arrives as a `click`, distinguished by
`MouseEvent.detail === 0`; without that guard a pointer tap would toggle twice.

**Tap tolerance is generous on purpose.** A press is judged by how far it
strays from where it went down, not by how far it travelled — a hand that
jitters back and forth over one spot has not panned anywhere — and the
threshold is 14px, because presses on a touchscreen or trackpad routinely drift
around 10px. A tight threshold silently reclassifies ordinary taps as pans, and
the seat map appears to ignore the passenger. Double-tapping a seat selects it
once rather than toggling twice, and does not zoom.

### Party seating

`selectors.pick_party_seats()` chooses N seats for one party. A 3 + 3 cabin
makes the longest unbroken run **three**, so a party of four can never be one
block — "together" is a ladder, and the same row across the aisle counts:

1. one block, taking the **smallest** that fits so a party of two does not
   consume the last run of three
2. one row, across the aisle
3. two adjacent rows
4. fewest groups, front-most

It lives in `selectors.py` rather than JavaScript so the management commands and
the natural-language assistant (§7) can reuse the one implementation, and so it
is testable without a browser. Two queries, whatever the party size.

### The reserve bar has two modes

The same bar either counts the party by hand or takes a description. A chat
button beside *Continue* swaps the stepper for a text box; a back arrow
returns, and Escape steps back one thing at a time — out of the box first, then
out of the panel.

Both modes drive the same `seat-map` route, so whichever is showing, the answer
arrives as a selection the panel and the camera already understand.

### Static assets are versioned in DEBUG

`{% versioned_static %}` appends the file's modification time. Django's
development server sends static files with `Last-Modified` and no
`Cache-Control`, so a browser may reuse a script for minutes without
revalidating — which presents as new markup driving old JavaScript, and looks
exactly like a broken button. Production URLs are untouched; cache-busting
there is `collectstatic`'s job.

### Selection state and the summary panel

Selecting a seat sets `aria-pressed="true"` on the seat button and opens the
`#reserve-panel` side sheet (`static/js/seat-selection.js`).

After **any** swap of `#seat-map`, the selection is exactly the seats the server
rendered as pressed, in document order. A booking response presses nothing, so
the selection clears; a party pick presses N seats, so it is adopted. One rule,
no flag, and the client never disagrees with the map in front of it.

**State that JavaScript toggles is styled from `static/src/input.css`, not from
Tailwind classes swapped in JS.** Tailwind only compiles classes it can see in
the scanned templates, so a class named only inside a `.js` file produces no CSS
and fails silently at runtime. The selected-seat green, the panel's off-canvas
transform, and the reserve bar's shift are therefore plain CSS rules keyed off
`aria-pressed` / `data-open`.

Each seat is a button posting its own designation:

```html
<button hx-post="{% url 'book-seat' flight.id %}"
        hx-vals='{"seat": "{{ seat.designation }}"}'
        hx-target="#seat-map"
        hx-swap="outerHTML"
        {% if is_taken %}disabled{% endif %}>
  {{ seat.designation }}
</button>
```

Returning the whole map rather than the single clicked seat is deliberate: when
another kiosk books concurrently, the swap refreshes every seat, so the map
self-heals instead of drifting.

Errors return **HTTP 200 with an error fragment**, not 4xx — htmx does not swap
error responses by default, so a 409 would leave the user staring at an
unchanged page. The status banner is delivered as an out-of-band swap:

```html
<div id="booking-status" hx-swap-oob="true" class="text-red-600">
  Seat 12C was just taken.
</div>
```

CSRF is handled globally by `hx-headers` on `<body>` in `base.html`.

---

## 7. Natural-Language Seat Search

> **Design note.** This feature is **not** document retrieval, and building it as
> classic RAG would make it wrong. Embedding seat and booking rows and searching
> them by vector similarity fails on exactly the constraints that matter:
> similarity cannot enforce "row under 10", availability changes on every booking
> so the index is stale immediately, and re-embedding on each write is wasted
> effort. The database already answers these questions exactly.
>
> The architecture below therefore routes **facts through the ORM** and uses
> **vector search only where meaning is genuinely fuzzy** — mapping loose phrasing
> onto the system's vocabulary. This keeps sqlite-vec earning its place without
> making it the source of truth for availability.

### Pipeline

```mermaid
flowchart TD
    A["'window seats for 6 people'"] --> B[qwen3:1.7b, decoding constrained to the schema]
    B --> C[_validate: clamp rows, drop invented fields]
    C --> D[Guards: drop claims the passenger never made]
    D --> E{Anything left?}
    E -- no --> V[sqlite-vec vocabulary lookup]
    V --> F
    E -- yes --> F[SeatQuery]
    F --> G[Django ORM — source of truth]
    G --> H[Ranked free seats]
    H --> I[Seat map fragment, seats already selected]
```

The result arrives as a **selection on the ordinary seat map**, so the panel,
the totals and the camera need to know nothing about language. `?q=` is one
more parameter on the `seat-map` route beside `?party=` and `?keep=`.

### Step 1 — Structured extraction, never SQL

The LLM's only job is turning prose into a **validated filter object**. It never
emits SQL and never sees the database. This is the security boundary: a prompt
injection can at worst produce a strange-but-valid filter, never arbitrary SQL.

```python
@dataclass(frozen=True)
class SeatQuery:
    flight_number: str | None = None    # reserved for search across flights
    destination: str | None = None      # reserved for search across flights
    position: Literal['window', 'aisle', 'middle'] | None = None
    min_row: int | None = None
    max_row: int | None = None
    toward: Literal['front', 'back'] | None = None   # which end to offer first
    side: Literal['left', 'right'] | None = None     # A-C or D-F
    party: int | None = None                          # how many seats
    together: bool = False                            # seat them as a group
    is_random: bool = False                           # draw, do not take the first
```

Ollama is called with `format=<json-schema>` so decoding is constrained to the
schema rather than parsed hopefully out of prose. **The schema does more work
than the model choice does**: it `require`s every field, because a small model
omits keys it is unsure about, and it bounds the rows against `CABIN_ROWS`,
because an unbounded one answered "near the front" with `max_row` in the
trillions. See `docs/plans/08-natural-language-search.md` §3.2.

### Step 1b — Guards: the model invents claims nobody made

Constrained decoding controls the *shape* of the answer, not its honesty. In
practice the model asserts a side for "aisle seat at the back", a row band for
"random window seat on the left", a direction for "window seats for 6 people",
and a seat type for "6 seats for a family in one row".

Asking it not to does not work — a worked counter-example taught the exact
association it was meant to break. So each of those fields is checked against
the passenger's own words and **dropped when unsupported**:

| Guard | Accepts |
|---|---|
| `_drop_unsaid_side` | left, port, right, starboard |
| `_drop_unsaid_band` | front, forward, nose… / back, rear, tail… |
| `_drop_unsaid_toward` | the same two word sets |
| `_drop_unsaid_party` | a digit, or one…ten, couple, pair, both |
| `_drop_unsaid_position` | window, porthole, view, aisle, corridor, middle… |
| `_drop_unsaid_together` | together, one row, next to, beside, family… |

Two properties keep this safe. The guards **only ever remove** — one that added
a constraint could invent one itself, and a negation would defeat it. And they
cover **closed word sets**: naming a side, a count, an end or a grouping takes
one of a handful of words. Explicit rows are never second-guessed; "rows 5 to 9"
is the passenger's whatever surrounds it.

`position` is the awkward one, since its synonyms are open-ended. Its list is
deliberately wide, and anything stranger falls through to an empty query —
which is exactly what sends it to the vocabulary index.

### Step 1c — Ranking is part of the answer

A filter says which seats *qualify*; three fields say which of them to offer,
and getting this wrong answers a different question than the one asked:

- **`toward`** — "as far back as possible" must not return the front-most seat
  of the back section. Bounds and direction are separate ideas.
- **`together`** — a party asking for one row is one request, not N requests
  that each match. The search prefers a single row that can hold them all,
  skips a row with a seat already taken, and falls back rather than refusing
  when the filter makes one row impossible, as six window seats always will.
- **`is_random`** — random samples *within* the constraint. "A random seat at
  the back" that shuffles the whole cabin has answered half the sentence.

### Step 2 — Position is derived, not stored

With `CABIN_COLUMNS = 'ABCDEF'`, window is `{A, F}`, aisle is `{C, D}`, middle is
`{B, E}`. Computed from the layout constant so a layout change cannot leave a
stored flag stale:

```python
WINDOW = {CABIN_COLUMNS[0], CABIN_COLUMNS[-1]}
AISLE  = {CABIN_COLUMNS[len(CABIN_COLUMNS)//2 - 1], CABIN_COLUMNS[len(CABIN_COLUMNS)//2]}
MIDDLE = set(CABIN_COLUMNS) - WINDOW - AISLE
```

### Step 3 — Where sqlite-vec is used

Vector search handles **vocabulary resolution only**: phrases the extractor
doesn't recognise ("by the porthole", "up front", "near the loo") are embedded
and matched against a small curated set of canonical concept descriptions. The
match returns a *field value*, which then flows through the same validated
`SeatQuery`.

This corpus is small, static, and authored — it changes when the vocabulary
changes, never on booking. That is what makes it a legitimate use of an index.

```sql
CREATE VIRTUAL TABLE assistant_concept_vec USING vec0(
    concept_id INTEGER PRIMARY KEY,
    embedding  FLOAT[768]          -- nomic-embed-text
);
```

`vec0` is a virtual table outside the ORM's control. It is created via
`migrations.RunSQL`, and read through raw SQL in the retriever — no Django model
is mapped onto it.

### Step 4 — Loading the extension into Django

Django does not load SQLite extensions natively. The `assistant` app hooks
`connection_created`:

```python
def load_sqlite_vec(sender, connection, **kwargs):
    if connection.vendor != 'sqlite':
        return
    connection.connection.enable_load_extension(True)
    sqlite_vec.load(connection.connection)
    connection.connection.enable_load_extension(False)
```

**Environment requirement:** the Python interpreter's `sqlite3` module must be
built with extension loading enabled. Verified present on this machine
(SQLite 3.46.1). The stock macOS system Python is a known exception — contributors
there need a Homebrew or `uv`-managed Python.

### Local models via Ollama

| Role | Model | Notes |
|---|---|---|
| Generation / extraction | `qwen3:1.7b` | ~1s per query. Chosen over `qwen3:14b` by measurement, below. |
| Embedding | `nomic-embed-text` | Installed. 768 dimensions, matching the `vec0` table above. |

### Three things measurement decided

**`think=False` is mandatory, not a tuning knob.** qwen3 reasons before
answering by default. The same extraction that returns in about a second
without thinking was still running after **sixty seconds** with it.

**The small model is the right one, because the schema does the work.**
`qwen3:14b` answers correctly but takes 6–7s warm, which reads as broken at a
kiosk. `qwen3:1.7b` answers in ~1s — and its first attempts were garbage
(`max_row: 1000000000000000`, `position` missing entirely) until the JSON
schema was tightened. What fixed it was not a bigger model:

- `required: [position, min_row, max_row]` — otherwise the model omits keys it
  is unsure about
- `minimum`/`maximum` on the rows, from `CABIN_ROWS`
- three worked examples in the system prompt

The validation layer stays regardless. A schema constrains a well-behaved
model; it is not a guarantee, and `_validate()` clamps what arrives anyway.

**`vec0` measures L2 distance, not cosine**, so the match threshold is not a
0–1 similarity. Measured against this corpus with `nomic-embed-text`: genuine
rephrasings land at 0.60–0.65, related-but-wrong at 0.91–0.98, and nonsense
above 1.09. The cut sits at 0.80, in the gap. Those numbers are properties of
the embedding model — changing it means re-measuring.

The embedding dimension is baked into the virtual table. **Changing embedding
model means dropping and rebuilding the index** — a migration, not a config edit.

Both are reached through a narrow provider interface (`assistant/providers.py`),
so swapping Ollama for a hosted API later touches one file. Ollama being local
means no API keys, no per-query cost, and no passenger phrasing leaving the
machine — worth stating explicitly, since NL queries can contain personal details.

### Failure behaviour

Ollama is a separate process that may be stopped, and a local model takes a
second or two even when it is running. **The three core requirements never
depend on it.**

| Condition | Response |
|---|---|
| Ollama unreachable or slow (>8s) | Map unchanged, existing selection intact, "smart search is unavailable" |
| Nothing matches | Map unchanged, selection intact, "No free window seats up to row 1" |
| A match | Seats selected, camera flies to them, banner names what was understood |

Every path returns **200 with the map**, for the same reason booking failures do
(§6): htmx does not swap error responses, so a 4xx would leave the passenger
staring at an unchanged page.

The banner always states the filter in English — *"Aisle seats on the right as
far back as possible."* That is not decoration: it is the only way a passenger
can tell a misunderstanding from an empty cabin.

While the request is in flight the *Find* button becomes a spinner and stops
accepting clicks. A second of silence on a kiosk reads as a dead control — this
project has shipped that bug more than once.

---

## 8. Directory Layout

```
ars/
├── ars/                      project package (settings, urls)
├── reservations/             ← core booking domain
│   ├── models.py             Flight, Seat, Passenger, Booking
│   ├── services.py           assign_first_available, book_seat  (no HTTP here)
│   ├── selectors.py          available_seats, seat_grid
│   ├── exceptions.py         SeatTakenError, InvalidSeatError, …
│   ├── views.py              thin — parse, delegate, render
│   ├── templatetags/assets.py  versioned_static
│   └── management/commands/   print_flight, seed_flights
├── assistant/                ← natural-language search
│   ├── providers.py          Ollama client boundary — the seam tests mock
│   ├── extraction.py         prose → SeatQuery, plus the guards
│   ├── vocabulary.py         sqlite-vec concept lookup
│   ├── models.py             Concept: the authored corpus
│   ├── schema.py             SeatQuery dataclass + JSON schema
│   ├── migrations/           Concept table + the vec0 virtual table
│   ├── management/commands/reindex_concepts.py
│   └── apps.py               connection_created → load sqlite-vec
├── templates/
└── static/
```

Business rules live in `services.py`/`selectors.py`, not views — so the
management command and the htmx views share one implementation, and the booking
rules are testable without HTTP.

---

## 9. Dependencies

| Package | Purpose |
|---|---|
| `django` | Framework |
| `django-htmx` | `request.htmx`, local htmx asset |
| `python-dotenv` | `SECRET_KEY` from `.env` |
| `sqlite-vec` | Vector search inside SQLite |
| `ollama` | Local LLM/embedding client |

External, not pip-installable: the **Ollama server**, and the Tailwind standalone
CLI (see README).

---

## 10. Testing Strategy

| Area | Approach |
|---|---|
| Seat parsing | Table-driven over the four failure modes in §4 |
| First-available | Seed partial bookings, assert exact seat chosen |
| Concurrency | A booked seat re-booked raises `SeatTakenError` and leaves one row; the real race was demonstrated with two browsers rather than two threads |
| Seat map | Query count assertion to catch N+1 regressions across 180 seats |
| Party seating | Exact seats asserted per tier: one block, one row, adjacent rows, scattered |
| `print_flight` | The rendering compared line for line — there the format *is* the deliverable |
| NL extraction | Fixed model output → expected `SeatQuery`, mocked at `providers.py` |
| Guards | Each drops an unsaid claim and keeps a said one, word-boundary matched |
| Degradation | Provider raising → map still renders, selection survives, 200 |
| Injection | `'; DROP TABLE …` leaves the database intact and still bookable |

**No test may require Ollama to be running.** The provider is a single file
precisely so the suite can mock that seam.

**What the suite cannot cover** is whether the model extracts a given phrase
correctly — asserting that would put a 1.7B model in CI. Those phrasings are
verified by hand and listed in `docs/plans/08-natural-language-search.md`, which
also carries the re-check command to run after touching the prompt.

**Browser-driven checks** (Playwright, real input) cover what a test client
cannot: pointer capture, drag versus tap, the camera, and the booking
round-trip. One blind spot to remember — Playwright launches a fresh profile
every run, so it will never reproduce a stale-cache bug.

---

## 11. Open Decisions

- ~~**No route for `/` yet**~~ — resolved: `/` renders the flight list and each
  row links to `/flights/<id>/`, the pan/zoom seat map.
- **Selection is still not a hold.** Seats picked in the panel are reserved for
  nobody until *Confirm booking* commits; another kiosk can take one in the
  meantime, and the passenger finds out at commit. That is the §5 policy
  working as designed, not a gap.
- **A party is all-or-nothing.** If any seat in a multi-seat request was just
  taken, the whole booking rolls back rather than partially succeeding —
  serving two of three passengers and charging for it is worse than refusing.
- ~~**No `print_flight` command yet**~~ — resolved: `print_flight` renders the
  same `Cabin` as text, sharing the availability query with the web view.
  `--available-only` lists free designations for piping.
- **Party size and selection are one value.** The stepper displays the number of
  selected seats; raising it asks the server for another seat beside the party,
  lowering it drops the newest pick in the page.
- **No cancellation.** A booking is immediate and final.
- **`SEAT_FARE` is a flat placeholder** in settings so the panel can show a
  total. Real pricing belongs on `Flight` (or a fare class), which is a
  migration, not a config edit.
- **No seat holds.** Deliberately scoped out; see the selection note above.
- **Natural-language search is within one flight.** `SeatQuery` carries
  `flight_number` and `destination` for a future search across flights; this
  release leaves them unused rather than removing them.
- **An invented seat type survives the guards.** "seats for six" comes back as
  *window* seats. Unlike sides, counts and ends, position synonyms are
  open-ended, which is exactly what the vocabulary index is for — a closed-set
  guard tight enough to catch this would break "by the porthole". Revisit by
  consulting the index before dropping, if it grates.
- **The vocabulary is ten phrases.** It earns its place as a fallback, but the
  model now handles most of what it covers.
- **`DEBUG = True`** and no deployment target chosen.
- **Second aircraft layout** would trigger the `Seat` → `Aircraft` migration in §1.
