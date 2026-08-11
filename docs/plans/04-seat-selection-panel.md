# Plan — Seat selection and the reservation panel

Status: **as-built**, written after the fact.
Landed in `e113ffd` (PR #3), refined in `4b4002a` (PR #4).

Tapping seats fills a side panel with what has been chosen and what it costs.

---

## 1. What was built

Selecting a seat turns it green and slides in a panel from the right: the
*PlaneServe* header, the chosen seats as chips, a running total, and *Continue*.
Multi-select; tapping a selected seat deselects it; chips are tappable to remove
a seat. The panel also opens from the reserve bar's *Continue*.

Built from a sketch, which is why it is a side sheet with dotted leaders rather
than a modal.

---

## 2. The rule that is easy to get wrong

> **State that JavaScript toggles is styled from `input.css`, not by swapping
> Tailwind classes.**

Tailwind only compiles classes it can *see* in the scanned templates. A class
named only inside a `.js` file produces **no CSS at all** and fails silently at
runtime — the JavaScript works, the DOM updates, and nothing changes on screen.

So the selected-seat green, the panel's off-canvas transform, and the reserve
bar's shift are plain CSS rules keyed off attributes:

```css
.seat[aria-pressed="true"] { ... }
#reserve-panel[data-open="true"] { transform: translateX(0); }
```

`aria-pressed` was chosen over a `data-selected` attribute because it is also
what a screen reader announces — one attribute, two jobs.

---

## 3. Decisions

### Selection lives in the page, and holds nothing

The selection is an array in JavaScript. Nothing is sent to the server, and no
seat is reserved by choosing it. This follows from the concurrency policy
(ARCHITECTURE.md §5): a seat is yours when your insert commits, not before.
A passenger can therefore have 12C selected and still lose it.

### Pricing is a placeholder, deliberately visible as one

A total needs a price, and there is no fare anywhere in the schema. Rather than
invent a model field, `SEAT_FARE = Decimal('50.00')` and `CURRENCY_SYMBOL` went
into settings, passed to the panel as data attributes and multiplied by the
selection count in JS.

It is config, so it is obvious it is temporary. Real per-flight pricing belongs
on `Flight` (or a fare class) and is a migration.

### Chips are removal controls

Each chip is a button that deselects its seat. Cheap to add, and it means a
passenger who has zoomed away from a seat can still drop it.

---

## 4. Panel steps

The panel gained steps when booking arrived (see [booking-flow.md](05-booking-flow.md)):

| Step | Content |
|---|---|
| summary | chips, total, *Continue* |
| names | one name field per selected seat, *Confirm booking*, *Back* |
| first | a single name field for first-available |

Only one is visible at a time, toggled with the `hidden` attribute — which works
next to Tailwind's `flex` only because Tailwind's preflight declares
`[hidden] { display: none !important }`. Worth knowing before removing preflight.

---

## 5. Selection state after a swap

A booking POST replaces `#seat-map` wholesale, so every seat button is a new
element and the old selection refers to detached nodes. After a swap the
selection is cleared and the panel returns to its summary step.

Two details found while building this:

- The swap detection compares the current `#seat-map` node against the cached
  one rather than trusting `event.detail.target`, because which element htmx
  reports for an `outerHTML` swap varies. A replaced map is never the same node.
- The keyboard `click` listener had to move from `#seat-map` to the viewport.
  Bound to the map, it was destroyed by the first booking swap and keyboard
  selection silently died from then on.

The [party-size plan](06-party-size-auto-select.md) replaces "clear after a swap"
with "adopt whatever the server rendered as pressed", which covers both cases
without a flag.

---

## 6. Tests

Django asserts the panel renders with its fare data attributes, that available
seats carry `aria-pressed="false"` while taken seats carry no `aria-pressed` at
all, and that the selection script is loaded.

The behaviour itself is browser-verified: select, multi-select, deselect, the
running total, and that a seat in a *replaced* map still responds.

---

## 7. Left open

- The party-size stepper is inert — that is the next plan.
- Selection is per-page; navigating away and back loses it.
- Nothing caps how many seats one person can select.
