# Plan — `print_flight`, the literal print

Status: **built**, PR #11.
Target branch: `feature/print-flight` → PR into `dev`.

Closes the half of core requirement 1 that the web seat map does not cover.

---

## 1. Why this exists at all

> *"Implement a way to **print** a flight/airplane that displays available
> seating on the plane."*

The seat map displays available seating, and it is the half everyone notices.
But the requirement says *print*, and a pannable, zoomable, colour-coded canvas
is not a print. A terminal rendering of the same cabin is, it costs very little
once `seat_map()` exists, and it is the half that is trivially checkable by
someone with no browser.

**The point is that it shares the query, not the format.** If the command
recomputed availability its own way, the two renderings could disagree, and the
one that disagreed would be whichever nobody was looking at.

---

## 2. Shape

```bash
uv run ars/manage.py print_flight PR101
```

```
PR101  MNL → CEB  2026-08-14 09:30

      A  B  C   D  E  F
  1   .  .  X   .  .  .
  2   X  X  .   .  .  .
  3   .  .  .   .  .  .
 ...
 30   .  .  .   .  .  .

  . = available    X = taken
  178 of 180 available
```

- Flight is named by **number**, not id: `PR101` is what is printed on a
  boarding pass and what an operator would type. Matched case-insensitively.
- Column header and row numbers come from the same `Cabin` the web view uses,
  so a layout change moves both.
- The aisle is the wider gap after column C, derived from `aisle_index()` —
  not a hardcoded space.
- The availability footer mirrors the web header's count.

### Options

| Flag | Effect |
|---|---|
| *(none)* | The block above |
| `--available-only` | Lists free designations one per line, for piping |

`--available-only` earns its place: it makes the command usable from a script
(`print_flight PR101 --available-only | wc -l`) rather than only readable.

---

## 3. Implementation

`reservations/management/commands/print_flight.py`, roughly 60 lines.

```python
class Command(BaseCommand):
    help = 'Print a flight\'s seat map as text.'

    def add_arguments(self, parser):
        parser.add_argument('number')
        parser.add_argument('--available-only', action='store_true')

    def handle(self, number, available_only, **options):
        try:
            flight = Flight.objects.get(number__iexact=number)
        except Flight.DoesNotExist:
            raise CommandError(...) from None

        cabin = selectors.seat_map(flight)
        ...
```

**No new selector.** `seat_map()` already returns rows split at the aisle with
occupancy resolved, which is exactly what the renderer needs.

**Rendering stays in the command**, not in `selectors.py`: text layout is a
presentation concern, the same way the Tailwind grid is.

### Errors

| Condition | Response |
|---|---|
| Unknown flight number | `CommandError` naming the number, exit status 1 |
| No flights at all | `CommandError` suggesting `seed_flights` |
| Cabin not seeded | `CommandError` suggesting `migrate` |

`CommandError` rather than a printed message, so a script can tell the
difference between "no seats free" and "no such flight".

---

## 4. Tests

`tests/test_print_flight.py`, using `call_command` with a `StringIO`:

- a cabin with two known bookings renders the **exact** expected block,
  compared line for line — the format is the deliverable here, so it is what
  the test asserts
- the header line carries number, route and departure time
- taken seats show `X`, free show `.`, and the counts in the footer agree with
  `cabin.seats_available`
- `--available-only` prints one designation per line and nothing else
- an unknown number raises `CommandError` and prints no map
- the command runs in **two queries**, the same as the web view

That last one matters: a naive renderer that asks each row for its seats would
issue 30 queries and nobody would notice from the output.

---

## 5. Work breakdown

| # | Step | Touches |
|---|---|---|
| 1 | The command and its rendering | `management/commands/print_flight.py` |
| 2 | Tests, including the exact-output one | `tests/test_print_flight.py` |
| 3 | README usage, and ARCHITECTURE §2 marked done | docs |

Half a day at most, and it is the last thing standing between this project and
"all three core requirements are implemented".

---

## 6. How they were settled

- **Times print in the kiosk's own zone**, `Asia/Manila`, matching every other
  surface. Stored values stay UTC.
- **No colour.** `.` and `X` read fine, and colour breaks when piped.
- **Flights are named by number only.** One way to name a thing is simpler to
  document than two.
- **Three queries, not two.** The cabin costs two; finding the flight is a
  third, unavoidably. The test asserts three and says why.
