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
  var FOCUS_MS = 450; // long enough to read as movement, short enough to wait out
  var FOCUS_MAX_SCALE = 1.25; // comfortable reading zoom; do not shove seats in a face
  var FOCUS_FILL = 0.55; // fraction of the viewport the party should occupy

  var reduceMotion =
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
  var flight = null; // in-progress camera move

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

  /* Where a zoom about a screen point lands, without applying it. Holding that
   * point still is what keeps the cabin from sliding away under the fingers. */
  function zoomTarget(clientX, clientY, factor) {
    var next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
    var rect = viewport.getBoundingClientRect();
    var px = clientX - rect.left;
    var py = clientY - rect.top;
    var ratio = next / scale;

    return { scale: next, tx: px - ratio * (px - tx), ty: py - ratio * (py - ty) };
  }

  /* Continuous gestures -- wheel and pinch -- are already the passenger's own
   * movement, so they apply instantly. Animating them would lag the fingers. */
  function zoomAt(clientX, clientY, factor) {
    var to = zoomTarget(clientX, clientY, factor);
    if (to.scale === scale) return;

    stopFlight();
    scale = to.scale;
    tx = to.tx;
    ty = to.ty;
    render();
  }

  /* Discrete zooms -- buttons, double-tap -- are a request to be somewhere
   * else, so they travel there. */
  function zoomStep(clientX, clientY, factor) {
    var to = zoomTarget(clientX, clientY, factor);
    if (to.scale === scale) return;
    flyTo(to.scale, to.tx, to.ty, 260);
  }

  function zoomCentre(factor) {
    var rect = viewport.getBoundingClientRect();
    zoomStep(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  function fitTarget() {
    var width = canvas.offsetWidth;
    var height = canvas.offsetHeight;
    if (!width || !height) return null;

    var padding = 32;
    var next = Math.min(
      MAX_SCALE,
      Math.max(
        MIN_SCALE,
        Math.min(
          (viewport.clientWidth - padding * 2) / width,
          (viewport.clientHeight - padding * 2) / height
        )
      )
    );
    return {
      scale: next,
      tx: (viewport.clientWidth - width * next) / 2,
      ty: (viewport.clientHeight - height * next) / 2,
    };
  }

  function fit(animated) {
    var to = fitTarget();
    if (!to) return;

    if (animated) {
      flyTo(to.scale, to.tx, to.ty, 320);
      return;
    }
    stopFlight();
    scale = to.scale;
    tx = to.tx;
    ty = to.ty;
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

  function stopFlight() {
    if (flight) {
      cancelAnimationFrame(flight);
      flight = null;
    }
  }

  /* Move the camera to a given scale and offset over FOCUS_MS.
   *
   * Cutting from one view to another leaves the passenger to work out that the
   * cabin moved at all; travelling there shows them where it went. Eased out,
   * so it leaves quickly and settles gently. */
  function flyTo(targetScale, targetTx, targetTy, duration) {
    var ms = duration || FOCUS_MS;
    stopGlide();
    stopFlight();

    if (reduceMotion) {
      scale = targetScale;
      tx = targetTx;
      ty = targetTy;
      render();
      return;
    }

    var fromScale = scale;
    var fromX = tx;
    var fromY = ty;
    var started = null;

    var step = function (now) {
      if (started === null) started = now;
      var progress = Math.min(1, (now - started) / ms);
      var eased = 1 - Math.pow(1 - progress, 3);

      scale = fromScale + (targetScale - fromScale) * eased;
      tx = fromX + (targetTx - fromX) * eased;
      ty = fromY + (targetTy - fromY) * eased;
      render();

      flight = progress < 1 ? requestAnimationFrame(step) : null;
    };
    flight = requestAnimationFrame(step);
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
    /* The zoom controls sit inside the viewport. Capturing the pointer would
     * retarget their click to the viewport and leave the buttons dead -- the
     * same trap the seats fell into. Leave a press on them entirely alone.
     *
     * `moved` is reset too: a stale value from an earlier pan would make the
     * click suppressor below swallow the button's click. */
    if (event.target.closest('#map-controls')) {
      moved = 0;
      downTarget = null;
      return;
    }

    stopGlide();
    stopFlight();
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

    var rects = targets.map(function (el) {
      return el.getBoundingClientRect();
    });
    var view = viewport.getBoundingClientRect();

    // Screen coordinates back into content coordinates, so the target can be
    // worked out without touching the live transform first.
    var left = Math.min.apply(null, rects.map(function (r) { return r.left; }));
    var right = Math.max.apply(null, rects.map(function (r) { return r.right; }));
    var top = Math.min.apply(null, rects.map(function (r) { return r.top; }));
    var bottom = Math.max.apply(null, rects.map(function (r) { return r.bottom; }));

    var x1 = (left - view.left - tx) / scale;
    var x2 = (right - view.left - tx) / scale;
    var y1 = (top - view.top - ty) / scale;
    var y2 = (bottom - view.top - ty) / scale;

    // Zoom so the party fills a comfortable share of the viewport -- in for a
    // couple of seats, out for a party spread over several rows.
    var fit = Math.min(
      (view.width * FOCUS_FILL) / (x2 - x1),
      (view.height * FOCUS_FILL) / (y2 - y1)
    );
    var targetScale = Math.min(FOCUS_MAX_SCALE, Math.max(MIN_SCALE, fit));

    flyTo(
      targetScale,
      view.width / 2 - ((x1 + x2) / 2) * targetScale,
      view.height / 2 - ((y1 + y2) / 2) * targetScale
    );
  });

  viewport.addEventListener('dblclick', function (event) {
    // Double-tapping a seat is someone picking a seat emphatically, not asking
    // to zoom. Only empty cabin space zooms.
    if (event.target.closest('.seat')) return;
    zoomStep(event.clientX, event.clientY, 1.6);
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
      else fit(true);
    });
  });

  window.addEventListener('resize', function () {
    fit(false); // a resize is not a journey
  });
  fit(false);
})();
