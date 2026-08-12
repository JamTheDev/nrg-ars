# Airline Reservation System

A kiosk-style seat reservation system: browse flights, pick seats on a
pan-and-zoom cabin map, and book them. There is no login — a passenger name is
typed at booking time, the way it works at an airport terminal.

It also ships an **AI assistant** that answers questions about a flight
(*"how many window seats are there?"*) and finds seats from a description
(*"window seats for 6 people"*), using a model running on your own machine.

**Stack:** Django 6.1 · SQLite · htmx · Tailwind CSS v4 · Ollama (optional)

### The three core requirements

| Requirement | Where it lives |
|---|---|
| Print a flight showing available seating | `print_flight` command, and the web seat map |
| Assign the first available seat | *First available seat*, `services.assign_first_available` |
| Assign a specific seat, e.g. "1A" | Tap a seat, or `services.book_seats` |

| Further reading | |
|---|---|
| How it works and why | [ARCHITECTURE.md](ARCHITECTURE.md) |
| How each feature was built, and what went wrong | [docs/plans/](docs/plans/) |
| Contribution rules, enforced by hooks | [CLAUDE.md](CLAUDE.md) |

---

## Quick start

```bash
uv sync                                     # 1. dependencies

cp .env.example .env                        # 2. secrets
uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
#    paste the value into SECRET_KEY, keeping the single quotes

mkdir -p bin                                # 3. the Tailwind binary (gitignored)
curl -sSL -o bin/tailwindcss \
  https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/tailwindcss-linux-x64
chmod +x bin/tailwindcss

uv run ars/manage.py migrate                # 4. schema + the 180-seat catalog
uv run ars/manage.py seed_flights           # 5. seven demo flights
uv run ars/manage.py runserver              # 6. http://127.0.0.1:8000/
```

On macOS use `tailwindcss-macos-arm64` (Apple Silicon) or `tailwindcss-macos-x64`.

`seed_flights` spreads flights over the next two weeks and is safe to re-run:
it refreshes a schedule that has aged into the past rather than duplicating it.

---

## Using it

**The flight list** (`/`) shows every flight in departure order with seats
remaining. Departed and full flights are visibly unbookable.

**The seat map** (`/flights/<id>/`) is the cabin from above. Drag to pan, pinch
or scroll to zoom, tap seats to select. The bar at the bottom has two modes:

- **Reserve for ‹ N ›** — the stepper picks N seats seated together
- **the chat button** — describe what you want, or ask a question

*Continue* asks for a name per seat and books them. *First available seat* in
the header takes the lowest-numbered free seat.

**Printing a flight** renders the same cabin as text, from the same
availability query, so the two can never disagree:

```bash
uv run ars/manage.py print_flight PR101
uv run ars/manage.py print_flight PR101 --available-only | wc -l
```

```
PR101  MNL → CEB  2026-08-13 18:00

      A  B  C   D  E  F
  1   .  .  X   .  .  .
  2   X  X  .   .  .  .
 ...
  . = available    X = taken
  177 of 180 available
```

---

## The AI assistant (optional)

**Everything above works without it.** With no Ollama the chat box reports that
smart search is unavailable and booking carries on unaffected.

| You type | It does |
|---|---|
| *How many seats are available?* | answers from the database |
| *What seats are in the middle section?* | answers with the range |
| *Window seats for 6 people* | selects six window seats |
| *Farthest back aisle on the right* | selects 30D |

Numbers are always counted by the database. The model decides what to count and
phrases the reply; it is never asked to do arithmetic.

### Setup

```bash
curl -fsSL https://ollama.com/install.sh | sh   # macOS: brew install ollama
ollama serve                                    # already a service on most installs

ollama pull qwen3:1.7b                          # ~1.4 GB, prose → filter
ollama pull nomic-embed-text                    # ~275 MB, embeds the vocabulary

uv run ars/manage.py migrate                    # creates the vec0 table
uv run ars/manage.py reindex_concepts           # → "10 concepts indexed."
```

`qwen3:1.7b` answers in about a second; `qwen3:14b` takes six or seven, which
reads as broken at a kiosk. Nothing leaves the machine — passenger phrasing can
contain personal detail. Ollama elsewhere? Set `OLLAMA_HOST` in `.env`.

### Check it without a browser

```bash
uv run ars/manage.py shell -c "
from assistant import extraction
from assistant.schema import describe
for p in ['window seat near the front', 'how many window seats are there?']:
    q = extraction.extract(p)
    print(f'{p!r:40} [{q.intent}] {describe(q)}')
"
```

The first call takes about 9 seconds while Ollama loads the model, then 1–2
seconds each. The model stays resident for 30 minutes.

### If it misbehaves

| Symptom | Meaning |
|---|---|
| "Smart search is unavailable" | Ollama is not running, or a model is not pulled |
| "Uh-oh! I ran into something" | The message was screened as off-topic |
| First query ~9s, rest 1–2s | Normal: the model was being loaded |
| `no such table: assistant_concept_vec` | Run `migrate` |
| `enable_load_extension` errors | Python built without SQLite extension support — use a `uv`-managed or Homebrew Python |

Wrong interpretations are model behaviour. The full problem log, and a command
for re-checking phrases after a prompt change, is in
[docs/plans/08](docs/plans/08-natural-language-search.md).

---

## Development

```bash
uv run ars/manage.py test reservations assistant   # 164 tests
uvx ruff check .                                   # must pass before a PR

./bin/tailwindcss -i ars/static/src/input.css -o ars/static/css/tailwind.css --watch
```

The compiled `tailwind.css` **is** committed, so the app renders for anyone who
hasn't downloaded the binary. Never edit it by hand.

### Three traps worth knowing

They share a shape: the code on disk is right and the process serving it is not.

1. **`runserver --noreload` caches templates.** Template edits do nothing until
   you restart.
2. **A new Python module needs a restart.** Template tag libraries are
   discovered once at startup.
3. **Tailwind only sees templates.** A class that exists only inside a `.js`
   file compiles to nothing, so state toggled by JavaScript is styled from
   `input.css` instead.

Static assets carry their modification time in DEBUG, so a changed file is a
changed URL and the browser cannot serve you yesterday's JavaScript.

---

## Layout

```
ars/
├── ars/                  # settings, root urls
├── reservations/         # the booking domain
│   ├── models.py         # Flight, Seat, Passenger, Booking
│   ├── selectors.py      # reads: seat maps, party picks, searches, counts
│   ├── services.py       # writes: booking rules
│   ├── views.py          # thin — parse, delegate, render
│   └── management/commands/   # print_flight, seed_flights
├── assistant/            # the AI assistant
│   ├── prompts.py        # every instruction given to a model
│   ├── safety.py         # screens a message before it is acted on
│   ├── extraction.py     # prose → validated SeatQuery
│   ├── answers.py        # facts counted, then phrased
│   ├── vocabulary.py     # sqlite-vec lookup for unusual phrasing
│   └── providers.py      # the only file that talks to Ollama
├── templates/reservations/
└── static/               # input.css (source), tailwind.css (built), js/
```

---

## Notes

- `SECRET_KEY` comes from `.env` and is never committed.
- `DEBUG = True`, and `ALLOWED_HOSTS` includes a LAN address for testing from a
  phone. Both must change before any real deployment.
- Times are stored in UTC and displayed in `Asia/Manila`.
- `SEAT_FARE` is a flat placeholder so the panel can show a total. Real pricing
  belongs on `Flight` and is a migration.
