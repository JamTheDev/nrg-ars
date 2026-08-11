# Plan — "Reserve for ‹ N ›" auto seat selection

Status: **proposed**, not started.
Target branch: `feature/party-size-auto-select` → PR into `dev`.

Turns the inert party-size stepper into the fastest path through the kiosk:
set the number, get that many seats picked for you, sitting together.

---

## 1. Behaviour being built

Decided with the repository owner:

| Question | Decision |
|---|---|
| What does changing the stepper do? | **Auto-picks N seats immediately.** No extra button. |
| How hard does it try to seat people together? | **Together where possible, nearest block otherwise.** Never refuses while seats exist. |
| Stepper vs hand-picked seats | **The stepper follows the selection.** One number, always equal to the number of highlighted seats. |

Consequences:

- Tapping seats by hand moves the stepper. Moving the stepper changes the seats.
  There is no state where the panel says 3 and two seats are lit.
- Auto-picked seats are ordinary selections: tap one to drop it, tap another to
  swap it. The stepper follows.
- Selection is still **not a hold** (ARCHITECTURE.md §5). Picking four seats
  reserves nothing until *Confirm booking* commits.

---

## 2. The cabin makes "together" awkward — read this first

`CABIN_COLUMNS = 'ABCDEF'` with the aisle in the middle means a row is **3 + 3**.
The longest run of adjacent seats is therefore **three**, not six.

A party of four can *never* be one unbroken block. If "together" is defined as
"one contiguous run", the fallback path is not an edge case — it is what every
family of four hits on an empty aircraft. So "together" is defined in tiers,
and **the same row across the aisle counts as together**, because to a family it
plainly is.

### Preference ladder

Chosen candidate is the first tier that can seat the whole party:

1. **One block** — consecutive free seats, one row, one side of the aisle.
   Where several blocks fit, take the **smallest sufficient** one, front-most on
   a tie. Spending a 3-run on a party of 2 needlessly destroys the only place a
   party of 3 could have sat.
2. **One row, across the aisle** — e.g. `12A 12B 12C` + `12D` for a party of 4,
   or `12B 12C` + `12D 12E` for a party of 4 straddling the aisle.
3. **Two adjacent rows** — e.g. `12A 12B` + `13A 13B`. Directly behind is close
   enough to talk over a seat back.
4. **Fewest groups, front-most** — greedy over the largest blocks. Reached only
   on a genuinely fragmented cabin.

If the flight has fewer free seats than the party, nothing is picked and the
passenger is told (§6).

### Not in scope

Window/aisle preference, seating children beside an adult, extra-legroom rows.
Preference-aware seating is what the natural-language assistant (ARCHITECTURE.md
§7) is for; it should call the same selector once it exists.

---

## 3. Where the logic lives

**Server-side, in `selectors.py`.** Not in JavaScript, even though the browser
already has the whole cabin in the DOM and could do it without a round trip.

Reasons, in order of weight:

1. It is a business rule, and business rules live in `selectors.py`/`services.py`
   so the htmx views, the management commands, and the future NL assistant share
   one implementation (ARCHITECTURE.md §8). A second copy in JS would drift.
2. It is testable without a browser. The tier ladder has enough branches that
   this matters.
3. The client's map is a snapshot. Picking from the server's current state means
   the seats offered are the seats that exist right now, and the response is a
   freshly rendered cabin, so the map self-heals in the same swap.

Cost: one request per increment. Mitigated in §5.

---

## 4. Selector API

```python
# reservations/selectors.py

@dataclass(frozen=True)
class PartyPick:
    seats: list[Seat]          # in cabin order
    tier: Literal['block', 'row', 'adjacent-rows', 'scattered']
    # `tier` drives the wording in the panel: "Seated together" vs
    # "Best we could do — 2 seats apart".


def pick_party_seats(flight: Flight, size: int, keep: Sequence[str] = ()) -> PartyPick:
    """Choose `size` free seats for one party, seated together where possible.

    `keep` holds designations the passenger already chose by hand; they are
    kept and the remainder are picked as close to them as the cabin allows.

    Raises PartyTooLargeError if size exceeds MAX_PARTY_SIZE, and
    NotEnoughSeatsError if the flight cannot seat the party at all.
    """
```

Cost: one query for the seat catalog, one for this flight's bookings — the same
two `seat_map()` already makes, and it should reuse it rather than re-query.

### Sketch

```
free      = seat_map(flight) rows, cells where not is_taken
blocks    = maximal runs of free cells within (row, side of aisle)

tier 1: smallest block with len >= size, front-most on a tie
tier 2: first row whose total free >= size; take from it, minimising blocks
tier 3: first adjacent row pair whose combined free >= size
tier 4: blocks sorted by size descending; take until satisfied

keep:   anchor on the kept seats, extend outward within their block, then
        their row, then the neighbouring row, before falling back to the
        ladder for whatever is still missing
```

For N ≤ 6 across 30 rows this is a scan over ~60 blocks. No optimisation needed.

### Constants

```python
MAX_PARTY_SIZE = 6      # one full row; beyond this even tier 2 cannot hold
```

Lives beside `CABIN_ROWS`/`CABIN_COLUMNS` in settings, since it is derived from
the layout.

---

## 5. HTTP and the client

### Endpoint

`GET /flights/<id>/map/?party=<n>&keep=<12A,12B>` → the `_seat_map.html`
fragment with the chosen seats already carrying `aria-pressed="true"`.

