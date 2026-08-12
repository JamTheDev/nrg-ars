# Plans

One document per feature, in the order they were built.

**As-built** documents were written after the work shipped, reconstructed from
the commits, the code, and the decisions made along the way. They are not
predictions that happened to come true — they exist so the reasoning behind
shipped code, and the traps found while writing it, survive somewhere other
than a pull request description.

**Proposed** documents describe work not yet started.

| Plan | Status | Landed in |
|---|---|---|
| [Reservations domain](01-reservations-domain.md) | as-built | `a9bdd30`, PR #3 |
| [Flight list](02-flight-list.md) | as-built | `e113ffd`, PR #3 |
| [Seat map and kiosk camera](03-seat-map-kiosk-camera.md) | as-built | `e113ffd` + `4b4002a`, PRs #3 and #4 |
| [Seat selection and reservation panel](04-seat-selection-panel.md) | as-built | `e113ffd`, PR #3 |
| [Booking flow](05-booking-flow.md) | as-built | `aed1ca1`, PR #4 |
| [Party-size auto selection](06-party-size-auto-select.md) | built | PR #8 |
| [`print_flight`, the literal print](07-print-flight.md) | built | PR #11 |
| [The AI assistant](08-natural-language-search.md) | built | PR #12 — **read this one first if you are touching the assistant** |

All three core requirements are implemented, and so is the natural-language
search ARCHITECTURE.md §7 designs.

**Start with [08](08-natural-language-search.md) if you are touching the
language side.** Its problem log is the only record of why the prompt, the JSON
schema and the guards look the way they do — most of it was learned by the
feature being confidently wrong.

ARCHITECTURE.md remains the reference for how the system works today. These
documents explain *why it is that way*, including the options that were rejected.
Where the two disagree, ARCHITECTURE.md is right and the plan is history.
