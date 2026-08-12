# Plan — Flight list

Status: **as-built**, written after the fact.
Landed in `e113ffd` (PR #3). 7 tests in `tests/test_flight_list.py`.

The departures board at `/`, and the first screen the project ever had.

---

## 1. What was built

`GET /` renders every flight in departure order with the seats remaining on
each, and each bookable row links to its seat map.

---

## 2. Decisions

### Availability is counted, not queried per flight

`flight_rows()` returns a `FlightRow` per flight — the flight, the cabin
capacity, and the seats available — built from **two queries no matter how many
flights exist**: one for the catalog size, one for the flights with their
booking counts annotated.

```python
capacity = cabin_capacity()
flights = Flight.objects.annotate(booked_count=Count('bookings'))
```

The obvious alternative — asking each flight for its availability — is an N+1
that stays invisible with five demo flights and embarrasses itself with fifty.
There is a test asserting the query count, so a regression fails the suite
instead of quietly costing a query per row.

### The view hands the template finished rows

`FlightRow` carries `is_full` and `has_departed` as properties. The template
does no arithmetic and no comparison against "now" — it picks a branch. This is
the same discipline the seat map follows with `Cabin`.

### An unbookable flight is an `<a>` with no href

A departed or full flight must not look clickable. Rather than a disabled-styled
link that still navigates, or an `href="#"` that goes nowhere, the anchor is
rendered **without an `href` at all** — which makes it inert and unfocusable by
the browser's own rules, no JavaScript involved:

```html
<a {% if row.has_departed or row.is_full %}aria-disabled="true"
   {% else %}href="{% url 'flight-detail' row.flight.id %}"{% endif %}>
```

### All flights are listed, not just upcoming ones

Departed flights stay on the board, greyed out and marked *Departed*, rather
than disappearing. On a kiosk, a flight vanishing from the list is
indistinguishable from a flight that was never there.

---

## 3. Interaction detail

Rows were built as static cards first, and made to *look* interactive in a
second pass before any destination existed: hover lift, sky border, a chevron
that slides. That ordering was deliberate — the destination page did not exist
yet, and a link to a missing route is a `NoReverseMatch`, not a placeholder.

---

## 4. Tests

- rows come back in departure order
- availability is derived from bookings, and a booking on one flight cannot
  affect another
- the seat catalog seeded to 180
- **query count is exactly 2**, independent of flight count
- the view renders flights, the empty state, and links to each seat map
- no `{#` leaks into the rendered page (see §5)

---

## 5. Two traps found here

**Django's `{# #}` comments are single-line only.** A multi-line one renders as
visible text on the page. This shipped briefly and was caught by screenshotting
the real render rather than trusting the markup. There is now a test asserting
`{#` never appears in the response — it applies to every page, but it was born
here.

**`runserver --noreload` serves stale templates.** Django's cached template
loader is active in development, and the autoreloader is what normally clears
it. With `--noreload`, template edits appear to have no effect. This cost real
debugging time; it is noted in the README.

---

## 6. Left open

- The list shows every flight ever seeded; there is no paging or date filter.
- `seed_flights` is the only way to create flights without the admin.
