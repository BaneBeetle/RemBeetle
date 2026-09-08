/*
 * auth-shell.js — vanilla JS auth integration for Open-LLM-VTuber frontend.
 *
 * Runs BEFORE the React bundle so it can intercept WebSocket construction.
 * Deliberately written as a standalone IIFE with no dependencies other
 * than the Supabase JS SDK (lazy-loaded from esm.sh) because the React
 * bundle is already a pre-compiled submodule we don't want to rebuild.
 *
 * Responsibilities:
 *   1. Fetch /api/config to learn if auth is enabled + Supabase keys.
 *   2. Synchronously wrap window.WebSocket so `new WebSocket(...)` calls
 *      on the /client-ws endpoint automatically include `?token=<jwt>`.
 *      This wrap happens even before Supabase finishes loading — in the
 *      auth-disabled / not-signed-in state it's transparent.
 *   3. Lazy-load Supabase JS SDK, restore existing session, subscribe
 *      to auth-state changes.
 *   4. Render a small floating button (top-right) that toggles sign-in
 *      with Google / sign-out.
 *
 * Design notes:
 *   - We only rewrite WS URLs containing "/client-ws" so we never leak
 *     the JWT to unrelated websockets (e.g. Live2D asset streams).
 *   - The button's DOM is created lazily after `body` is ready; the
 *     WebSocket wrap is installed immediately at script load time.
 *   - If /api/config says auth_enabled=false, the script becomes a
 *     complete no-op — no UI, no WebSocket wrap.
 *   - Pinned Supabase SDK version to avoid surprise CDN breakage.
 *   - The backend origin comes from window.__REMAI_RESOLVED_BACKEND_ORIGIN (set by the
 *     URL rewriter in index.html from ./config.js), so this frontend can be hosted on
 *     a different origin (Vercel) than the FastAPI backend (EC2).
 */

