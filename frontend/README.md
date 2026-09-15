# hia frontend

The web UI for Home Intelligent Assistant. See the repo root
[README](../README.md) and [docs/](../docs/) for the project as a whole.

P2's scope only, per [docs/04-roadmap.md](../docs/04-roadmap.md): a live entity view
and a data-quality page — not the fuller Overview/Twin/Decisions/Learning/Entities
UI [docs/02-architecture.md](../docs/02-architecture.md) describes, which depends on
subsystems (governor, twin, training) that don't exist yet.

## Setup

Requires Node 24+.

```sh
cd frontend
npm install
```

## Running it

Two ways, depending on what you're doing:

**Iterating on the UI** — Vite's dev server, with hot reload, proxying `/api/*`
(including the WebSocket) to a `hia serve` you run separately on `:8099`:

```sh
npm run dev
# in another terminal: cd ../backend && uv run hia serve
```

**Seeing the real thing** — build it and let `hia serve` serve it directly (this is
how it actually ships):

```sh
npm run build              # writes dist/
cd ../backend && uv run hia serve
# → http://localhost:8099/
```

`hia serve` looks for the build at `../frontend/dist` relative to wherever it's run
from (`HIA_FRONTEND_DIST_DIR` to override) and runs API-only, logging that it did,
if the directory doesn't exist — a fresh checkout before `npm run build` isn't an
error.

## Why every URL is relative, not `/api/...`

Under Home Assistant's ingress, this app is served from a path prefix the
Supervisor assigns at runtime (`/api/hassio_ingress/<token>/...`) — never known at
build time. `vite.config.ts` sets `base: "./"` so built asset references are
relative, and `src/api.ts` resolves every fetch/WebSocket URL against
`document.baseURI` rather than an absolute root path, for the same reason: whatever
prefix the page loaded under, requests need to inherit it. An absolute path would
work fine in local dev and silently break under ingress — there's no HAOS hardware
in this project's dev loop yet to catch that on, so it has to be right by
construction instead.

## Checks

```sh
npx tsc -b        # typecheck
npx eslint .       # lint
npx vitest run     # unit tests
npx vite build     # production build
```

All four run in CI (`.github/workflows/ci.yml`) on every push and PR.
