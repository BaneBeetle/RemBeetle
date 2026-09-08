# Hosting the RemBeetle frontend on Vercel (backend stays on EC2)

## What moves and what does not

Vercel hosts static files and short-lived serverless functions. RemBeetle's backend is a
long-running FastAPI process: every conversation is a persistent WebSocket (`/client-ws`),
the server keeps per-connection state, writes to local directories (`cache/`, `chat_history/`,
`logs/`), and its Python environment (torch, onnxruntime, sherpa-onnx, scipy) is far above the
size a Vercel Function may have. None of that can run on Vercel without rewriting the app.

So the split is:

| Piece | Where | URL |
| --- | --- | --- |
| Frontend (this `frontend/` directory: HTML, React bundle, Live2D runtime, VAD/ONNX libs) | Vercel | `https://rembeetle.com`, `https://www.rembeetle.com` |
| Backend (FastAPI, WebSockets, Supabase auth, RAG, `/api/*`, backgrounds, avatars, Live2D model files) | EC2, unchanged (Caddy -> uvicorn :12393) | `https://api.rembeetle.com` |
| Auth / database | Supabase, unchanged | |

The browser loads the page from Vercel and talks directly to the backend origin for the
WebSocket and all API/asset calls. Nothing is proxied through Vercel (Vercel rewrites are not
a reliable path for WebSockets).

## How the frontend finds the backend

The compiled React bundle hardcodes `http://127.0.0.1:12393` and `ws://127.0.0.1:12393`.
The script at the top of `index.html` rewrites every request at runtime to a backend origin:

1. `window.REMAI_BACKEND_ORIGIN` from `config.js`, if set (Vercel).
2. Otherwise the page's own origin (backend serving the frontend itself: EC2 today, or
   `http://localhost:12393` in development). This is the previous behaviour, unchanged.

`auth-shell.js` uses the same resolved origin for `/api/config`. The JWT injection on
`/client-ws` and the Google sign-in flow are unchanged; Supabase redirects back to
`window.location.origin`, which stays `https://rembeetle.com` after the cutover.

On Vercel, `vercel-build.sh` copies the static files into `dist/` and generates
`dist/config.js` from the `REMAI_BACKEND_ORIGIN` environment variable. It refuses to build if
the variable is missing or if the host is not allowed by the Content-Security-Policy in
`vercel.json`.

## Files in this change

- `frontend/index.html`: backend-origin resolution, loads `./config.js`. Otherwise identical to production.
- `frontend/auth-shell.js`: `/api/config` fetched from the resolved backend origin.
- `frontend/config.js`: `null` by default (same-origin). Never edit it for Vercel.
- `frontend/vercel.json`: framework "Other", build/output settings, security headers (same CSP as production plus the `api.rembeetle.com` host), cache headers.
- `frontend/vercel-build.sh`: build script described above.
- `frontend/assets/main-nu7uwxNJ.js`, `main-QEkl09-0.css`, `auth-shell.js`, `favicon.ico`, `libs/live2dcubismcore.min.js`, `libs/vad.worklet.bundle.min.js`: synced from the production server (the repo copy was stale; `index.html` referenced a bundle that was not in git).
- `.gitignore`: ignores `frontend/dist/`.

## Runbook

Do the steps in order. Nothing user-facing changes until step 5.

### 1. Backend: give the API its own hostname

