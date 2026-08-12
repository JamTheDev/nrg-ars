# Plan — Reservations domain

Status: **as-built**, written after the fact.
Landed in `a9bdd30` (PR #3).

The four models everything else stands on: flights, a shared catalog of cabin
positions, passengers, and the bookings that join them.

---

## 1. What was built

`Flight`, `Seat`, `Passenger`, `Booking` in one `reservations` app, plus the
migration that seeds the seat catalog and a development command that creates
demo flights.

---

## 2. The decision that shaped everything else

### Seat is a shared catalog, not a per-flight copy

`Seat` holds each **position** in the cabin exactly once — 180 rows for a 30×6
layout — and every flight references that same catalog. Occupancy is a property
of `Booking`, never of `Seat`.

The alternative, copying 180 `Seat` rows per flight, was rejected: it multiplies
storage by the flight count and makes "seat 12C" ambiguous across flights, for
no benefit while every aircraft has the same layout.

**The reversal condition is worth stating plainly:** the day a second cabin
layout appears, this decision flips. `Seat` gains an `Aircraft` foreign key and
`Flight` points at an `Aircraft`. That is the single most likely future
migration in this codebase.

### Availability is derived, never stored

There is no `is_taken` column. A seat is taken on a flight if a `Booking` joins
them, and that is the only place the fact lives:

```python
Seat.objects.exclude(bookings__flight=flight)
```

A stored flag would be a second source of truth that can disagree with the
bookings table, and it would have to be maintained inside every write path.

### One constraint carries the correctness of the whole system

```python
models.UniqueConstraint(fields=['flight', 'seat'], name='unique_seat_per_flight')
```

Everything in the booking path is a nicety layered on top of it. Two kiosks may
both attempt 1A; the database decides. See [booking-flow.md](05-booking-flow.md)
for how that plays out, and ARCHITECTURE.md §5 for why `select_for_update()`
cannot help here.

### Ordering is a domain rule, not a display preference

`Seat.Meta.ordering = ['row', 'column']` is what defines "first available" for
core requirement 2. It lives on the model so the web views and the management
commands cannot disagree about which seat is first.

---

## 3. Seeding the catalog

`CABIN_ROWS = 30` and `CABIN_COLUMNS = 'ABCDEF'` live in settings, and migration
`0002_seed_seats` reads them **once** to create the 180 rows.

They are deliberately *not* read at request time. After the migration runs, the
seeded rows are the source of truth, and changing the settings requires a new
migration rather than silently disagreeing with the database. The seeding uses
`ignore_conflicts=True` so a re-run is harmless, and the reverse deletes the
catalog.

---

## 4. What shipped

| Piece | File |
|---|---|
| Models | `reservations/models.py` |
| Schema | `migrations/0001_initial.py` |
| Catalog seed | `migrations/0002_seed_seats.py` |
| Admin registration | `reservations/admin.py` |
| Demo flights | `management/commands/seed_flights.py` |

`seed_flights` was an addition beyond the request: there is no auth, so the
admin needs a superuser, and an empty database demonstrates nothing. It is
idempotent on flight number.

---

## 5. Loose ends left deliberately

- **`Passenger` is created per booking, never deduplicated.** Two people called
  "J. Santos" are two passengers. Matching people by name would merge strangers.
- **No cancellation path.** A booking is immediate and final.
- **No fare on `Flight`.** Pricing arrived later as a flat `SEAT_FARE` setting
  (see [seat-selection-panel.md](04-seat-selection-panel.md)); real per-flight
  pricing is a migration, not a config edit.
