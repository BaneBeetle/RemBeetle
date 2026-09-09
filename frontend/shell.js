/*
 * shell.js - RemAI "Midnight Companion" runtime hooks.
 *
 * The app UI is a compiled React bundle with hashed class names, so theme.css
 * cannot target it directly. This script walks the DOM under #root and adds
 * data-mc="..." attributes to the structural pieces (sidebar, stage, input bar,
 * connection badge ...) and mirrors a few live states into attributes:
 *
 *   html/#root   data-mc-sidebar = open | closed
 *   footer       data-mc-footer  = open | closed
 *   badge        data-mc-ws      = on | off | unknown   (WebSocket)
 *   mic button   data-mc-mic     = on | off
 *   state chip   data-mc-ai      = idle | active
 *
 * It only ever adds attributes. It never removes, hides, disables or moves a
 * control, so if anything here throws the app keeps working with the bundle's
 * own look. Everything is idempotent and batched to one pass per task.
 */
(function () {
  'use strict';

  var root = document.getElementById('root');
  if (!root || typeof MutationObserver !== 'function') return;

  function set(el, name, value) {
    if (el && el.getAttribute(name) !== value) el.setAttribute(name, value);
  }
  function text(el) {
    return ((el && el.textContent) || '').replace(/\s+/g, ' ').trim();
  }
  function rgb(el) {
    var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(el).backgroundColor || '');
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  // The bundle encodes some state only as a background colour, which theme.css
  // overrides. Lift our state attribute for the read, then put it back; no paint
  // happens in between, so nothing flickers.
  function bundleColor(host, attr, el) {
    var prev = host.getAttribute(attr);
    if (prev !== null) host.removeAttribute(attr);
    var c = rgb(el);
    if (prev !== null) host.setAttribute(attr, prev);
    return c;
  }
  function rotated(el) {
    return /180deg/.test((el && el.getAttribute('style')) || '');
  }

  function tagSidebar(wrap) {
    set(wrap, 'data-mc', 'sidebar-wrap');
    var sb = wrap.children[0];
    if (!sb) return;
    set(sb, 'data-mc', 'sidebar');
    var handle = sb.children[0];
    var content = sb.children[1];
    if (handle && handle.querySelector('svg') && !handle.querySelector('button')) {
      set(handle, 'data-mc', 'sidebar-handle');
      set(root, 'data-mc-sidebar', rotated(handle) ? 'closed' : 'open');
      startCollapsed(handle);
    }
    if (!content) return;
    set(content, 'data-mc', 'sidebar-content');
    var header = content.children[0];
    var history = content.children[1];
    var panels = content.children[2];
    if (header) {
      var row = header.children[0];
      if (row && row.querySelector('button')) set(row, 'data-mc', 'iconrow');
    }
    if (history) {
      set(history, 'data-mc', 'history');
      var nodes = history.querySelectorAll('p, span, div');
      if (nodes.length < 40) {
        for (var i = 0; i < nodes.length; i++) {
          if (nodes[i].children.length === 0 && /^No messages yet/i.test(text(nodes[i]))) {
            set(nodes[i], 'data-mc', 'empty');
            break;
          }
        }
      }
    }
    if (panels && panels.getAttribute('data-scope') === 'tabs') set(panels, 'data-mc', 'panels');
  }

  function tagFooter(footer) {
    set(footer, 'data-mc', 'footer');
    var inner = footer.children[0];
    if (!inner) return;
    set(inner, 'data-mc', 'footer-inner');
    var handle = inner.children[0];
    var body = inner.children[1];
    if (handle && handle.querySelector('svg') && !handle.querySelector('button')) {
      set(handle, 'data-mc', 'footer-handle');
      set(footer, 'data-mc-footer', rotated(handle) ? 'closed' : 'open');
    }
    if (!body) return;
    set(body, 'data-mc', 'footer-body');
    var controls = body.children[0];
    if (!controls) return;
    set(controls, 'data-mc', 'controls');
    var left = controls.children[0];
    var inputwrap = controls.children[1];
    if (left && !left.querySelector('textarea')) {
      set(left, 'data-mc', 'left');
      var stateWrap = left.children[0];
      var btnRow = left.children[1];
      if (stateWrap && !stateWrap.querySelector('button')) {
        set(stateWrap, 'data-mc', 'state-wrap');
        var chip = stateWrap.children[0];
        if (chip) {
          set(chip, 'data-mc', 'state');
          var t = text(chip).toLowerCase();
          set(chip, 'data-mc-ai', t === '' || t === 'idle' ? 'idle' : 'active');
        }
      }
      if (btnRow && btnRow.querySelector('button')) {
        set(btnRow, 'data-mc', 'btnrow');
        var buttons = btnRow.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
          var b = buttons[i];
          if (/raise hand/i.test(b.getAttribute('aria-label') || '')) {
            set(b, 'data-mc', 'interrupt');
          } else {
            set(b, 'data-mc', 'mic');
            micState(b);
          }
        }
      }
    }
    if (inputwrap && inputwrap.querySelector('textarea')) {
      set(inputwrap, 'data-mc', 'inputwrap');
      set(inputwrap.querySelector('textarea'), 'data-mc', 'textarea');
      var attach = inputwrap.querySelector('button[aria-label="Attach file"]');
      if (attach) set(attach, 'data-mc', 'attach');
    }
  }

  // On phones and small tablets the sidebar is an overlay that hides the
  // character, so the first render starts with it tucked away: one synthetic
  // tap on the bundle's own handle, once per page load, nothing else.
  var collapsedOnce = false;
  function startCollapsed(handle) {
    if (collapsedOnce) return;
    collapsedOnce = true;
    try {
      if (!window.matchMedia || !window.matchMedia('(max-width: 1023px)').matches) return;
      if (rotated(handle)) return;            // already closed
      var target = handle.querySelector('svg') || handle;
      target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    } catch (e) { /* leave it open */ }
  }

  function micState(btn) {
    // Bootstrap icons: mic-mute-fill (slash) means muted, mic-fill means live.
    var muted = !!btn.querySelector('path[d^="M13 8c0"], path[d^="m9.486"]');
    var live = !muted && !!btn.querySelector('path[d^="M5 3a3"], path[d^="M3.5 6.5"]');
    var state = muted ? 'off' : live ? 'on' : null;
    if (!state) {
      var c = bundleColor(btn, 'data-mc-mic', btn);
      if (c) state = c[0] > c[1] + 60 ? 'off' : 'on';
    }
    if (state) set(btn, 'data-mc-mic', state);
  }

  function wsState(wrap) {
    var badge = wrap.children[0] || wrap;
    var c = bundleColor(wrap, 'data-mc-ws', badge);
    if (!c) return;
    var on = c[1] > c[0] && c[1] > c[2];      // the bundle paints green when connected
    var off = c[0] > c[1] + 60;               // and red when not
    set(wrap, 'data-mc-ws', on ? 'on' : off ? 'off' : 'unknown');
  }

  function tagStage(stage) {
    set(stage, 'data-mc', 'stage');
    var kids = stage.children;
    for (var i = 0; i < kids.length; i++) {
      var ch = kids[i];
      if (ch.querySelector('textarea')) {
        tagFooter(ch);
      } else if (ch.querySelector('img') || ch.querySelector('video') || ch.querySelector('canvas')) {
        set(ch, 'data-mc', 'bg');
      } else if (ch.querySelector('p')) {
        set(ch, 'data-mc', 'subtitle');
      } else if (text(ch).length > 0 && text(ch).length <= 24) {
        set(ch, 'data-mc', 'ws');
        wsState(ch);
      }
    }
  }

  function tag() {
    var kids = root.children;
    if (kids.length < 2) return;
    var live2d = kids[0];
    var app = kids[1];
    if (live2d.querySelector('canvas')) set(live2d, 'data-mc', 'live2d');
    if (!app.querySelector('textarea') && !app.querySelector('img')) return;
    set(app, 'data-mc', 'app');
    // Marker that the tag pass reached the app. The mobile overlay rules in theme.css
    // are gated on this, so if this script never runs the app falls back to the
    // bundle's native layout instead of a half-applied overlay.
    set(root, 'data-mc-ready', '1');
    var wrap = app.children[0];
    var stage = app.children[1];
    if (wrap && !wrap.querySelector('textarea')) tagSidebar(wrap);
    if (stage) tagStage(stage);
  }

  var pending = false;
  function run() {
    pending = false;
    try { tag(); } catch (e) { /* fail open: the bundle's own look remains */ }
  }
  function schedule() {
    if (pending) return;
    pending = true;
    // setTimeout rather than requestAnimationFrame: rAF is paused in background
    // tabs, and a page opened behind another tab would otherwise stay untagged.
    window.setTimeout(run, 0);
  }

  try {
    new MutationObserver(schedule).observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class', 'aria-label', 'data-state']
    });
  } catch (e) { /* observer unavailable: still run once below */ }
  run();
})();
