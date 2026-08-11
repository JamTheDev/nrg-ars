# Plan — Seat map and kiosk camera

Status: **as-built**, written after the fact.
Landed in `e113ffd` (PR #3), fixed in `4b4002a` (PR #4).
11 tests in `tests/test_seat_map.py`, plus browser verification.

The cabin drawn top-down, navigable like a map: drag, pinch, scroll, flick.

---

## 1. What was built

`GET /flights/<id>/` renders the aircraft from above — rounded nose, wings and
tailplane crossing the fuselage, 30 rows of 3 + aisle + 3 — inside a viewport
that pans and zooms. Taken seats render disabled.

---

## 2. Server side

### Two queries for 180 seats

`seat_map(flight)` returns a `Cabin` of `CabinRow`s, each already split into
`left` and `right` at the aisle. One query for the catalog, one for the seat ids
booked on this flight. A test asserts the count.

### The aisle is a rendering concern, derived from the layout

`aisle_index()` returns `len(CABIN_COLUMNS) // 2`, so the split is computed from
the layout constant rather than hardcoded as `if column == 'C'`. The same
constant defines window/aisle/middle for the natural-language assistant, so a
layout change moves both together.

### The template gets structure, not logic

`Cabin` exposes `seats_total`, `seats_taken`, `seats_available`, `columns_left`,
`columns_right`. The template iterates and branches; it never computes.

---

## 3. The camera

Vanilla Pointer Events in `static/js/seatmap.js`. No library, nothing from a
CDN — consistent with htmx being served locally.

### The transform lives above the swappable fragment

```
#seat-map-viewport   clips, owns the gestures
└── #seat-map-canvas transform: translate(...) scale(...)
    └── #seat-map    ← the fragment booking POSTs replace
```

This is the whole reason the layering exists. When a booking swaps the cabin,
the camera is untouched, so the passenger keeps looking at the row they were
looking at. Putting the transform on the fragment would reset the view on every
booking.

### Behaviour

Drag to pan, flick to glide with friction, two-finger pinch, wheel zoom, and
double-tap — all zooming about the point under the cursor or fingers, so the
cabin does not slide away while zooming. Pan is clamped so the aircraft can
never be flung off screen. It opens fit-to-viewport.

---

## 4. The bug that mattered: taps are not clicks

**Symptom:** clicking a seat did nothing.

**Cause, in two independent parts.**

`setPointerCapture()` — needed so a pan that leaves the viewport keeps tracking
— also retargets the compatibility mouse events that follow it. The real
`click` is delivered to the **viewport**, not to the seat under the finger. A
plain click listener on the seat never fires.

So the camera measures the gesture itself and publishes a `seatmap:tap`
CustomEvent carrying the `pointerdown` target, which is the last honest answer
available. Keyboard activation still arrives as a `click`, told apart by
`MouseEvent.detail === 0`; without that guard a pointer tap would toggle twice.

The second part shipped and had to be fixed in `4b4002a`:

- The tap/pan threshold was **6px of accumulated travel**. Presses on a
  trackpad or touchscreen routinely drift ~10px, so ordinary taps were being
  classified as pans and dropped.
- Measuring *travel* rather than *displacement* made it worse: a hand jittering
  back and forth over one spot accumulated distance without going anywhere.

Now a press is judged by how far it strays from where it went down, threshold
14px. Double-tapping a seat also came out as nothing — the two taps toggled it
on and straight back off — so a repeat tap within 400ms is swallowed and
double-tapping a seat no longer zooms.

### Why it was not caught first time

The original verification dispatched **synthetic** pointer events. Synthetic
events make `setPointerCapture()` throw, so the capture path was never
exercised, and they have zero drift by construction. The harness confirmed code
that could not work in a real browser.

The fix was verified with Playwright driving real input, which reproduced the
failure on the first run. **Anything gesture-related needs a real browser and
real input; a synthetic-event harness proves almost nothing here.**

---

## 5. Tests

**Django** — cabin covers every row, splits at the aisle, occupancy is
per-flight, counts derive from the grid, two queries across 180 seats, the view
renders seats and marks taken ones, unknown flight is 404.

**Browser (Playwright, real input)** — the input matrix, all of which must hold:

| Input | Expected |
|---|---|
| tap, 0/4/8/13px drift | selects |
| 120px drag | pans, selects nothing |
| double click | selects once |
| touch tap | selects |

---

## 6. Left open

- No keyboard navigation between seats beyond native tab order — 180 tab stops.
- The open reservation panel covers the right of the map; the map is pannable,
  so it is a nuisance rather than a blocker.
- `print_flight`, the text rendering of the same data (ARCHITECTURE.md §2), is
  still unbuilt.
