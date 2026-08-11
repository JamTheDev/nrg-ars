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
  var viewport = document.getElementById('seat-map-viewport');
  if (viewport) {
    viewport.addEventListener('seatmap:tap', function (event) {
      toggle(event.detail.target.closest('.seat'));
    });
  }

  /* Keyboard activation still comes through as a click. MouseEvent.detail is 0
   * for Enter/Space, which is what separates it from a pointer click already
   * handled above -- without this guard a seat would toggle twice. */
  map.addEventListener('click', function (event) {
    if (event.detail !== 0) return;
    toggle(event.target.closest('.seat'));
  });

  document.querySelectorAll('[data-panel="close"]').forEach(function (button) {
    button.addEventListener('click', closePanel);
  });

  document.querySelectorAll('[data-action="continue"]').forEach(function (button) {
    button.addEventListener('click', openPanel);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closePanel();
  });

  render();
})();
