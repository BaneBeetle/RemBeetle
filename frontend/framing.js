/*
 * RemBeetle: frame Rem like a VTuber webcam.
 *
 * The compiled app centres the Live2D model on the canvas and ignores the initialXshift /
 * initialYshift fields of model_dict.json, so the only way to keep the face in the upper part
 * of the frame is to move the model after it loads. This script does exactly that, once per
 * loaded model: it pulls the model down in proportion to its scale (the scale itself comes from
 * kScale in the backend's model_dict.json, which the app doubles at runtime), keeps following
 * scale changes (scroll-to-resize) so the framing stays anchored, and stops touching the model
 * the moment the user drags it. Fail-open: any error leaves the app exactly as it was.
 */
(function () {
  'use strict';
  // -0.85 at a runtime scale of 1.5 (server kScale 0.75). Tuned on the 1000x900 desktop canvas;
  // on tall phone canvases the same value gives a face-and-shoulders close-up.
  var Y_PER_SCALE = -0.85 / 1.5;
  var EPS = 1e-3;
  var state = { model: null, lastY: null };

  function frame() {
    try {
      var getAdapter = window.getLAppAdapter;
      if (typeof getAdapter !== 'function') return;
      var adapter = getAdapter();
      var model = adapter && adapter.getModel && adapter.getModel();
      var matrix = model && model._modelMatrix;
      if (!matrix || typeof matrix.getArray !== 'function') return;

      if (model !== state.model) { state.model = model; state.lastY = null; }
      if (state.lastY !== null && isNaN(state.lastY)) return;   // hands off this model

      var a = matrix.getArray();
      var scale = a[0];
      if (!(scale > 0)) return;                                  // app has not scaled it yet
      var y = Y_PER_SCALE * scale;

      if (state.lastY === null) {
        // Only take over a model that nobody has positioned yet.
        if (Math.abs(a[12]) > EPS || Math.abs(a[13]) > EPS) { state.lastY = NaN; return; }
      } else if (Math.abs(a[13] - state.lastY) > EPS) {
        state.lastY = NaN;                                       // the user dragged it: stop
        return;
      } else if (Math.abs(y - state.lastY) <= EPS) {
        return;                                                  // nothing changed
      }

      if (typeof adapter.setModelPosition === 'function') {
        adapter.setModelPosition(0, y);
      } else if (typeof matrix.setMatrix === 'function') {
        var m = Array.prototype.slice.call(a);
        m[12] = 0; m[13] = y;
        matrix.setMatrix(m);
      } else {
        return;
      }
      state.lastY = matrix.getArray()[13];                       // store the float32 the matrix kept
    } catch (e) { /* fail open */ }
  }

  setInterval(frame, 500);
})();

/*
 * RemBeetle: default background.
 *
 * The compiled app hardcodes /bg/ceiling-window-room-night.jpeg as its default background and
 * remembers the user's choice in localStorage under "backgroundUrl" (a JSON string). This runs
 * before the bundle (framing.js is deferred; the bundle is loaded later by auth-shell.js) and:
 *   1. rebases a stored /bg/ URL onto the current backend origin, so choices saved when the page
 *      was served by the backend itself keep working now that the page lives on Vercel;
 *   2. switches the old default to the mansion hallway. A background the user picked in
 *      Settings is left alone. Fail-open.
 */
(function () {
  'use strict';
  var OLD_DEFAULT = '/bg/ceiling-window-room-night.jpeg';
  var NEW_DEFAULT = '/bg/mansion-hallway.jpeg';
  try {
    var origin = String(window.__REMAI_RESOLVED_BACKEND_ORIGIN || window.location.origin).replace(/\/+$/, '');
    var raw = window.localStorage.getItem('backgroundUrl');
    var next;
    if (raw === null) {
      next = origin + NEW_DEFAULT;
    } else {
      var cur = JSON.parse(raw);
      if (typeof cur !== 'string') return;
      var i = cur.indexOf('/bg/');
      if (i < 0) return;                                  // not one of the backend's backgrounds
      var path = cur.slice(i);
      if (path === OLD_DEFAULT) path = NEW_DEFAULT;
      next = origin + path;
      if (next === cur) return;
    }
    window.localStorage.setItem('backgroundUrl', JSON.stringify(next));
  } catch (e) { /* fail open */ }
})();
