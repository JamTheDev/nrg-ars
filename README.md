# Airline Reservation System

Python-based web application airline reservation system that enables users to reserve a seat on a flight.

## Core Requirements
1. Implement a way to print a flight/airplane that displays available seating on the plane.
2. Implement a way to assign the first available seat.
3. Implement a way to assign a seat for a specific passenger (e.g., "1A").

## Tech Stack
Backend - Django
Database - SQLite
Frontend - HTMX and TailwindCSS

## Getting Started

### 1. Install Python dependencies

```bash
uv sync
```

### 2. Create your .env file

`SECRET_KEY` is read from the environment, so the app will refuse to start
without it:

```bash
cp .env.example .env
uv run python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Paste the generated value into `.env`, keeping the single quotes so characters
like `$` and `!` are read literally. `.env` is gitignored and must stay that way.

### 3. Install the Tailwind CLI

Tailwind runs as a standalone binary, so no Node.js or `package.json` is needed.
The binary is gitignored, so each clone downloads its own copy:

```bash
mkdir -p bin
curl -sSL -o bin/tailwindcss \
  https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/tailwindcss-linux-x64
chmod +x bin/tailwindcss
```

On macOS, swap `tailwindcss-linux-x64` for `tailwindcss-macos-arm64` (Apple Silicon)
or `tailwindcss-macos-x64` (Intel).

### 4. Run migrations

```bash
uv run ars/manage.py migrate
```

### 5. Seed some flights (optional)

The departures screen reads real rows, so an empty database shows an empty list:

```bash
uv run ars/manage.py seed_flights
```

Seven flights spread over the next two weeks. Departures are relative to
*now*, and re-running **refreshes** a schedule that has fallen into the past —
demo data ages, and a board of departed flights is unbookable. Existing flights
are updated rather than duplicated, and their bookings are untouched.

### 6. Print a flight (core requirement 1)

The cabin renders as text as well as on screen, from the same availability
query, so the two cannot disagree:

```bash
uv run ars/manage.py print_flight PR101
uv run ars/manage.py print_flight PR101 --available-only | wc -l
```

### 7. Natural-language search (optional)

The seat map's chat button turns the reserve bar into a chat box. It does two
things:

- **Answers questions** about the flight — *"How many seats are available?"*,
  *"How many window seats are there?"*, *"Is this flight full?"*
- **Finds seats** on request — *"window seats for 6 people"*, *"farthest back
  aisle on the right"* — selecting them on the map

Every number in an answer is counted from the database. The model classifies
the question and names the filter; it is never asked to count, because a
language model asked to count will invent a number.

**The app runs fine without any of this.** With no Ollama the box says smart
search is unavailable and everything else — browsing, picking, booking, first
available, `print_flight` — carries on. Skip to step 8 if you do not want it.

#### 7a. Install and start Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh   # Linux; macOS: brew install ollama
ollama serve                                     # a service on most installs
```

Check it is up before going further:

```bash
curl -s http://localhost:11434/api/tags | head -c 80
```

#### 7b. Pull the two models

```bash
ollama pull qwen3:1.7b          # ~1.4 GB, turns prose into a filter
ollama pull nomic-embed-text    # ~275 MB, embeds the vocabulary
ollama list                     # both should appear
```