(function () {
  'use strict';

  // ---------- Synchronous WebSocket wrap -----------------------------------
  //
  // Runs immediately so any subsequent `new WebSocket(url)` call from the
  // React bundle sees our wrapped constructor. `currentJwt` starts null
  // and gets populated once the Supabase SDK finishes restoring the
  // session; WS connections opened before then are anonymous (soft gate).

  let currentJwt = null;
  let authEnabled = null; // null until /api/config returns

  const originalWebSocket = window.WebSocket;
  const WrappedWebSocket = function (url, protocols) {
    // Only inject the token into our own backend's client endpoint.
    // Leaking JWTs to third-party WS servers would be a data-exposure bug.
    if (
      authEnabled &&
      currentJwt &&
      typeof url === 'string' &&
      url.indexOf('/client-ws') !== -1
    ) {
      const separator = url.indexOf('?') !== -1 ? '&' : '?';
      url = url + separator + 'token=' + encodeURIComponent(currentJwt);
    }
    return new originalWebSocket(url, protocols);
  };
  // Preserve the static constants and prototype so `instanceof`, readyState
  // checks, etc. all keep working against the wrapped constructor.
  WrappedWebSocket.CONNECTING = originalWebSocket.CONNECTING;
  WrappedWebSocket.OPEN = originalWebSocket.OPEN;
  WrappedWebSocket.CLOSING = originalWebSocket.CLOSING;
  WrappedWebSocket.CLOSED = originalWebSocket.CLOSED;
  WrappedWebSocket.prototype = originalWebSocket.prototype;
  window.WebSocket = WrappedWebSocket;

  // ---------- Async initialisation ----------------------------------------

  async function initAuth() {
    // 1. Fetch server config.
    let config;
    try {
      // Same backend origin as all other traffic (resolved in index.html from ./config.js).
      // '' keeps the original relative request when the backend serves this page itself.
      const backendOrigin = window.__REMAI_RESOLVED_BACKEND_ORIGIN || '';
      const resp = await fetch(backendOrigin + '/api/config');
      if (!resp.ok) {
        console.warn('[auth-shell] /api/config returned', resp.status);
        return;
      }
      config = await resp.json();
    } catch (e) {
      console.warn('[auth-shell] failed to load /api/config:', e);
      return;
    }

    if (!config.auth_enabled) {
      console.log('[auth-shell] auth disabled on server, no-op');
      authEnabled = false;
      return;
    }

    if (!config.supabase_url || !config.supabase_anon_key) {
      console.warn('[auth-shell] auth enabled but Supabase keys missing in /api/config');
      return;
    }

    authEnabled = true;

    // 2. Lazy-load the Supabase SDK from a pinned esm.sh URL. Using esm.sh
    //    avoids bundling Supabase into the submodule build — one fewer
    //    thing to keep in sync.
    let createClient;
    try {
      const mod = await import('https://esm.sh/@supabase/supabase-js@2.45.0');
      createClient = mod.createClient;
    } catch (e) {
      console.error('[auth-shell] failed to load Supabase SDK:', e);
      return;
    }

    const supabase = createClient(config.supabase_url, config.supabase_anon_key, {
      auth: {
        // Store in localStorage so the session survives tab closes.
        persistSession: true,
        // Pick up the #access_token=... fragment after OAuth redirect.
        detectSessionInUrl: true,
        autoRefreshToken: true,
      },
    });
    // Expose for debugging from the devtools console.
    window.__supabase = supabase;

    // 3. Session state + button rendering.
    let currentUser = null;

    const renderButton = function () {
      if (!document.body) {
        document.addEventListener('DOMContentLoaded', renderButton);
        return;
      }

      let btn = document.getElementById('auth-shell-button');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'auth-shell-button';
        btn.setAttribute(
          'style',
          [
            'position: fixed',
            'top: 12px',
            'right: 12px',
            'z-index: 999999',
            'padding: 8px 14px',
            'border-radius: 8px',
            'border: 1px solid rgba(255,255,255,0.18)',
            'background: rgba(20, 20, 20, 0.78)',
            'color: #fff',
            'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
            'font-size: 13px',
            'font-weight: 500',
            'cursor: pointer',
            'backdrop-filter: blur(10px)',
            '-webkit-backdrop-filter: blur(10px)',
            'box-shadow: 0 2px 10px rgba(0,0,0,0.25)',
          ].join(';'),
        );
        document.body.appendChild(btn);
      }

      if (currentUser) {
        const label = currentUser.email || (currentUser.id || '').slice(0, 8);
        btn.textContent = 'Sign out (' + label + ')';
        btn.onclick = async function () {
          try {
            await supabase.auth.signOut();
          } catch (e) {
            console.error('[auth-shell] sign out failed:', e);
          }
        };
      } else {
        btn.textContent = 'Sign in with Google';
        btn.onclick = async function () {
          try {
            await supabase.auth.signInWithOAuth({
              provider: 'google',
              options: {
                // Return to the current page after the Google redirect.
                // Must be in the allowed redirect URLs in the Supabase
                // dashboard (Settings → Auth → URL Configuration).
                redirectTo: window.location.origin,
              },
            });
          } catch (e) {
            console.error('[auth-shell] sign in failed:', e);
          }
        };
      }
    };

    const applySession = function (session) {
      if (session) {
        currentJwt = session.access_token;
        currentUser = session.user;
        console.log(
          '[auth-shell] session active for',
          currentUser && currentUser.email,
        );
        // The React bundle opens its WebSocket before the Supabase SDK
        // finishes restoring the session, so the first WS connection is
        // anonymous. Reload once so the next connection gets the token.
        if (!sessionStorage.getItem('auth-shell-session-seen')) {
          sessionStorage.setItem('auth-shell-session-seen', '1');
          window.location.reload();
          return;
        }
      } else {
        currentJwt = null;
        currentUser = null;
        // Clear the flag on sign-out so the next sign-in triggers a reload
        sessionStorage.removeItem('auth-shell-session-seen');
      }
      renderButton();
    };

    // Restore any existing session (e.g. after page refresh).
    try {
      const { data } = await supabase.auth.getSession();
      applySession(data.session);
    } catch (e) {
      console.warn('[auth-shell] getSession failed:', e);
      renderButton();
    }

    // React to sign-in / sign-out / token refresh.
    supabase.auth.onAuthStateChange(function (_event, session) {
      applySession(session);
    });
  }

  // Kick off async init, then load the React bundle once auth state is
  // settled. This ensures the WebSocket wrapper has the JWT before the
  // React app calls `new WebSocket(...)`.
  initAuth()
    .catch(function (e) {
      console.error('[auth-shell] init failed:', e);
    })
    .finally(function () {
      if (typeof window.__loadReactBundle === 'function') {
        window.__loadReactBundle();
      }
    });
})();
