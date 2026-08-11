/*
 * Kiosk camera for the seat map: drag to pan, pinch or scroll to zoom,
 * double-tap to zoom in, flick to glide.
 *
 * The transform lives on #seat-map-canvas, one level above the #seat-map
 * fragment, so an htmx swap of the map does not reset the camera.
 *
 * Vanilla Pointer Events -- no library, nothing loaded from a CDN.
 */
(function () {
  'use strict';

  var viewport = document.getElementById('seat-map-viewport');
  var canvas = document.getElementById('seat-map-canvas');
  if (!viewport || !canvas) return;

  var MIN_SCALE = 0.15;
  var MAX_SCALE = 3;
  var EDGE_MARGIN = 80; // px of the cabin that must stay on screen
  // How far the pointer may wander and still count as a tap. A press on a
  // touchscreen or trackpad routinely drifts ~10px, so a tight slop reads
  // ordinary taps as pans and silently drops them.
  var DRAG_SLOP = 14;
  var FRICTION = 0.92;
  var MIN_VELOCITY = 0.05;

  var scale = 1;
  var tx = 0;
  var ty = 0;

  var pointers = new Map();
  var lastPan = null; // {x, y} of the single active pointer
  var pinch = null; // {distance, scale}
  var moved = 0; // furthest the pointer strayed from where it went down
  var origin = null; // where it went down
  var downTarget = null; // what the gesture started on, for tap dispatch
  var velocity = { x: 0, y: 0 };
  var glide = null;

  function contentSize() {
    return { width: canvas.offsetWidth * scale, height: canvas.offsetHeight * scale };
  }

  /* Keep at least EDGE_MARGIN of the cabin inside the viewport, so it can
   * never be flung out of sight and lost. */
  function clampPan() {
    var size = contentSize();
    var maxX = viewport.clientWidth - EDGE_MARGIN;
    var minX = EDGE_MARGIN - size.width;
    var maxY = viewport.clientHeight - EDGE_MARGIN;
    var minY = EDGE_MARGIN - size.height;

    tx = Math.min(maxX, Math.max(minX, tx));
    ty = Math.min(maxY, Math.max(minY, ty));
  }

  function render() {
    clampPan();
    canvas.style.transform = 'translate(' + tx + 'px, ' + ty + 'px) scale(' + scale + ')';
  }

  function zoomAt(clientX, clientY, factor) {
    var next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
    if (next === scale) return;

    var rect = viewport.getBoundingClientRect();
    var px = clientX - rect.left;
    var py = clientY - rect.top;
    var ratio = next / scale;

    // Hold the point under the cursor/fingers still while the scale changes.
    tx = px - ratio * (px - tx);
    ty = py - ratio * (py - ty);
    scale = next;
    render();
  }

  function zoomCentre(factor) {
    var rect = viewport.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function fit() {
    var width = canvas.offsetWidth;
    var height = canvas.offsetHeight;
    if (!width || !height) return;

    var padding = 32;
    scale = Math.min(
      MAX_SCALE,
      Math.max(
        MIN_SCALE,
        Math.min(
          (viewport.clientWidth - padding * 2) / width,
          (viewport.clientHeight - padding * 2) / height
        )
      )
    );
    tx = (viewport.clientWidth - width * scale) / 2;
    ty = (viewport.clientHeight - height * scale) / 2;
    render();
  }

  function distanceBetweenPointers() {
    var points = Array.from(pointers.values());
    return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
  }

  function midpointOfPointers() {
    var points = Array.from(pointers.values());
    return { x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 };
  }

  function stopGlide() {
    if (glide) {
      cancelAnimationFrame(glide);
      glide = null;
    }
  }

  function startGlide() {
    if (Math.hypot(velocity.x, velocity.y) < MIN_VELOCITY * 10) return;

    var step = function () {
      tx += velocity.x;
      ty += velocity.y;
      velocity.x *= FRICTION;
      velocity.y *= FRICTION;
      render();
      glide =
        Math.hypot(velocity.x, velocity.y) > MIN_VELOCITY ? requestAnimationFrame(step) : null;
    };
    glide = requestAnimationFrame(step);
  }

  viewport.addEventListener('pointerdown', function (event) {
    stopGlide();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    try {
      viewport.setPointerCapture(event.pointerId);
    } catch (ignored) {
      // Capture is an optimisation for pans that leave the viewport; a pointer
      // that is already gone throws here and the gesture still works without it.
    }

    if (pointers.size === 1) {
      lastPan = { x: event.clientX, y: event.clientY };
      origin = { x: event.clientX, y: event.clientY };
      moved = 0;
      downTarget = event.target;
      velocity = { x: 0, y: 0 };
      viewport.style.cursor = 'grabbing';
    } else if (pointers.size === 2) {
      lastPan = null;
      pinch = { distance: distanceBetweenPointers(), scale: scale };
    }
  });

  viewport.addEventListener('pointermove', function (event) {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointers.size === 2 && pinch) {
      var current = distanceBetweenPointers();
      if (pinch.distance > 0) {
        var centre = midpointOfPointers();
        var target = pinch.scale * (current / pinch.distance);
        zoomAt(centre.x, centre.y, target / scale);
      }
      return;
    }

    if (!lastPan) return;
    var dx = event.clientX - lastPan.x;
    var dy = event.clientY - lastPan.y;
    lastPan = { x: event.clientX, y: event.clientY };

    // Distance from the origin, not distance travelled: a hand that jitters
    // back and forth over the same spot has not panned anywhere.
    if (origin) {
      moved = Math.max(moved, Math.hypot(event.clientX - origin.x, event.clientY - origin.y));
    }
    velocity = { x: dx, y: dy };
    tx += dx;
    ty += dy;
    render();
  });

  /* A press that ended without panning is a tap on whatever it started on.
   *
   * This has to be published from here rather than left to a plain click
   * listener: setPointerCapture() retargets the compatibility mouse events that
   * follow, so the real click lands on the viewport, not on the seat under the
   * finger. The pointerdown target is the last honest answer we get. */
  function dispatchTap() {
    if (moved > DRAG_SLOP || !downTarget) return;
    viewport.dispatchEvent(
      new CustomEvent('seatmap:tap', { detail: { target: downTarget }, bubbles: true })
    );
  }

  function releasePointer(event) {
    pointers.delete(event.pointerId);

    if (pointers.size < 2) pinch = null;
    if (pointers.size === 0) {
      lastPan = null;
      viewport.style.cursor = '';
      if (event.type === 'pointerup') dispatchTap();
      downTarget = null;
      startGlide();
    } else {
      var remaining = Array.from(pointers.values())[0];
      lastPan = { x: remaining.x, y: remaining.y };
    }
  }

  viewport.addEventListener('pointerup', releasePointer);
  viewport.addEventListener('pointercancel', releasePointer);

  viewport.addEventListener(
    'wheel',
    function (event) {
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 1 / 1.12);
    },
    { passive: false }
  );

  /* Bring a set of seats into view. Auto-picking seats the passenger cannot
   * see is indistinguishable from auto-picking nothing.
   *
   * The seats' rectangles are already in screen coordinates under the current
   * transform, so the camera moves by the difference between their centre and
   * the viewport's -- no unpicking of the transform required. */
  viewport.addEventListener('seatmap:focus', function (event) {
    var targets = (event.detail && event.detail.targets) || [];
    if (!targets.length) return;

    function bounds() {
      var rects = targets.map(function (el) {
        return el.getBoundingClientRect();
      });
      return {
        left: Math.min.apply(null, rects.map(function (r) { return r.left; })),
        right: Math.max.apply(null, rects.map(function (r) { return r.right; })),
        top: Math.min.apply(null, rects.map(function (r) { return r.top; })),
        bottom: Math.max.apply(null, rects.map(function (r) { return r.bottom; })),
      };
    }

    var view = viewport.getBoundingClientRect();
    var box = bounds();
    var margin = 0.6; // leave room around the party, and for the panel

    // Zoom out only if the party does not already fit.
    var fits = Math.min(
      (view.width * margin) / (box.right - box.left),
      (view.height * margin) / (box.bottom - box.top)
    );
    if (fits < 1) {
      zoomAt((box.left + box.right) / 2, (box.top + box.bottom) / 2, fits);
      box = bounds();
    }

    stopGlide();
    tx += view.left + view.width / 2 - (box.left + box.right) / 2;
    ty += view.top + view.height / 2 - (box.top + box.bottom) / 2;
    render();
  });

  viewport.addEventListener('dblclick', function (event) {
    // Double-tapping a seat is someone picking a seat emphatically, not asking
    // to zoom. Only empty cabin space zooms.
    if (event.target.closest('.seat')) return;
    zoomAt(event.clientX, event.clientY, 1.6);
  });

  /* A drag that ends over a seat must not read as a tap on that seat. */
  viewport.addEventListener(
    'click',
    function (event) {
      if (moved > DRAG_SLOP) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  document.querySelectorAll('[data-zoom]').forEach(function (button) {
    button.addEventListener('click', function () {
      var action = button.dataset.zoom;
      if (action === 'in') zoomCentre(1.3);
      else if (action === 'out') zoomCentre(1 / 1.3);
      else fit();
    });
  });

  window.addEventListener('resize', fit);
  fit();
})();
