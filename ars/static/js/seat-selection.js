/*
 * Seat selection and the reservation summary panel.
 *
 * Presentation only: selection lives in the page, nothing is sent to the
 * server and no seat is held. The panel opens on the first selection or on
 * Continue -- those triggers are placeholders until the booking flow is
 * designed.
 *
 * Selected state is expressed as aria-pressed on the seat button, styled from
 * static/src/input.css. Tailwind never sees classes that live only in JS, so
 * state classes belong in the stylesheet, not here.
 */
(function () {
  'use strict';

  var panel = document.getElementById('reserve-panel');
  var map = document.getElementById('seat-map');
  var viewport = document.getElementById('seat-map-viewport');
  if (!panel || !map) return;

  var chips = document.getElementById('reserve-seats');
  var totalOutput = document.getElementById('reserve-total');
  var emptyNote = document.getElementById('reserve-empty');
  var barWrap = document.getElementById('reserve-bar-wrap');
  var mapControls = document.getElementById('map-controls');

  var fare = parseFloat(panel.dataset.fare) || 0;
  var currency = panel.dataset.currency || '';

  var selected = []; // designations, in the order they were picked
  var locked = false; // true once the party is being named: the cabin freezes

  function seatButton(designation) {
    return map.querySelector('[data-seat="' + designation + '"]');
  }

  function formatMoney(amount) {
    return currency + amount.toFixed(2).replace(/\.00$/, '');
  }

  /* The bar and the zoom controls both sit where the panel opens, so they move
   * out of its way rather than disappearing behind it. */
  function openPanel() {
    panel.dataset.open = 'true';
    panel.setAttribute('aria-hidden', 'false');
    if (barWrap) barWrap.dataset.panelOpen = 'true';
    if (mapControls) mapControls.dataset.panelOpen = 'true';
  }

  function closePanel() {
    panel.dataset.open = 'false';
    panel.setAttribute('aria-hidden', 'true');
    if (barWrap) delete barWrap.dataset.panelOpen;
    if (mapControls) delete mapControls.dataset.panelOpen;
  }

  function deselect(designation) {
    selected = selected.filter(function (value) {
      return value !== designation;
    });
    var button = seatButton(designation);
    if (button) button.setAttribute('aria-pressed', 'false');
    render();
  }

  function renderChips() {
    chips.replaceChildren();

    selected.forEach(function (designation) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className =
        'rounded border border-slate-300 px-2 py-1 font-mono text-sm text-slate-700 ' +
        'transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700';
      chip.textContent = designation;
      chip.title = 'Remove ' + designation;
      chip.setAttribute('aria-label', 'Remove seat ' + designation);
      chip.addEventListener('click', function () {
        deselect(designation);
      });
      chips.appendChild(chip);
    });
  }

  function render() {
    renderChips();
    totalOutput.textContent = formatMoney(selected.length * fare);
    emptyNote.hidden = selected.length > 0;
    renderParty();
  }

  var DOUBLE_TAP_MS = 400;
  var lastTap = { designation: null, at: 0 };

  function toggle(seat) {
    if (locked || !seat || seat.disabled || !map.contains(seat)) return;

    var designation = seat.dataset.seat;
    var now = Date.now();

    /* Swallow the second half of a double-tap. Toggling twice in a blink
     * leaves the seat exactly as it was, which reads to the passenger as the
     * seat map ignoring them. Deselecting stays available a moment later. */
    if (lastTap.designation === designation && now - lastTap.at < DOUBLE_TAP_MS) {
      lastTap.at = now;
      return;
    }
    lastTap = { designation: designation, at: now };

    if (seat.getAttribute('aria-pressed') === 'true') {
      deselect(designation);
      return;
    }

    /* The cap applies however seats are chosen. Capping only the stepper would
     * let the same booking exceed it by tapping. */
    if (selected.length >= maxParty) {
      flashLimit();
      return;
    }

    seat.setAttribute('aria-pressed', 'true');
    selected.push(designation);
    render();
    openPanel();
  }

  /* Pointer taps arrive from the camera, which knows whether the press was a
   * tap or the end of a pan -- and which element it really started on. */
  if (viewport) {
    viewport.addEventListener('seatmap:tap', function (event) {
      toggle(event.detail.target.closest('.seat'));
    });
  }

  /* Keyboard activation still comes through as a click. MouseEvent.detail is 0
   * for Enter/Space, which is what separates it from a pointer click already
   * handled above -- without this guard a seat would toggle twice.
   *
   * Bound to the viewport, not to the map: a booking swap replaces #seat-map
   * wholesale and a listener on it would go with it. */
  if (viewport) {
    viewport.addEventListener('click', function (event) {
      if (event.detail !== 0) return;
      toggle(event.target.closest('.seat'));
    });
  }

  document.querySelectorAll('[data-panel="close"]').forEach(function (button) {
    button.addEventListener('click', function () {
      showStep('summary');
      closePanel();
    });
  });

  /* Escape steps back one thing at a time: out of the search box first, then
   * out of the panel. */
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (barMode() === 'search') {
      showBar('stepper');
      return;
    }
    closePanel();
  });

  /* ---- Steps: summary -> name entry -> POST ---------------------------- */

  var summary = document.getElementById('panel-summary');
  var summaryFooter = document.getElementById('panel-summary-footer');
  var namesForm = document.getElementById('panel-names');
  var firstForm = document.getElementById('panel-first');
  var fields = document.getElementById('passenger-fields');

  /* Anything past the summary is a booking in progress. The seats being named
   * must not move while they are being named, so the cabin locks: unselected
   * seats grey out, no seat responds, and the stepper is disabled. */
  function showStep(step) {
    summary.hidden = step !== 'summary';
    summaryFooter.hidden = step !== 'summary';
    namesForm.hidden = step !== 'names';
    firstForm.hidden = step !== 'first';

    locked = step !== 'summary';
    if (viewport) viewport.dataset.locked = locked ? 'true' : 'false';
    renderParty();
  }

  /* One name per seat, seat and passenger adjacent so the POST pairs them by
   * position -- browsers submit fields in DOM order. */
  function buildNameFields() {
    fields.replaceChildren();

    selected.forEach(function (designation) {
      var label = document.createElement('label');
      label.className = 'block';

      var caption = document.createElement('span');
      caption.className = 'font-mono text-xs font-semibold uppercase tracking-wide text-slate-400';
      caption.textContent = 'Seat ' + designation;

      var seatField = document.createElement('input');
      seatField.type = 'hidden';
      seatField.name = 'seat';
      seatField.value = designation;

      var nameField = document.createElement('input');
      nameField.type = 'text';
      nameField.name = 'passenger';
      nameField.required = true;
      nameField.autocomplete = 'off';
      nameField.placeholder = 'Full name';
      nameField.className =
        'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 outline-none ' +
        'transition focus:border-sky-500 focus:ring-2 focus:ring-sky-200';

      label.append(caption, seatField, nameField);
      fields.appendChild(label);
    });

    var firstInput = fields.querySelector('input[type="text"]');
    if (firstInput) firstInput.focus();
  }

  document.querySelectorAll('[data-action="continue"]').forEach(function (button) {
    button.addEventListener('click', function () {
      openPanel();
      if (!selected.length) return; // nothing to name yet
      buildNameFields();
      showStep('names');
    });
  });

  document.querySelectorAll('[data-action="first-available"]').forEach(function (button) {
    button.addEventListener('click', function () {
      openPanel();
      showStep('first');
      var input = firstForm.querySelector('input[name="passenger"]');
      if (input) input.focus();
    });
  });

  document.querySelectorAll('[data-action="back"]').forEach(function (button) {
    button.addEventListener('click', function () {
      showStep('summary');
    });
  });

  /* ---- The reserve bar has two modes ----------------------------------
   *
   * Count the party by hand, or describe what you want. Same bar, same
   * endpoint underneath; the chat button swaps which one is showing and the
   * back arrow returns.
   */

  var barStepper = document.getElementById('bar-stepper');
  var barSearch = document.getElementById('bar-search');

  function showBar(mode) {
    if (!barStepper || !barSearch) return;
    barStepper.hidden = mode !== 'stepper';
    barSearch.hidden = mode !== 'search';

    if (mode === 'search') {
      var input = barSearch.querySelector('input[name="q"]');
      if (input) input.focus();
    }
  }

  function barMode() {
    return barSearch && !barSearch.hidden ? 'search' : 'stepper';
  }

  document.querySelectorAll('[data-bar]').forEach(function (button) {
    button.addEventListener('click', function () {
      showBar(button.dataset.bar);
    });
  });

  /* ---- Party size ------------------------------------------------------
   *
   * The stepper has no state of its own: it displays the number of selected
   * seats. Picking seats by hand moves it; moving it changes the seats. There
   * is no arrangement where the panel claims 3 and two seats are lit.
   */

  var partyCount = document.querySelector('[data-party="count"]');
  var partyUp = document.querySelector('[data-party="increment"]');
  var partyDown = document.querySelector('[data-party="decrement"]');
  var maxParty = parseInt(panel.dataset.maxParty, 10) || 6;
  var mapUrl = panel.dataset.mapUrl;
  var pending = null;
  var desired = 0; // party size being asked for, which leads the selection
  var limitNote = document.getElementById('party-limit-note');
  var limitTimer = null;

  function renderParty() {
    // Whenever the state settles, the target and the selection agree again.
    desired = selected.length;
    if (partyCount) partyCount.textContent = String(selected.length);
    if (partyUp) partyUp.disabled = locked || selected.length >= maxParty;
    if (partyDown) partyDown.disabled = locked || selected.length === 0;
  }

  /* Say why a tap did nothing. A seat that refuses silently is the bug this
   * project has already shipped once. */
  function flashLimit() {
    if (!limitNote) return;

    limitNote.hidden = false;
    // Force a reflow between display and the transition. Without a resolved
    // starting style there is nothing to transition from and the note simply
    // appears -- a requestAnimationFrame alone is not reliably enough.
    void limitNote.offsetWidth;
    limitNote.dataset.visible = 'true';

    clearTimeout(limitTimer);
    limitTimer = setTimeout(function () {
      limitNote.dataset.visible = 'false';
      limitTimer = setTimeout(function () {
        limitNote.hidden = true;
      }, 200);
    }, 2500);
  }

  function focusSeats(elements) {
    if (!viewport || !elements.length) return;
    viewport.dispatchEvent(
      new CustomEvent('seatmap:focus', { detail: { targets: elements } })
    );
  }

  /* Growing the party needs the cabin, so it asks the server. Shrinking does
   * not: dropping the seat most recently added is something the page can do on
   * its own, instantly, without the server helpfully rearranging seats the
   * passenger deliberately chose. */
  /* Taps accumulate into a target rather than each asking for "one more than
   * the current selection". Four quick taps from two is a party of six, not
   * four requests that all ask for three. */
  function grow() {
    if (locked || !mapUrl || !window.htmx) return;

    var target = Math.min(maxParty, Math.max(desired, selected.length) + 1);
    if (target === desired) {
      flashLimit();
      return;
    }
    desired = target;
    if (partyCount) partyCount.textContent = String(desired);

    clearTimeout(pending);
    pending = setTimeout(function () {
      window.htmx.ajax('GET', mapUrl, {
        target: '#seat-map',
        swap: 'outerHTML',
        values: { party: desired, keep: selected.join(',') },
      });
    }, 200);
  }

  function shrink() {
    if (locked || !selected.length) return;
    deselect(selected[selected.length - 1]);
  }

  if (partyUp) partyUp.addEventListener('click', grow);
  if (partyDown) partyDown.addEventListener('click', shrink);

  /* One rule for every swap: the selection is whatever the server rendered as
   * pressed, in document order.
   *
   * A booking response presses nothing, so the selection clears. A party pick
   * presses N seats, so the selection adopts them. No flag distinguishes the
   * two, and the client never disagrees with the map it is looking at. */
  document.body.addEventListener('htmx:afterSwap', function () {
    var current = document.getElementById('seat-map');
    // Identity, not the event target: which element htmx reports for an
    // outerHTML swap varies, but a replaced map is never the same node.
    if (!current || current === map) return;

    map = current;
    var pressed = Array.prototype.slice.call(
      map.querySelectorAll('.seat[aria-pressed="true"]')
    );
    selected = pressed.map(function (seat) {
      return seat.dataset.seat;
    });
    render();
    showStep('summary');

    var status = document.getElementById('booking-status');
    if (selected.length) {
      openPanel();
      focusSeats(pressed);
    } else if (status && status.dataset.status === 'ok') {
      closePanel();
    }
  });

  render();
})();