DNS (Squarespace Domains, account.squarespace.com, Domains, `rembeetle.com`, DNS Settings; the
zone is served by Google's `ns-cloud-c*.googledomains.com` nameservers): add a record with
Type `A`, Name `api`, Priority blank, TTL 30 minutes (the smallest Squarespace offers),
IP Address `54.196.106.185`.

On the EC2 box (SSH as `ubuntu` with `rembeetle-key.pem`), add a Caddy site block. Caddy obtains
the TLS certificate automatically once the DNS record resolves (ports 80 and 443 are already
open):

```bash
sudo tee -a /etc/caddy/Caddyfile >/dev/null <<'CADDY'

api.rembeetle.com {
	encode gzip
	reverse_proxy 127.0.0.1:12393
}
CADDY
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Check:

```bash
curl -sS https://api.rembeetle.com/api/config
```

Expected: `{"auth_enabled":true,"supabase_url":...}`. The backend already answers every origin
(`Access-Control-Allow-Origin: *`, WebSocket handshakes accepted regardless of `Origin`), so no
application change is needed.

Keep the existing `rembeetle.com, www.rembeetle.com` block for now. It is the rollback path.

### 2. Vercel project

1. Vercel dashboard: Add New Project, import `BaneBeetle/RemBeetle` from GitHub (branch `vercel-frontend`, or `main` after merging).
2. Framework Preset: Other. Root Directory: `frontend`. Leave Build Command / Output Directory
   as detected from `vercel.json`.
3. Environment Variables: `REMAI_BACKEND_ORIGIN` = `https://api.rembeetle.com` for Production and Preview. The build
   script trims stray whitespace and a trailing slash from the value.
4. Deploy. The build log should end with `Built dist/ for backend origin https://api.rembeetle.com`.
5. Open the preview URL (`https://<project>.vercel.app`). Expected: Rem's Live2D model loads,
   the background image loads, the connection indicator is green (WebSocket to
   `wss://api.rembeetle.com/client-ws`), the "Sign in with Google" button appears top-right.
   Browser console should show `[auth-shell]` lines and no CSP or CORS errors.

Google sign-in on the preview URL only works after step 3; production sign-in needs no change.

### 3. Supabase redirect URLs (optional for previews)

Supabase dashboard, Authentication, URL Configuration. Site URL stays `https://rembeetle.com`.
Redirect URLs must contain `https://rembeetle.com` and `https://www.rembeetle.com` (they already
do, or sign-in would be broken today). To allow sign-in from Vercel preview deployments add
`https://*.vercel.app/**`. Google Cloud OAuth settings do not change (the OAuth redirect goes to
Supabase, not to the site).

### 4. Lower the DNS TTL

Set the TTL of the `rembeetle.com` and `www` records to the smallest value Squarespace offers
(30 minutes) and wait at least the old TTL before the cutover. With a 30-minute TTL the cutover
and any rollback take up to 30 minutes to reach every resolver.

### 5. Cutover

1. Vercel project, Settings, Domains: add `rembeetle.com` and `www.rembeetle.com`. Vercel shows
   the exact records to create.
2. In Squarespace Domains DNS Settings: change the apex `A` record to Vercel's value (`76.76.21.21` unless the
   domain card shows another) and change `www` to a `CNAME` pointing at the value shown in the
   domain card (typically `cname.vercel-dns.com`). Remove the old `A` records for those two names.
3. Wait for Vercel to show both domains as valid and the certificate issued.

### 6. Verify

- `https://rembeetle.com` returns `server: Vercel` headers and the page loads.
- Model, backgrounds and avatars load (requests go to `https://api.rembeetle.com/...`).
- Connection indicator green; hold the mic button and talk; Rem answers with audio.
- Sign in with Google, reload, verify the session persists and the WebSocket carries the token
  (network tab: `client-ws?token=...`).
- Camera / screen share still start (they only need a secure origin, which Vercel provides).
- Expected noise in the browser console, identical to production today: one 404 for
  `/undefined/undefined.model3.json` (the bundle tries a default model before the server sends
  the real model info) and a `Cannot read properties of null (reading 'release')` error when
  the page is reloaded. Neither affects the app.
- Hobby plan: the first visit downloads about 12 MB (ONNX wasm + bundle); the Hobby plan
  includes 100 GB of transfer per month and is for non-commercial use.

### 7. Rollback

Point the apex `A` record back to `54.196.106.185` and `www` back to an `A` record with the
same IP. Caddy still serves both names, so the old setup is back within the DNS TTL. Nothing
on the server was changed in a way that needs undoing.

### 8. Cleanup (a few days after the cutover)

- Remove `rembeetle.com, www.rembeetle.com` from `/etc/caddy/Caddyfile` (Caddy would keep
  trying to renew certificates for names that no longer point at it) and `sudo systemctl reload caddy`.
- Security group: close inbound `12393`; Caddy only needs 80 and 443. The frontend never
  connects to `:12393` directly.
- Optional: sync the server's `frontend/` with this repo so both copies are identical. With
  `config.js` set to `null`, the backend-served copy behaves exactly as today.

## Changing the backend origin later

Update the `REMAI_BACKEND_ORIGIN` variable in Vercel and the three host entries
(`img-src`, `connect-src`, `media-src`) in `frontend/vercel.json`, then redeploy. The build
fails on purpose if the two disagree.

## Local development

Serve `frontend/` from the backend as before (`python run_server.py`, open
`http://localhost:12393`). `config.js` is `null`, so everything is same-origin. To test the Vercel
build locally, run `REMAI_BACKEND_ORIGIN=https://api.rembeetle.com bash frontend/vercel-build.sh`
and serve `frontend/dist/` with any static server.
