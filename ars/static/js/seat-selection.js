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

  var fare = parseFloat(panel.dataset.fare) || 0;
  var currency = panel.dataset.currency || '';

  var selected = []; // designations, in the order they were picked

  function seatButton(designation) {
    return map.querySelector('[data-seat="' + designation + '"]');
  }

  function formatMoney(amount) {
    return currency + amount.toFixed(2).replace(/\.00$/, '');
  }

  function openPanel() {
    panel.dataset.open = 'true';
    panel.setAttribute('aria-hidden', 'false');
    if (barWrap) barWrap.dataset.panelOpen = 'true';
  }

  function closePanel() {
    panel.dataset.open = 'false';
    panel.setAttribute('aria-hidden', 'true');
    if (barWrap) delete barWrap.dataset.panelOpen;
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
  }

  function toggle(seat) {
    if (!seat || seat.disabled || !map.contains(seat)) return;

    var designation = seat.dataset.seat;
    if (seat.getAttribute('aria-pressed') === 'true') {
      deselect(designation);
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

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closePanel();
  });

  /* ---- Steps: summary -> name entry -> POST ---------------------------- */

  var summary = document.getElementById('panel-summary');
  var summaryFooter = document.getElementById('panel-summary-footer');
  var namesForm = document.getElementById('panel-names');
  var firstForm = document.getElementById('panel-first');
  var fields = document.getElementById('passenger-fields');

  function showStep(step) {
    summary.hidden = step !== 'summary';
    summaryFooter.hidden = step !== 'summary';
    namesForm.hidden = step !== 'names';
    firstForm.hidden = step !== 'first';
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

  /* The POST returns a freshly rendered cabin, so every seat button is a new
   * element and the old selection no longer refers to anything. Drop it. */
  document.body.addEventListener('htmx:afterSwap', function () {
    var current = document.getElementById('seat-map');
    // Identity, not the event target: which element htmx reports for an
    // outerHTML swap varies, but a replaced map is never the same node.
    if (!current || current === map) return;

    map = current;
    selected = [];
    render();
    showStep('summary');

    var status = document.getElementById('booking-status');
    if (status && status.dataset.status === 'ok') closePanel();
  });

  render();
})();