Both are chosen deliberately. `qwen3:1.7b` answers in about a second where
`qwen3:14b` takes six or seven, which reads as broken at a kiosk — and it is
just as accurate once the JSON schema does its share of the work
([why](docs/plans/08-natural-language-search.md#32-the-model-was-not-the-problem-the-schema-was)).
`nomic-embed-text` produces 768-dimension vectors, which is baked into the
`vec0` table; a different embedding model means a new migration.

Nothing leaves the machine. Passenger phrasing can contain personal detail, and
a local model means it stays local.

#### 7c. Build the vocabulary index

```bash
uv run ars/manage.py migrate            # creates the vec0 virtual table
uv run ars/manage.py reindex_concepts   # embeds the ten curated phrases
```

Expect `10 concepts indexed.` Re-run it after editing `CONCEPTS` in
`ars/assistant/vocabulary.py`, or after changing the embedding model.

#### 7d. Check it end to end, without a browser

```bash
uv run ars/manage.py shell -c "
from assistant import extraction
from assistant.schema import describe
for p in ['window seat near the front', 'farthest back aisle on the right',
          '6 seats for a family in one row', 'how many window seats are there?']:
    q = extraction.extract(p)
    print(f'{p!r:40} [{q.intent}] {describe(q)}')
"
```

```
'window seat near the front'             [find]  window seats up to row 10
'farthest back aisle on the right'       [find]  aisle seats on the right as far back as possible
'6 seats for a family in one row'        [find]  seats for 6 passengers together
'how many window seats are there?'       [count] window seats
```

Wording varies slightly between runs — that is a language model, not a parser.
What matters is that each phrase produces a filter that means what you asked
for.

**Timing.** The first call after a cold start takes around **9 seconds** while
Ollama loads the model; every call after that is **1–2 seconds**. The model is
kept resident for 30 minutes, so only the first query pays. The request timeout
is 30 seconds — sized for that cold start, not for a warm call.

#### 7e. When it does not work

| Symptom | Cause | Fix |
|---|---|---|
| "Smart search is unavailable" | Ollama not running, or a model not pulled | `ollama list`, then 7a–7b |
| Every query takes 6–7s | A larger model is configured | Check `GENERATION_MODEL` in `ars/assistant/providers.py` |
| First query ~9s, rest 1–2s | Ollama was loading the model | Normal. It stays resident for 30 minutes |
| `reindex_concepts` fails | Ollama unreachable | Same as row one |
| `no such table: assistant_concept_vec` | Migration not run | `uv run ars/manage.py migrate` |
| `enable_load_extension` errors | Python built without SQLite extension support | Use a `uv`-managed or Homebrew Python, not the macOS system one |
| Search understands the wrong thing | Model or prompt behaviour | The problem log and re-check command are in [docs/plans/08](docs/plans/08-natural-language-search.md) |

Ollama on another machine? Set `OLLAMA_HOST` in `.env`
(for example `http://192.168.1.20:11434`).

### 8. Start the server

```bash
uv run ars/manage.py runserver
```

The flight list is served at http://127.0.0.1:8000/, and each flight opens a
drag/pinch/scroll seat map at `/flights/<id>/` where seats can be picked and
booked. *First available seat* in the header assigns the lowest-numbered free
seat instead. The htmx/Tailwind smoke-test page
still lives at http://127.0.0.1:8000/htmx-demo/.

If you run with `--noreload`, note that Django's cached template loader will not
pick up template edits until you restart the server.

## Frontend Workflow

Tailwind compiles `ars/static/src/input.css` into `ars/static/css/tailwind.css`,
scanning `ars/templates/` for the utility classes you use. Rebuild after editing
templates, or leave a watcher running in a second terminal:

```bash
# one-off build
./bin/tailwindcss -i ars/static/src/input.css -o ars/static/css/tailwind.css --minify

# rebuild on save
./bin/tailwindcss -i ars/static/src/input.css -o ars/static/css/tailwind.css --watch
```

The compiled `tailwind.css` **is** committed, so the app renders correctly for
anyone who hasn't installed the Tailwind binary.

htmx is served locally from the `django-htmx` package (no CDN). `templates/base.html`
loads it via `{% htmx_script %}` and attaches the CSRF token to every htmx request
through `hx-headers` on `<body>`, so `hx-post` works without a per-form `{% csrf_token %}`.

## Project Layout

```
ars/
├── ars/                 # Django project package (settings, urls, views)
├── reservations/        # core booking domain (models, selectors, views)
├── assistant/           # natural-language seat search
├── templates/           # Project-level templates (base.html, demo.html)
│   └── reservations/    # flight_list.html, flight_detail.html, _seat_map.html
├── static/
│   ├── src/input.css    # Tailwind source
│   ├── css/tailwind.css # compiled output (committed)
│   └── js/              # seatmap.js (pan/zoom), seat-selection.js (panel)
└── manage.py
```

## Notes

- `ALLOWED_HOSTS` permits `192.168.254.100` alongside `localhost` and `127.0.0.1`,
  so the app is reachable both over the LAN and locally.
- `SECRET_KEY` is loaded from `.env` via python-dotenv and is never committed.
- `DEBUG = True` is a development-only setting and must change before any real
  deployment.
- `htmx-demo/` and `ars/ars/views.py` are throwaway scaffolding to prove the
  frontend stack works. Delete them once real views exist.
