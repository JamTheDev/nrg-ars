# Plan — Booking flow

Status: **as-built**, written after the fact.
Landed in `aed1ca1` (PR #4). 26 tests across `tests/test_booking.py` and
`tests/test_booking_views.py`, plus a real two-browser race.

Core requirements 2 and 3: assign the first available seat, and assign a
specific one.

---

## 1. What was built

- **Specific seats** — pick seats → *Continue* → a name field per seat →
  *Confirm booking* → `POST /flights/<id>/book/`
- **First available** — header button → one name field →
  `POST /flights/<id>/book/first/`, taking the lowest-numbered free seat

Both return the refreshed cabin swapped into `#seat-map`, plus a status banner
and the header's availability count swapped out of band.

---

## 2. Concurrency policy

Confirmed with the repository owner: **let every request try, and let whoever
commits first win.**

Nothing is checked for availability up front, because a check that passes proves
nothing by the time the `INSERT` runs. `UniqueConstraint(flight, seat)` decides,
and the loser is told which seat went.

`select_for_update()` is deliberately absent. Django's SQLite backend inherits
`has_select_for_update = False` and the query compiler skips the locking branch
entirely — **no SQL is emitted and no error is raised.** Code that calls it
looks protected while doing nothing, which is worse than not calling it.

### The one exception

Blocking the loser is right when the passenger asked for *1A*. When they asked
for "any seat", refusing them because someone else took 1A serves nobody, so
first-available walks to the next free seat instead — up to
`MAX_ASSIGN_RETRIES = 3`, since each loss means another kiosk committed in the
gap.

### A party is all-or-nothing

Serving two of three passengers and charging for it is worse than refusing.
Each insert runs in its own savepoint inside one transaction:

```python
with transaction.atomic():                 # the party
    for request in requests:
        try:
            with transaction.atomic():     # savepoint per seat
                Booking.objects.create(...)
        except IntegrityError as exc:
            raise SeatTakenError(seat.designation) from exc
```

The inner savepoint is what makes the failing seat *nameable*: after an
`IntegrityError`, Django marks the transaction for rollback and any further
query in the same block raises `TransactionManagementError`. Rolling back to the
savepoint keeps the connection usable long enough to raise a specific error,
then the outer transaction discards the whole party — including seats that had
already landed.

---

## 3. Four failures, four messages

Collapsing these into one "invalid seat" would make the UI meaningfully worse:

| Condition | Error | Message |
|---|---|---|
| `"99ZZ"` | `InvalidSeatError` | not a seat designation |
| `"45A"` | `SeatNotFoundError` | this aircraft has rows 1–30 |
| taken | `SeatTakenError` | 12C was just taken |
| blank name | `InvalidPassengerError` | every seat needs a passenger name |

`"45A"` parses perfectly and simply does not exist; telling that passenger their
input was malformed would be a lie. Each exception carries the sentence the
passenger sees, so views translate nothing.

---

## 4. HTTP shape

**Failures return 200, not 4xx.** htmx does not swap error responses by default,
so a 409 would leave the passenger staring at an unchanged page with no
explanation. The status code communicates with the machine; the banner
communicates with the person.

**The response carries the whole cabin**, not the clicked seat. When another
kiosk books concurrently, the swap refreshes every seat, so the map self-heals
rather than drifting.

**The header count is swapped out of band.** It sits outside `#seat-map`, so
without this the cabin updated while the count beside it kept insisting the
flight was empty — found by screenshotting the real page after a booking, not by
the test suite.

Also added here: the SQLite `OPTIONS` from ARCHITECTURE.md §5 — WAL journal mode
and a 20-second busy timeout — now that there is finally a write path.

---

## 5. Structure

| Layer | File | Contents |
|---|---|---|
| Errors | `exceptions.py` | one class per cause, each with its passenger-facing sentence |
| Rules | `services.py` | `parse_designation`, `resolve_seat`, `book_seats`, `assign_first_available` |
| HTTP | `views.py` | parse the POST, delegate, render the fragment |
| Fragments | `_booking_response.html` | map + banner + count |

`SeatRequest(designation, passenger_name)` is the unit of a booking, so a party
is a list of them and a single seat is a list of one.

The name fields are built client-side, one per selected seat, with the hidden
`seat` input adjacent to its `passenger` input — browsers submit fields in DOM
order, so the pairs arrive aligned. The view refuses mismatched counts outright
rather than zipping them and silently booking the wrong seat for someone.

---

## 6. Tests

**Services (14)** — the parse table across four failure modes, all-or-nothing
rollback, the same seat on another flight, first-available ordering and skipping,
a forced lost race proving the retry walks on, a full flight, blank names.

**Views (12)** — books a party, returns fragment-not-page, the OOB banner and
count, 200 on failure, each failure message, `GET` is 405.

**Browser (Playwright, real input)** — select → name → confirm books the seats,
greys them out, clears the selection, closes the panel and refreshes the count;
first-available books 1A; and **a genuine two-tab race**: tab B booked 9C first,
tab A got *"Seat 9C was just taken. Please pick another."* That is the policy in
§2 demonstrated against two real browsers rather than argued about.

---

## 7. Left open

- **No holds and no cancellation.** A booking is immediate and final.
- **`print_flight`** — the literal "print" of core requirement 1 — is still
  unbuilt.
- **The party-size stepper is inert.** See
  [party-size-auto-select.md](06-party-size-auto-select.md).
- **Passenger names are free text**, unvalidated beyond being non-blank.
