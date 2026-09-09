# RemAI — "Midnight Companion" (redesign A)

A restyle-from-the-outside layer over the compiled Chakra/Panda bundle. The app UI
cannot be rebuilt, so the look is delivered by `theme.css` (design tokens + component
styling), `shell.js` (a tiny, fail-open vanilla layer that tags the DOM with `data-mc`
hooks and mirrors live state), an inline branded loading screen in `index.html`, and a
restyled sign-in button in `auth-shell.js`.

## Direction

A quiet room at night with someone who listens. Cinematic, dark, luminous. A deep
navy-black base; Rem's blue used as **light** (glows, edges, focus rings, the connection
lamp), never as a flat fill. Glass appears only where controls live — the input bar, the
sidebar, drawers — with real material: hairline inner borders, layered translucency,
restrained blur. The chrome recedes (quiet at rest on pointer devices, lit on
hover/focus) while every essential control stays visible and tappable on touch. The
Live2D character and the background art are the hero; nothing added competes with them.

## Tokens

**Color** (dark, committed — the surface *is* the night)
- Ground: `--mc-ink-0 #05070e`, `--mc-ink-1 #0b1020`, `--mc-ink-2 #121a2e`
- Text (cool-tinted to belong to the navy, all ≥ 4.5:1 on ground):
  `--mc-text #e8eef9`, `--mc-text-2 #a9b6cf`, `--mc-text-3 #8f9bb5`
- Rem's blue as light: `--mc-blue #8cc4ff`, `--mc-blue-deep #5aa6f2`, plus glow tints
  (`--mc-glow` .34, `--mc-glow-soft` .14, `--mc-glow-faint` .08)
- The one warm accent, reserved for *disconnected*: `--mc-ember #f2b56b`
- Material: hairlines `rgba(160,196,255,.14/.28)`, glass `rgba(10,15,28,.62)` /
  `rgba(9,13,24,.88)`, top highlight `rgba(255,255,255,.06)`, blur `18px saturate(1.25)`

**Type** (self-hosted woff2, SIL OFL, latin subset, ~129 KB total, `font-display: swap`)
- Display / moments: **Bodoni Moda** (`--mc-font-display`) — wordmark, loading screen,
  empty-state, drawer titles.
- Text / UI: **Source Sans 3** (`--mc-font-text`) — everything else.
- *Why these:* the brief bars Inter, DM Sans, Roboto and Playfair. I wanted the display
  face to feel like a cinematic title card at night, so I chose a Didone — but **not**
  Playfair, the default Didone. Bodoni Moda is colder and more architectural, with
  sharper hairlines and an optical range; its italic carries the intimate "waking Rem…"
  and "Start a conversation" moments without tipping into wedding-invitation romance.
  It is paired with Source Sans 3, a humanist (not geometric) workhorse whose warmth
  suits a companion and whose screen legibility suits dense controls — a deliberate
  step away from the Inter/DM/Roboto monoculture.

**Radii** — controls 12px, containers 16px, input bar 20px, chips + sign-in pill full round.
**Spacing** — the bundle owns layout; the layer adds breathing room in the glass bar
(8px gutter, 10px control gaps) and drawer.
**Motion** — one ease, `cubic-bezier(0.16, 1, 0.3, 1)`; durations 160 / 260 / 520 ms.
Ambient life: a barely-there 80s gradient drift behind the glass, and a 4.2s "breathing"
glow on the connection lamp / active AI lamp. All motion is removed under
`prefers-reduced-motion`; glass falls back to solid under `prefers-reduced-transparency`.

## Signature moments

- **Loading screen** (inline in `index.html`, paints on the first frame): the *RemAI*
  wordmark in italic Bodoni over a soft blue orb, status line "waking Rem…". Removed the
  instant `#root` renders; fail-open with a 10s hard cap so it can never block the app.
- **Connection lamp** — the green/red pill becomes a quiet glass capsule with a single
  breathing blue dot when connected, an ember dot when not (state read by `shell.js`).
- **AI-state chip** — hairline capsule whose lamp only lights (blue, breathing) when Rem
  is actually working; "idle" rests unlit.
- **Sign-in** — a refined glass pill that belongs to the same world (self-hosted type,
  blue-glow hover, light focus ring).
- **Input bar** — one piece of layered glass holding the state chip, mic (a lamp when
  live), interrupt, attachment and text field; the field's caret and focus ring are Rem's
  blue.

## Fail-open & safety

`shell.js` only ever *adds* attributes, is idempotent, and is wrapped in try/catch; if it
never runs, its `data-mc-ready` marker is absent and the mobile overlay rules don't apply,
so the app falls back to the bundle's native layout. No functional control is hidden or
covered. All resources are same-origin (self-hosted fonts, `theme.css`, `shell.js`); no
new CSP host is needed and the strict policy in `vercel.json` is untouched.

## What I deliberately did NOT do

- No neon, no gradient text, no purple "AI glow", no glass-as-decoration — glass only
  where it holds controls; blue only as light.
- Did not touch the backend paths, the URL-rewriter / auth logic, `#root`, the
  Electron-tab / About-tab MutationObserver, or the compiled bundle and its CSS.
- Did not reposition or re-skin the Live2D model or background art, or add any element
  that competes with them.
- Did not change backend-authored copy (subtitles, replies, state labels).
- No third-party scripts, fonts-from-CDN, analytics, or icon libraries; icons remain the
  bundle's own SVGs, restyled by color/size only.
- Left the character's own click/interaction untouched; the mobile drawer overlays it
  only while open.
