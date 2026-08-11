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

Idempotent on flight number — re-running it never duplicates a flight. Flights
can also be added through the admin.

### 6. Start the server

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