Named `seat-map` — this route is already reserved in ARCHITECTURE.md §6.

HTML over the wire, not JSON: the fragment is the thing that has to change
anyway, and rendering it server-side keeps one template as the source of truth
for how a selected seat looks.

### One rule for selection state after any swap

> After any swap of `#seat-map`, the selection is exactly the seats the server
> rendered as pressed, in document order.

This replaces the current "clear the selection after a swap" rule and covers
both cases with no flags:

- booking response → no seat is pre-pressed → selection clears, as today
- auto-pick response → the chosen seats are pressed → selection adopts them

### Stepper wiring

- `hx-get` on both chevrons, `hx-target="#seat-map"`, `hx-swap="outerHTML"`.
- **Increment** hits the server: `?party=N&keep=<current selection>`.
- **Decrement** does **not**. Dropping seats needs no cabin knowledge, so the
  client just un-presses the most recently added seat. Instant, and it avoids
  the server helpfully rearranging seats the passenger deliberately chose.
- Debounce with `hx-trigger="click delay:200ms"` plus `hx-sync="this:replace"`,
  so tapping ‹ › ‹ › quickly fires one request, not four.
- Clamp to `1..MAX_PARTY_SIZE`; disable the chevron at each end.

### The camera has to follow

An auto-pick that lands in row 12 while the passenger is looking at row 1 looks
exactly like nothing happening — this is the same class of bug as the seat taps
that silently did nothing. After adopting a selection, pan (and zoom out if the
block spans rows) so every picked seat is on screen.

`seatmap.js` gains a `seatmap:focus` listener taking a list of elements — the
mirror of the `seatmap:tap` event it already publishes.

---

## 6. Failure behaviour

| Condition | Response |
|---|---|
| `party` not an integer, or outside 1..6 | 200 + error banner, stepper reverts |
| Party larger than the free-seat count | 200 + "Only 4 seats left on this flight." |
| Flight full | 200 + "Flight PR101 is full." |
| Seats found but split up | 200 + picks made, panel notes "not seated together" |

Errors reuse `_booking_result.html` as an out-of-band swap, and return **200**
for the reason in §6 of ARCHITECTURE.md — htmx does not swap error responses,
so a 4xx would leave the passenger staring at an unchanged page.

---

## 7. Work breakdown

| # | Step | Touches |
|---|---|---|
| 1 | `pick_party_seats` + tier ladder + exceptions | `selectors.py`, `exceptions.py` |
| 2 | Unit tests for the ladder, `keep`, and failures | `tests/test_party_pick.py` |
| 3 | Render pre-selected seats in the fragment | `_seat.html`, `_seat_map.html` |
| 4 | `seat-map` view + route | `views.py`, `urls.py` |
| 5 | Adopt-selection-after-swap rule | `seat-selection.js` |
| 6 | Stepper wiring, clamping, debounce | `flight_detail.html`, `seat-selection.js` |
| 7 | `seatmap:focus` camera API | `seatmap.js` |
| 8 | Browser verification | Playwright |
| 9 | ARCHITECTURE.md §6 update, remove "stepper is inert" from §11 | docs |

Steps 1–2 are independently mergeable and carry the real complexity. If this
needs splitting, cut it there.

---

## 8. Tests

**Selector** — a cabin seeded to shape, then asserting the exact seats chosen:

- party of 2 with both a 2-run and a 6-run free → takes the 2-run (exact fit)
- party of 3 on an empty cabin → `1A 1B 1C`
- party of 4 on an empty cabin → same row across the aisle, not two rows
- row 12 has 4 free but split 1 + 3 → still one row, minimal blocks
- no row can hold the party → adjacent rows
- fragmented cabin → fewest groups, front-most, and `tier == 'scattered'`
- free seats fewer than the party → `NotEnoughSeatsError`, nothing picked
- `keep=['12B']` for a party of 3 → extends within row 12, keeps 12B
- query count stays at 2 regardless of party size

**View** — fragment (not a page) with exactly N seats `aria-pressed="true"`;
bad `party` values produce the error banner at 200.

**Browser (Playwright, real input)** — the checks a test client cannot make:

- stepper to 3 → 3 seats highlighted, panel shows 3 chips and `$150`
- picked seats are on screen afterwards (camera followed)
- tapping a 4th seat by hand → stepper reads 4
- decrement → newest seat drops, no request fired
- rapid ‹ › ‹ › → one request, final state correct

---

## 9. Risks

| Risk | Handling |
|---|---|
| A request per stepper tap feels laggy | Debounce + `hx-sync`; decrement is client-only |
| Auto-picked seats land off screen | `seatmap:focus`, verified in the browser |
| Swap wipes a deliberate hand-picked seat | `keep` carries the current selection on every increment |
| "Together" disappoints on a 3+3 cabin | Tier 2 treats one row as together; panel says when it could not |
| Party size and selection disagree | Single source of truth: selection count *is* the stepper value |

---

## 10. Open questions

1. **Decrement policy** — drop the most recently added seat (proposed), or
   re-pick a tighter block for the smaller party? Dropping is predictable;
   re-picking gives a better final arrangement. Proposal: drop.
2. **`MAX_PARTY_SIZE = 6`** — right cap for a kiosk, or allow more and let the
   scattered tier handle it?
3. **Does the panel need to name the tier?** "Seated together" vs "Best
   available — not seated together" is honest, but it is another string to
   design. Proposal: show it only when the party is split.
