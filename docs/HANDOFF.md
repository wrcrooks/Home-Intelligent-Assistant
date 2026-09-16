# Handoff

For an agent or developer picking this up cold. Written 2026-09-09; updated
several times through 2026-09-16 as work actually landed — see "Where things
stand" for the latest.

## Where things stand

**P0, P1 and P2 are all done — P2's full exit criterion is now confirmed on real
HAOS hardware.** The add-on installs from the (now public) repository, builds, and
runs correctly through ingress on a real aarch64 Home Assistant instance — the
user confirmed it installed and working on 2026-09-16. Getting there surfaced four
real bugs, none of them caught by anything short of actually testing on real
hardware (this dev machine's Docker Engine can't pull the real base image at all):
a Docker-Hub connectivity timeout in Supervisor's own build tooling (transient,
just needed a retry — now documented in `hia/DOCS.md`'s Troubleshooting section),
a missing executable bit on `run`/`finish` (Windows doesn't track the Unix `+x`
bit), and — the one that made the executable-bit fix *look* like it hadn't worked
— a Docker build-cache bug where the `git clone` step's unchanging instruction text
meant every subsequent build silently kept reusing the very first cached clone,
regardless of what had since been pushed or how many times "Rebuild" was clicked.
All four are detailed in the P2 packaging section below, each verified by
reproducing it (not just reasoning about it) before trusting the fix.

**P3 slice 1 (provenance classifier, Layer 1 + Layer 2) is now built and
verified** — synthetically against fake-HA fixtures, and with one real check
against the live house (`HomeAssistantClient`'s new REST `.get()` method,
confirming the user's token is admin-privileged, which `05-provenance.md`'s
Layer 2 needs). See the new "P3, slice 1" section below for what's built, what's
still genuinely unverified (this house has no automations configured yet, so
Layer 2's actual correlation logic hasn't been exercised against real data), and
what slice 2 (Layer 3 + admission control) still needs.

### P0 — the Home Assistant client

Exit criterion verified against a real, running Home Assistant instance, not just the
fake test server. `backend/` (Python 3.13, managed with `uv`) has:

- `hia.config` — settings via `pydantic-settings`, `HIA_`-prefixed env vars / `.env`.
- `hia.logging` — structlog, JSON or console rendering.
- `hia.ha.client.HomeAssistantClient` — the websocket client: auth handshake,
  `subscribe_events`, automatic reconnect with backoff + jitter and resubscription,
  a monotonic per-event sequence number that survives reconnects (so a consumer can
  detect gaps), and a bounded internal queue so a slow consumer drops the newest
  event and increments a counter rather than ever stalling the read loop.
- `hia.ha.registry` — fetches entity/device/area/floor/label registries; floor/label
  degrade to an empty list rather than failing if an older HA Core doesn't support
  them.
- `hia.cli` — the `hia` command: `watch`, `registry`, `check`.
- A full test suite (`backend/tests/`) running against a fake in-process HA
  websocket server (`tests/conftest.py`) that speaks the real protocol closely
  enough to genuinely exercise auth failure, reconnect + resubscribe, the monotonic
  seq/gap contract, and backpressure dropping — 8/8 passing, ruff and mypy (strict)
  both clean, CI configured in `.github/workflows/ci.yml`.
- `compose/dev-ha/` — a throwaway Home Assistant instance (`docker compose up -d`)
  with the `demo` integration, for testing against something real.

**How it was verified**, with Docker Desktop running: brought up
`compose/dev-ha/` (pinned to `2024.6.0` — this dev machine's Docker Engine, 20.10.21,
fails to unpack a layer in the current `:stable` image with `archive/tar: invalid tar
header`; confirmed reproducible, and confirmed an older tag pulls fine, before
concluding it's an engine/buildkit incompatibility rather than a fluke — see the
comment in the compose file), drove onboarding entirely via HA's own HTTP APIs with
no browser (`POST /api/onboarding/users` → auth code → `POST /auth/token` → a
short-lived bearer token → over the websocket, `auth/long_lived_access_token` to mint
a real 10-year token), then:

- `hia check` and `hia registry` both worked against the live instance — registry
  parsing handled real HA payloads (with extra fields beyond the ones this project's
  models declare) without complaint, exactly the point of `extra="allow"`.
- `hia watch` in the background, confirmed a real toggled light arrived as a
  `state_changed` event (`seq=1, resumed_after_gap=False`).
- `docker compose restart homeassistant` mid-stream. The client logged
  `ha_disconnected` twice with growing backoff (2.0s, 4.0s), then `ha_connected
  resumed=True` once Core came back — with **no manual resubscription needed**.
- The next event after reconnect (a demo sensor changing on its own, not even one we
  triggered) carried `resumed_after_gap=True, seq=2`; the two after that were back to
  `resumed_after_gap=False` at `seq=3, seq=4`. Monotonic across the reconnect, exactly
  as designed.

Incidental finding worth keeping: toggling the light via the REST API produced a
`context.user_id` on the resulting state change (the REST bearer token maps to a user
account) — a live, small-scale demonstration of exactly the "an automated caller can
carry a user_id and look human" trap documented in `05-provenance.md` and `CLAUDE.md`.
Nothing to fix; just confirms the concern was correctly identified, not hypothetical.

The throwaway instance and its generated state were torn down afterward
(`docker compose down` + config directory cleaned); nothing from that run is meant to
persist, and `compose/dev-ha/config/` is now gitignored except `configuration.yaml`.

### P1, part 1 — live ingestion and the event store

`hia.ingest` (new):

- `store.EventStore` — one DuckDB file (`{data_dir}/hia.duckdb`, gitignored), two
  tables. `state_changes` is typed (entity_id, state, attributes, old_state,
  timestamps, the three context columns) and is what both the live writer and
  (once it exists) recorder backfill write into — "live and historical data are
  indistinguishable downstream." `events` is generic (JSON `data` column) and
  captures everything else this project subscribes to: `automation_triggered`,
  `script_started`, `call_service`. Every row on both tables carries
  `context_id`/`context_parent_id`/`context_user_id` from day one — captured now
  because, per the roadmap's "cannot be retrofitted" list, it can't be added later.
- `pipeline.run_ingest` — consumes `HomeAssistantClient.events()` and writes each one
  via `asyncio.to_thread` (DuckDB's Python API is synchronous; this keeps a write from
  ever blocking the event loop, consistent with why the client's own reader is
  decoupled from consumer speed).
- `quality.build_report` — a first data-quality report: total counts, per-entity
  staleness, and a count of `resumed_after_gap` rows (how many times the live window
  had a reconnect and may have missed something). Flapping-sensor and unit-change
  detection are explicitly deferred to P4 — what counts as "flapping" is itself a
  modelling question that needs real data volume to answer sensibly, not a v1 concern.
- CLI: `hia ingest` (runs `watch`'s reconnect-proof stream, but persisted;
  `--stop-after N` for smoke tests) and `hia data-quality`.
- 18/18 tests passing (10 new), ruff and mypy clean. `duckdb`'s Python client needed
  `pytz` at runtime to materialize `min()`/`max()` over `TIMESTAMPTZ` columns —
  not obvious from the failure mode (`ModuleNotFoundError` surfacing through a DuckDB
  `InvalidInputException`), now pinned as a direct dependency rather than an
  undeclared transitive one.

**Verified live**, same throwaway-instance flow as P0: ran `hia ingest --stop-after 5`
against the dev instance while toggling real lights via the REST API, then inspected
the resulting `.duckdb` file directly. Worth keeping on record: the `call_service` row
and the `state_changed` row it caused share the same `context_id` — a live, small-scale
demonstration of exactly the context-chain linkage the provenance classifier's Layer 1
(P3) depends on. `hia data-quality` correctly reported the 2 state_changes / 3 events
split. Instance torn down afterward as usual.

### P1, part 2 — recorder backfill

`hia.ingest.backfill` reads Home Assistant's own recorder database — **read-only,
always**, enforced by SQLite's own URI `mode=ro` (verified: there's a test that
opens the same read-only connection this module uses and asserts an `INSERT`
actually raises `OperationalError`, not just a docstring promise) — and writes into
the *same* `state_changes` table the live writer uses, tagged `source="backfill"`.

The recorder schema has moved a lot over HA's history (`states_meta` normalisation,
epoch-float `*_ts` columns replacing datetime ones, binary ULID/UUID-encoded context
columns replacing string ones). Rather than trust a hardcoded `schema_version >= N`
cutoff — getting N exactly right is its own research problem, and being wrong about
it fails silently — the reader checks that the specific columns it needs actually
exist (via SQLAlchemy reflection) and raises a clear, named-column error if not.
Context binary columns are decoded with `ulid-transform`, the same PyPI package HA's
own recorder uses, specifically so a backfilled `context_id` is byte-for-byte the
same string a live-captured row for the same event would have had — without that,
provenance's context-chain matching (`05-provenance.md` §3) would silently fail to
join backfilled history to anything.

**Verified against the real dev-ha recorder database**, not just synthetic fixtures:
ran `hia backfill --db-path compose/dev-ha/config/home-assistant_v2.db` after
generating real light-toggle activity. Result: 116/116 rows had a non-null
`context_id`; the toggled-light rows showed the correct `old_state` (via the
`old_state_id` self-join), full real attribute JSON (via the `state_attributes`
join), and a `context_user_id` matching the exact account that made the REST calls.
**Schema version 43 was detected** on this HA release (2024.6.0) — a number never
looked up or hardcoded anywhere, which is the point: the column-presence check
worked correctly on a version this project has no explicit knowledge of.

**SQLite only, still.** MariaDB/Postgres should work through the same
SQLAlchemy-based reader — different connection URL, different driver dependency —
but neither has been tried against a live database, so that path stays unverified,
not supported, until someone actually runs it.

**Explicitly not handled, flagged rather than silently skipped:**

- The `statistics`/`statistics_short_term` tables (long-term rollups that outlive
  purged raw state history) aren't read yet — a real gap for entities whose raw
  history has already aged out under HA's default retention.
- No de-duplication between backfill and live ingestion. Backfill is meant for the
  history *before* live ingestion started; running it over a range `hia ingest`
  already covered will currently produce duplicate rows (same real event, two source
  values) — there's no uniqueness constraint yet to prevent it. A `context_id`-based
  upsert is the likely fix, once there's more confidence context_id is reliably
  unique per event across both paths than a single verification session can establish.

18/24 → 24/24 tests passing (6 new: URL rejection, the read-only-connection-really-
is-read-only check, correct context/attribute/old-state extraction, since/until
filtering, and two "refuse to guess" paths — a missing required column, and a
database that isn't a recorder database at all).

### P2, part 1 — the backend (`hia serve`)

(At the time this part was written, add-on packaging — Dockerfile, `config.yaml`,
s6, ingress deployment — didn't exist yet; that's covered in P2 part 3 below, and
is now confirmed working on real HAOS hardware.)

**The single biggest finding this pass, and it changed the architecture.** The
original plan (`02-architecture.md`, and how P1 was scoped) was: `hia ingest` writes,
a separate `hia serve` process reads the same DuckDB file read-only. That was wrong.
Verified against a real Linux container (not just hit first on Windows, then
confirmed it wasn't Windows-specific): DuckDB has no "one writer, separate readers"
mode at all. A database file is opened in *either* read-write (exactly one process)
*or* read-only (any number of processes, none writing) — never a mix. Attempting the
original design fails immediately with `Conflicting lock is held`. Corrected: `hia
serve` now owns the store outright — it connects to Home Assistant, ingests every
subscribed event *and* serves REST reads *and* relays live events over a WebSocket,
all through one DuckDB connection, in one process. `hia ingest`/`hia
backfill`/`hia data-quality` remain as standalone tools, but **none of them may run
at the same time as `hia serve`** against the same `HIA_DATA_DIR` — see
`hia.api.state`'s module docstring, which is now the canonical explanation, linked
from every affected docstring (`hia.ingest.store`, `hia.cli`'s command help text).
If you're about to design something assuming DuckDB supports concurrent
readers-alongside-a-writer, it doesn't — don't re-derive this the hard way.

`hia.api` (new):

- `state.AppState` — the one connection, one `asyncio.Lock` serializing every access
  to it (a single DuckDB connection object isn't documented as safe for concurrent
  multi-thread use either), and `ingest_and_relay()`: the one loop that both writes
  every subscribed event durably and broadcasts `state_changed` events (with a real
  new state) to connected browser clients. One pass over `client.events()`, not two
  independent consumers — `events()` is a single-consumer async generator; a second
  call would open a second, wasteful HA connection.
- `app.create_app(settings)` — FastAPI app factory. Routes: `GET /api/health`,
  `GET /api/entities` (latest known state per entity — `EventStore.latest_states()`,
  new this pass, with `id DESC` as an explicit tiebreaker on `last_updated` ties,
  since same-tick writes are real and arbitrary tie-breaking isn't acceptable),
  `GET /api/data-quality`, `WS /api/ws/events` (live relay, broadcast-only).
- `ingress.RestrictToSupervisorMiddleware` — restricts inbound HTTP *and* WebSocket
  connections to the Supervisor's proxy IP (`172.30.32.2`) when `Settings.is_addon`
  (true iff `SUPERVISOR_TOKEN` is set — new `Settings` field, deliberately not
  `HIA_`-prefixed, since the Supervisor sets it, not us). Deliberately a raw ASGI
  middleware, not Starlette's `BaseHTTPMiddleware`, which silently does not intercept
  `websocket`-scope connections at all — would have left the live relay unprotected.
- CLI: `hia serve` (host/port default `0.0.0.0:8099` — `8099` matching the
  conventional add-on `ingress_port` default, so the eventual manifest won't need to
  override it).

**A real bug in the P0 client, found only by testing a reconnect against a live
instance under genuine load**, not something any unit test could have caught: when
subscribing to multiple event types, `_subscribe_all` sent each `subscribe_events`
command and treated the *next* message received as that command's own result. Home
Assistant does not guarantee that ordering — it can and does interleave real `event`
messages (from an already-acknowledged *earlier* subscription) with the `result`
acknowledgements for *later* ones, especially right after a reconnect, when many
entities fire near-simultaneous events. Hit exactly there: restarting dev-ha while
`hia serve` was running produced a `zone.home` state_changed event that arrived
while the client was still waiting on the `script_started`/`call_service` subscribe
results, misread as a failed subscription, raised `CommandError`, and killed the
connection attempt (caught by the outer reconnect loop, so it degraded to endless
retries rather than crashing — but never actually recovered). Fixed by no longer
treating "the next message" as special: `_subscribe_all` now just fires all the
subscribe commands, and the *single* reader loop that already existed
(`_read_into_queue`) tells `result`s and `event`s apart itself, routing each
correctly regardless of interleaving. A synthetic regression test reproduces the
exact race (`tests/ha/test_client.py`, using a new `fake_ha` capability,
`interleave_event_before_subscription_result`) — verified it actually catches the
bug by reverting the fix and confirming the test fails with the identical
`CommandError` shape seen live, before restoring the fix.

**Verified live**, same flow as before, now with `hia ingest` and `hia serve` both
exercised deliberately at the same time to prove the redesign: `hia serve` alone
(no separate `hia ingest`) correctly ingested real toggled lights (`/api/entities`,
`/api/data-quality` both reflected them correctly) while *also* relaying them over a
real WebSocket connection (a standalone script, not pytest — see below) in real
time. Then `docker compose restart homeassistant` mid-stream: reconnected cleanly
with the client fix (`resumed=True`, no `CommandError`), ingestion and the API kept
working afterward, and `/api/data-quality` correctly showed
`gap_resumption_count: 1`.

**A second, unrelated environment-specific finding, also worth recording so it
isn't re-discovered the hard way**: a full end-to-end automated test through
`/api/ws/events` — Starlette `TestClient`'s WebSocket support, a background
`asyncio.to_thread` DuckDB write, and `pytest-asyncio`'s own event loop, all at
once — reproducibly crashes with `Windows fatal exception: access violation` deep in
CPython's asyncio/selectors internals (reproduced under both the default
`ProactorEventLoop` and, after switching specifically to rule it out,
`SelectorEventLoop` too — see `tests/conftest.py`). Not a bug in this project's
code — the add-on only ever runs on Linux, and the same logic is proven correct two
other ways: an isolated unit test of `AppState.ingest_and_relay` with no
`TestClient` at all (`tests/api/test_state.py`), and the real live verification
above. `tests/api/test_live.py` now sticks to a narrow WS-transport smoke test
(connects, is tracked, disconnects) that doesn't trigger the crash-prone
combination. Documented in both test files' docstrings.

42/42 tests passing (18 new across `tests/api/` and the one `hia.ha.client`
regression test), ruff and mypy `--strict` both clean.

**Explicitly not built in part 1, flagged rather than silently deferred:**

- No add-on packaging (Dockerfile, `config.yaml`, s6-overlay, `repository.yaml`) —
  the only way to actually verify the full exit criterion ("appears in the
  sidebar" needs real HAOS + Supervisor).
- `Settings.data_dir` currently assumes one household's worth of data in one
  process; nothing here re-litigates that.

### P2, part 2 — the frontend

`frontend/` (new): React 18 + TypeScript + Vite + Tailwind v4, exactly P2's scoped
UI per the roadmap — a live entity view and a data-quality page, not the fuller
Overview/Twin/Decisions/Learning/Entities panels `02-architecture.md` describes for
later phases (those need the governor/twin/training subsystems that don't exist
yet). `npm run build` writes `frontend/dist`; `hia serve` mounts it as static files
(new logic in `hia.api.app.create_app`, registered *after* the `/api/*` routes so
they always take precedence, and skipped gracefully — logged, not a startup failure
— when the directory doesn't exist, e.g. a fresh checkout or a backend-only dev
session). One deployable unit, matching the add-on's one-container reality; no CORS
to configure, since frontend and API share an origin.

**Every URL the frontend calls is resolved relative to `document.baseURI`, never an
absolute `/api/...` path** (`src/api.ts`, `vite.config.ts`'s `base: "./"`) — this
project's now-standard posture of getting ingress-compatibility right by
construction rather than by testing against real ingress, since there's still no
real HAOS hardware to test it against. `src/entityStore.ts`'s `applyStateChanged` —
merging one live `state_changed` message into the entity list, replacing an
existing row or inserting a new one — is the one piece of real logic, and is
unit-tested (Vitest) in isolation from the WebSocket/DOM.

**Verified with a real headless browser, not just curl or the unit tests** —
`chromium-cli` wasn't available in this environment, so `playwright`'s `chromium`
was installed into an isolated scratch npm project (not added to `frontend/`'s own
dependencies) and driven directly, per `run` skill's documented fallback for
exactly this case:

- Both pages screenshotted against a real `hia serve` with real dev-ha data:
  entities table populated and correct, "Live" WebSocket-connected badge green,
  data-quality stats matching what curl against `/api/data-quality` showed
  independently.
- **The actual live-push claim, proven, not assumed**: loaded the page, confirmed
  `light.ceiling_lights` was absent from the rendered DOM, toggled it via HA's REST
  API from *outside* the browser session entirely, and confirmed it appeared in the
  table — correctly sorted, state and "just now" timestamp both correct — with zero
  navigation or re-fetch triggered from the driving script. `console --errors`
  equivalent (page console listener) was empty throughout.
- This was **not out-of-the-box** — chromium-cli's absence meant installing
  Playwright and writing a small driver script — which is exactly the signal the
  `run` skill's own instructions say should be flagged for `/run-skill-generator`,
  so a future session doesn't have to rediscover this path. Not run yet; flagged
  here instead so it isn't lost.

`npm audit` found 5 dev-dependency-only vulnerabilities (vitest/esbuild's dev-server
request handling — affects `npm run dev`, not the production build or anything
`hia serve` ships) immediately after `npm install`; fixed via `npm audit fix
--force` (a vitest 4→5 major bump) before writing any application code, so nothing
shipped was ever built against a known-vulnerable toolchain even transiently.

4/4 frontend tests passing, eslint/tsc both clean, production build succeeds and
its `dist/index.html` was confirmed to reference assets with relative (`./assets/...`)
paths, not absolute ones.

**Explicitly not built, flagged rather than silently deferred:**

- No add-on packaging (Dockerfile, `config.yaml`, s6-overlay, `repository.yaml`) —
  the last piece of P2, and the only way to verify the full exit criterion for real.
- No React Router / client-side routing — the two-page UI uses local tab state, so
  there's no SPA-fallback routing concern yet; revisit `StaticFiles(html=True)`'s
  behaviour if/when that changes.
- The data-quality page polls every 10s rather than being push-driven; the live
  relay only carries `state_changed` events by design (docs/HANDOFF.md's P2 part 1).

### P2, part 3 — add-on packaging (confirmed installed and working on real aarch64 HAOS)

`repository.yaml` (repo root) and `hia/` (config.yaml, Dockerfile, rootfs, DOCS.md,
translations/en.yaml) exist. Layout was corrected mid-build after checking a real,
current official example (`home-assistant/apps-example` on GitHub, fetched
directly — not assumed from memory): `repository.yaml` and each app's folder sit
flat at the **repository root**, not nested under an `addon/` parent the way this
project's own `02-architecture.md` originally sketched — Supervisor's app-discovery
convention doesn't support that nesting.

**Three real things were figured out or fixed here, worth not re-deriving:**

1. **Supervisor always uses the app's own folder as the Docker build context** —
   confirmed by reading Supervisor's actual source
   (`supervisor/apps/build.py`: `docker buildx build .` with this directory
   bind-mounted at `/addon`), not assumed. A Dockerfile in `hia/` genuinely cannot
   `COPY` from `../backend` or `../frontend`, even though they're right there in
   the same repo. `hia/Dockerfile` works around this with a build stage that
   clones this repository's own source (`git clone --branch main
   https://github.com/wrcrooks/Home-Intelligent-Assistant.git`) rather than
   assuming co-location — see the Dockerfile's own header comment for the
   alternatives considered (restructuring the whole repo under `hia/`; a
   committed-copy sync step) and why this one was chosen.
2. **DuckDB has no musllinux (Alpine) wheel on PyPI**, and needs a full C++
   toolchain (CMake, g++) to build from source — verified directly, not assumed:
   ran `uv sync --locked` for this exact lockfile inside `python:3.13-alpine`
   (failed, `CMAKE_CXX_COMPILER not set`) and inside `python:3.13-slim`
   (glibc/Debian; succeeded cleanly, all 41 packages as prebuilt wheels). This
   ruled out `ghcr.io/home-assistant/base` (Alpine) as the add-on's base image —
   `hia/Dockerfile` uses `ghcr.io/home-assistant/base-debian:bookworm` instead.
   Given how central DuckDB is to this whole project, this was worth actually
   testing rather than trusting the "should be fine" instinct.
3. **Dockerfile ARG scoping**: an `ARG` used in a later `FROM` line must be
   declared before the *first* `FROM` in the file, not just before the `FROM` that
   uses it — declaring it in between stages reliably fails with "base name should
   not be blank". Both `BUILD_REF` and `BUILD_FROM` now sit at the top of
   `hia/Dockerfile` for this reason.

**None of the actual HA-published base images could be pulled and built on this
dev machine** — same root cause as P0's `ghcr.io/home-assistant/home-assistant`
finding (`archive/tar: invalid tar header`, this dev machine's Docker Engine
20.10.21 against recently-built image layers), now confirmed to affect
`ghcr.io/home-assistant/base`, `base-debian:trixie`, and every tag back through
`base-debian:bookworm-2026.03.1` — the registry doesn't retain tags old enough to
route around it the way `home-assistant/home-assistant:2024.6.0` did for P0. The
Dockerfile's *structure* (multi-stage clone → frontend build → backend
install → copy) was still validated end-to-end by substituting a pullable image
(`--build-arg BUILD_FROM=python:3.13-slim`) for the base-image stage only — this
caught the private-repo blocker below — but the real base image, and therefore
s6-overlay/bashio actually starting the service, remain unverified. Needs either a
Docker Engine update on this machine, or verification elsewhere.

**The actual blocker**: building with the real `git clone` step failed with
`could not read Username for 'https://github.com'` — not a bug, a discovery. This
project's GitHub repository is **private**. That breaks two things, not one:

- The Dockerfile's own clone-based build (above).
- **Home Assistant's Supervisor adding this repository as an add-on source at
  all** — "Add repository" in the Supervisor UI clones the given URL the same
  unauthenticated way. A private repo cannot be added by *anyone*, including the
  owner, without separately configuring git credentials Supervisor doesn't have a
  first-class way to supply for a simple community add-on repo.

This is a real decision about the repository's visibility, not a technical detail
to route around — flagged to the user rather than resolved unilaterally.

**Resolved**: the user made the repository public (audited first — full git
history checked for the `.env` file, JWT-shaped tokens, the real HA hostname used
in this same session's live verification, secret-shaped strings, and hardcoded
private IPs; none found; the only personal data present is the standard git commit
author email, which is normal for any public repo). Unauthenticated clone confirmed
working immediately after.

With that unblocked, three more real bugs turned up by actually building and
*running* the image (not just building it) — exactly the kind of thing this
project has repeatedly found only by testing:

1. **`curl -LsSf ... | sh` silently no-ops if `curl` is missing.** `curl` exits
   127, but its (empty) stdout still gets piped into `sh`, which exits 0 doing
   nothing — a plain `sh -c "a | b"` only checks `b`'s exit code, so the whole
   `RUN` reports success. `uv` was never actually installed; the failure only
   surfaced later, confusingly, as `uv: not found`. Fixed by installing `curl`
   explicitly (never assuming the base image has it) and downloading to a file
   before running it, so a failed download actually fails the build.
2. **`hatchling` (the backend's build backend) needs `README.md` physically
   present** — `backend/pyproject.toml` declares `readme = "README.md"`, and
   without it, building `hia`'s own package metadata fails outright. Added to the
   early `COPY`.
3. **The editable install of `hia` was created before `src/` was copied in** —
   the first working *build* still produced a broken *image*:
   `ModuleNotFoundError: No module named 'hia'` at runtime, caught only by
   actually running the container and hitting `hia serve`, not by the build
   succeeding. `uv sync` (which creates the editable install) now runs after
   `src/` is copied, not before.

**Verified by actually running the built image**, substituting a pullable image
for the base-image stage only (the real `ghcr.io/home-assistant/base-debian` still
can't be pulled on this dev machine — same known Docker Engine limitation, not
re-litigated): `hia serve` started, connected (and correctly began reconnecting
against a deliberately unreachable HA URL, exactly as designed), and served the
real built frontend plus every REST endpoint (`/`, `/api/health`, `/api/entities`,
`/api/data-quality`) correctly from inside the container. Also rebuilt for
`linux/arm64` via QEMU emulation — succeeded cleanly, confirming DuckDB has a
working manylinux wheel for aarch64 too, not just amd64.

**Resolved on real hardware, by the user, on a real aarch64 HAOS install**: the
build that couldn't be attempted with the real base image on this dev machine
(Docker Engine limitation, above) was tried for real and **succeeded** — install
failed on the first attempt with

```
Can't pull image docker:28.3.3-cli: [500] Head "https://registry-1.docker.io/...":
Get "https://auth.docker.io/token?...": net/http: TLS handshake timeout
```

which is Supervisor's *own* internal buildx helper image failing a plain network
TLS handshake against Docker Hub — confirmed by checking what `docker:28.3.3-cli`
actually is (Supervisor's containerized build environment, used so add-on builds
are consistent across HAOS and Supervised installs; see
[home-assistant/discussions#77](https://github.com/orgs/home-assistant/discussions/77))
rather than assumed — nothing in this app's own `Dockerfile` or `config.yaml`
references that image, and the failure happened before Supervisor even reached
this repo. A plain retry succeeded. Added to `hia/DOCS.md`'s new Troubleshooting
section (clock skew, IPv6, DNS/firewall to `registry-1.docker.io`/`auth.docker.io`
as the likely causes if a retry doesn't fix it) so it shows up in the add-on's own
Documentation tab, not just here.

This confirms the real base image pulls and builds correctly on real aarch64
hardware — the specific thing this dev machine's Docker Engine couldn't test.

**Then it was actually started, and that surfaced a real bug**: s6 logged

```
s6-supervise hia: warning: unable to spawn ./run (waiting 60 seconds): Permission denied
```

`rootfs/etc/services.d/hia/run` and `finish` were written on this project's
Windows dev machine, which doesn't track a Unix executable bit — they were
committed as plain `100644` files, and `Permission denied` spawning a script
(rather than a "bad interpreter" error) is the classic symptom of s6 trying to
exec a file with no `+x` at all. Confirmed directly: `git ls-files -s` on both
showed `100644`. Fixed two ways, not just one, since a Windows dev environment
makes it easy for this to regress silently: `git update-index --chmod=+x` on both
files (so the repository itself carries the right mode), *and* an explicit
`RUN chmod +x` in `hia/Dockerfile` after `COPY rootfs /` (so the image is correct
regardless of whatever mode the checkout that built it happened to have — the
belt-and-suspenders version that doesn't depend on getting the first part right
forever). Verified by actually rebuilding and checking inside the image
(`ls -la /etc/services.d/hia/` → `-rwxr-xr-x` on both, where it was `-rw-r--r--`
before) — not just trusting the `chmod` line was in the Dockerfile.

**That fix did not reach the user** — reinstalling the add-on (which clears its
`/data`, not Supervisor's underlying Docker build cache) and clicking "Rebuild"
several times still showed the identical `Permission denied` log. Root-caused, not
guessed: `hia/Dockerfile`'s clone step (`RUN git clone --depth 1 --branch main
https://github.com/...`) is one `RUN` instruction whose *text* never changes
between builds — Docker/BuildKit caches `RUN` steps by instruction text, not by
whatever the remote branch currently points to. So every subsequent build,
however triggered, kept reusing the very first cached clone from this add-on's
first successful build (before the chmod fix even existed), regardless of what
had since been pushed to GitHub.

Verified by deliberately reproducing it locally before trusting the fix: built
once (fresh clone), rebuilt again unchanged (`git clone` step showed `CACHED` —
the exact bug), then rebuilt a third time after bumping a new `CACHE_BUST` build
arg (`git clone` step actually re-ran, no `CACHED`). Fixed by adding
`ARG CACHE_BUST` to `hia/Dockerfile`, referenced inside the clone `RUN`
instruction's own text — bumped every time a real fix needs to actually reach a
build from now on, not just pushed to `main` and hoped for. Also bumped
`config.yaml`'s `version` (0.1.0 → 0.1.1), which had never been touched despite
several real fixes shipping — Supervisor uses it to signal an update is available
at all, so leaving it unchanged may also have contributed to the user not
getting a fresh build offered.

**Reached the user, and confirmed**: after the repository-level refresh (Settings →
Add-ons → Add-on Store → check for updates/reload) and rebuilding the add-on, the
`Permission denied` error was gone, and the user confirmed on 2026-09-16 that the
add-on is **installed and working** — `bashio::config` reading the set options,
`hia serve` coming up, and the add-on reachable through ingress (appears in the
sidebar, shows live state). This is P2's full exit criterion, confirmed on real
hardware, not assumed.

### P3, slice 1 — provenance classifier, Layer 1 + Layer 2

`docs/04-roadmap.md` P3 is split into two slices, matching this project's pattern
of shipping large phases in verifiable increments: this slice builds **Layer 1**
(context-chain resolution) and **Layer 2** (automation-fire correlation) from
`docs/05-provenance.md` §4; **Layer 3** (per-actor classification of every
observed `user_id`, plus the setup UI for tagging HA users) and **admission
control** (`automation_share`, §6) are deferred to a second slice.

`hia.provenance` (new):

- `chain.ContextChainResolver` — Layer 1. Built once per run from every stored
  `automation_triggered`/`script_started`/`call_service` event. Resolving a row's
  context checks two real, live-verified shapes of HA's context propagation: the
  row's own `context_id` matching a recorded machine event directly (the
  **same-id-sharing** pattern confirmed live in P1 — a REST-triggered
  `call_service` event and the `state_changed` it caused shared one `context_id`,
  not a parent/child pair), and `context_parent_id` matching one (the **parent-id
  chaining** pattern `05-provenance.md` §3 describes for automations). Climbs
  through a matched event's own `context_parent_id` to find the outermost
  ancestor (a service call nested inside an automation resolves to the
  automation, not just the immediate call), depth-capped and cycle-guarded.
- `correlate.AutomationCorrelator` — Layer 2, the layer that actually closes the
  sun/time-trigger hole (`05-provenance.md` §3): since those triggers set no
  context at all, there is nothing for Layer 1 to chain-walk. Instead this
  correlates on *timing* — if automation `X` fires and one of its known action
  targets changes state within a short window (default 5s) afterward, the change
  is attributed to `X` regardless of context. Target entities are bucketed and
  time-sorted so a lookup is a bisect, not an O(firings) scan per row classified.
- `classify.classify()` — combines both layers. **Deliberately stops at a binary
  output: `automation` or `unknown`**, never `human` — see the module's own
  docstring. Without Layer 3, a row neither layer explains might be a genuine
  human action, or might be an external automation (Node-RED, AppDaemon) whose
  service calls carry a `user_id` and look human by context alone
  (`05-provenance.md` §3) — this slice has no way yet to tell those apart, and
  guessing `human` would be exactly the "abstain rather than guess" non-negotiable
  in `CLAUDE.md` broken in the one place it matters most. `unknown` is a safe
  default either way: Layer 3, once built, only ever *upgrades* some `unknown`s to
  a real class, never has to walk back a wrong `automation` guess.
- `report.py` — runs the classifier over every stored `state_changes` row and
  summarizes it (counts by layer, top attributed automations). **Not** the P3 exit
  criterion itself (a hand-labelled 200-row sample at >95% precision, including 20+
  sun/time-triggered changes — needs real house data and manual labelling no
  single session can produce, same category as P1's 72-hour soak test); this is a
  sanity-check report, exposed as `hia provenance-report`.

`hia.ha.automations` (new): fetches automation configs and extracts action
targets — Layer 2's other input. Automation configs are **not available over the
websocket API at all**; the only way to read one is Home Assistant's REST-only
`config` component, `GET /api/config/automation/config/{config_id}` — confirmed by
reading `homeassistant/components/config/automation.py` and its
`BaseEditConfigView` base class directly (undocumented in the public REST API
reference, which doesn't mention this endpoint). `config_id` is the automation's
own `id` field, exposed as `unique_id` on its entity registry entry. This endpoint
requires an **admin-privileged token** (`@require_admin`) — a new constraint no
other part of this project has needed, since everything else goes over the
websocket API. `extract_action_targets()` walks an automation's action block
generically by key name (`entity_id`, wherever it appears — bare or under
`target:`) rather than enumerating action types, so a new HA action type is
silently included rather than silently missed; `condition`/`conditions`/`if`
sub-blocks are explicitly skipped so an entity a `choose` block only *reads* to
decide whether to run never shows up as a false-positive target.

`hia.ha.client.HomeAssistantClient` gained `rest_base_url()` and a `.get()`
method — a thin authenticated REST GET, alongside the existing websocket `.call()`
— since automation configs needed one and no REST client existed yet.

**Verified two ways.** Synthetically, against the fake HA fixtures (17 new tests:
`tests/provenance/`, `tests/ha/test_automations.py`) — same-id-sharing and
parent-id-chaining resolution, multi-hop climbing, cycle/depth guarding, the
sun-trigger correlation scenario Layer 1 cannot see, window/entity/ordering edge
cases for correlation, the abstain-not-guess fallthrough, and action-target
extraction across old/new HA action syntax and nested `choose`/`parallel` blocks
(including that a `choose` block's own `conditions` entities are correctly
excluded from its targets). And **live, against the real house** this project has
verified every other phase against: `HomeAssistantClient.get()` was exercised for
real (`/api/config/automation/config/definitely-does-not-exist` → a real `None`
from HA, not a raised exception), which also incidentally confirmed **the user's
real long-lived token is admin-privileged** — `require_admin` rejects
*before* checking whether an id exists, so a 404 (not a 401/403) on a
guaranteed-nonexistent id proves admin access works, not just that the endpoint
exists.

**What's still genuinely unverified, flagged rather than glossed over**: this real
house currently has **zero configured HA automations** (`hia registry` against it
returned none) — so there is nothing to run the *success* path of
`hia.ha.automations.fetch_targets` against for real, and Layer 2's actual
correlation logic (as opposed to its REST plumbing) has only been exercised
synthetically so far. The local `hia.duckdb` store from this dev machine's ongoing
soak test (see "What to do next" below) is currently held read-write by a running
`hia serve` process — deliberately left running rather than interrupted to run
`hia provenance-report` against it, since DuckDB's one-writer-or-many-readers
model (P2 section above) means a read-only open would just fail with "Conflicting
lock is held" while that process is up. Running `hia provenance-report` against
real accumulated history is real, near-term follow-up work, not done here.

44/44 → 63/63 tests passing (19 new), ruff and mypy `--strict` both clean.

## What to do next

1. **Keep `hia serve` running unattended for 72+ hours** to close out P0/P1's
   soak-test criteria for real — the one piece of verification no single session
   can complete honestly. It's already running locally against the real house as
   of this writing (started earlier this session) — **do not kill or restart it**
   just to run something else against the same `HIA_DATA_DIR`; DuckDB's
   one-writer-or-many-readers model means a read-only tool (`hia data-quality`,
   `hia provenance-report`) cannot run at the same time regardless.
2. **Run `hia provenance-report` against real accumulated history** once the
   store can safely be opened read-only (after the soak test above, or against a
   separate `HIA_DATA_DIR`) — the classifier itself is only verified
   synthetically plus one real REST smoke test so far (P3 slice 1, above); this
   is the natural next real-data check, same category as the soak test.
3. **This real house has no HA automations configured at all** (confirmed via
   `hia registry`) — Layer 2 correlation has nothing to correlate against here
   until some exist. If the user adds even one automation, `hia
   ha.automations.fetch_targets`'s actual *success* path (not just its 404
   path, which is all that's verified so far) becomes checkable for real. Worth
   asking about, not assuming.
4. **P3 slice 2 — Layer 3 (actor classification) and admission control**
   (`docs/05-provenance.md` §4 Layer 3, §6): classify every observed `user_id`
   as human/voice-bridge/service-account, the setup UI for tagging HA users, the
   temporal-regularity heuristic, and `automation_share`-based admission
   control. This is what upgrades slice 1's `unknown` rows into the full
   taxonomy (`human_physical`/`human_ui`/etc. from §2) and is a prerequisite for
   the actual P3 exit criterion (a hand-labelled 200-row sample at >95%
   precision) — that validation needs real accumulated, Layer-3-classified
   history and manual labelling, neither of which exist yet.
5. Consider running `/run-skill-generator` to capture the Playwright-based
   screenshot verification path as a project skill, since it wasn't available
   out of the box this time.
6. `statistics` table backfill and live/backfill de-duplication (P1, above) are real
   gaps worth closing, but neither blocks P2 or P3 — track them, don't let them
   stall forward progress.

Do not skip ahead to the interesting parts. P1 started a data clock that cannot be
rewound — which is exactly why live ingestion was built before backfill, not after —
and every model in the project is bottlenecked on how long it has been running.

## This dev machine has an RTX 3060

Confirmed via `nvidia-smi` (driver 560.94, CUDA 12.6). This does **not** change the
add-on's shipped CPU-only requirement (`CLAUDE.md`, `06-model-training.md`) — most real
HA hosts have no GPU, and the add-on must run correctly without one. It does mean:
model code from P4/P5 onward should resolve its device with the standard
`torch.device("cuda" if torch.cuda.is_available() else "cpu")` pattern so local
training iterates faster on this box, while remaining correct with no GPU present
elsewhere. Nothing in P0 uses torch yet; this is a note for whoever writes the first
Tier C model.

## How the design got here, in one paragraph

The naive framing — one RL agent, all entity states as observation, all services as
actions — fails for four independent reasons, any one fatal: a house produces ~1
episode/day against the 10⁵–10⁷ transitions model-free RL needs; exploration in
someone's living room is a product defect rather than a hyperparameter; there is no
reward signal in the box; and the house is non-stationary. The design therefore
reformulates into three tiers matched to what each technique is actually good at
(bandits for immediate reversible decisions, simulation-trained sequential RL for
thermal/energy, supervised prediction for the rest), derives reward automatically from
provenance-filtered human actions, and gates all actuation behind a governor with an
earned autonomy ladder. If you find yourself designing a single end-to-end agent, you
have circled back to the thing that does not work.

## Decisions already made, with reasons

Recorded so they are not silently re-litigated. Each is reversible if you have a real
argument — but make the argument out loud.

| Decision | Reason |
|---|---|
| Three-tier learning, not one agent | Sample efficiency; see above |
| Digital twin is the simulator, not just a visual | Justifies its cost three times over: training env, explanation surface, model-health check |
| DuckDB + Parquet over Postgres/Timescale | Embedded, analytical, no second container inside an add-on. Timescale stays an option for large installs |
| Automated reinforcement default, human ratings optional | The house already emits the signal; asking people to rate things does not scale and biases toward the annoyed |
| Provenance filtering as its own subsystem | Without it the reward loop is self-referential and can run away |
| Abstain on unknown provenance | A zero reward is a false claim about the world; a smaller dataset is not |
| 2D floorplan is source of truth, 3D is a view | One geometry model, not two that drift apart |
| `amd64` + `aarch64` only | PyTorch on armv7 is poor and the hardware cannot train anything useful |
| Add-on *and* Compose + HACS integration | Add-ons only exist on HA OS/Supervised; Container users are otherwise excluded |
| GBTs (LightGBM) before neural nets for behaviour cloning | Small tabular data regime; upgrade only past ~6 months / 50k+ labelled transitions ([06-model-training.md](06-model-training.md) §2) |
| Grey-box RC network before a learned thermal model | 10 days of data cannot identify a deep model; RC parameters are physically checkable and the twin needs to simulate forward, not just predict one step |
| IQL on real logs is a check on the simulator, not primarily a deployed policy | If it disagrees sharply with the sim-trained policy, the twin is untrustworthy and nothing gets promoted |
| MLflow in local file-store mode for experiment tracking | Local-only rules out cloud trackers; file mode needs no server process inside a resource-constrained add-on |
| Splits are temporal, never k-fold/random | House data is autocorrelated at short lag; the goal is generalising forward in time, not backward |
| Local only, no cloud | Privacy is the whole reason people self-host HA |
| Recorder backfill checks required columns exist, not `schema_version >= N` | Getting N exactly right is its own research problem across every HA release; a version this project has never seen (schema v43, discovered live) worked correctly on the first try because of this |
| `ulid-transform` for context bin→string, not a hand-rolled decoder | It's the exact package HA's own recorder uses — guarantees a backfilled `context_id` is byte-for-byte the same string live capture would have produced for the same event |
| `state_changes.context_id` nullable, `events.context_id` not | Backfill reads messier historical data (a few very old rows predate context tracking); live rows are guaranteed one by construction of the HA client |
| `hia serve` owns ingestion itself, not a separate `hia ingest` process | DuckDB has no one-writer-plus-separate-readers mode at all — verified live on Linux, not assumed; see P2 section above and `hia.api.state` |
| Raw ASGI middleware for the Supervisor-IP restriction, not `BaseHTTPMiddleware` | `BaseHTTPMiddleware` silently never sees `websocket`-scope connections — would have left the live relay completely unprotected on a real add-on |
| Frontend resolves every URL relative to `document.baseURI`, never `/api/...` | Ingress serves the app from a runtime-assigned path prefix never known at build time; only relative resolution follows it automatically, and there's no real HAOS to catch an absolute-path mistake on |
| `hia serve` mounts the built frontend itself (`StaticFiles`), not a separate static host | One deployable unit, matching the add-on's one-container reality; no CORS to configure since frontend and API share an origin |
| P2 UI scoped to entities + data-quality only, not the full panel set | The fuller Overview/Twin/Decisions/Learning/Entities UI (`02-architecture.md`) depends on subsystems (governor, twin, training) that don't exist yet — building it now would mean building against nothing |
| `hia/Dockerfile` clones this repo's own source rather than referencing sibling dirs | Supervisor always uses the app's own folder as the Docker build context (confirmed from Supervisor's own source) — a Dockerfile in `hia/` cannot `COPY ../backend` no matter how the rest of the repo is laid out |
| `base-debian`, not `base` (Alpine), for the add-on's base image | DuckDB has no musllinux wheel and needs a full C++ toolchain to build from source — verified directly by running `uv sync` inside both, not assumed |
| `repository.yaml` and `hia/` flat at the repo root, not under `addon/` | Matches a real, current official example fetched directly; Supervisor's app-discovery convention doesn't support the nesting this project originally sketched |
| P3 split into slice 1 (Layer 1 + Layer 2) and slice 2 (Layer 3 + admission control) | Matches this project's pattern for large phases; Layer 3 (actor classification) and the exit criterion's hand-labelled precision check both need real accumulated data slice 1 alone can't produce |
| Slice 1's classifier outputs `automation`/`unknown`, never `human` | CLAUDE.md's "abstain rather than guess" — without Layer 3, an unexplained row could be a real human action or an external automation (Node-RED) whose context looks human (`05-provenance.md` §3); guessing `human` would recreate the exact trap the taxonomy exists to avoid |
| Automation configs fetched over REST (`/api/config/automation/config/{id}`), not the websocket API | This endpoint doesn't exist over the websocket API at all — confirmed by reading HA's own `config/automation.py` source, not assumed |
| `extract_action_targets` walks generically by key name, skipping `condition`/`conditions`/`if` | A new HA action type is silently included rather than silently missed; explicitly excluding condition blocks avoids treating an entity a `choose` block only *reads* as something it can act on |

## Platform facts worth not re-deriving

Verified during design; all cited in the docs.

- **Add-ons**: `config.yaml` with `ingress: true`, Supervisor API via `SUPERVISOR_TOKEN`
  at `http://supervisor/`, connections restricted to `172.30.32.2`, honour
  `X-Ingress-Path` for base-URL rewriting.
- **Recorder schema**: `entity_id` is normalised into `states_meta` (join on
  `metadata_id`); long-term rollups live in `statistics` / `statistics_short_term`
  keyed by `statistics_meta` (not yet read by this project — see P1 part 2 above).
  `last_changed`/`last_updated` are stored as `*_ts` epoch-float columns, not
  datetimes. Context (`context_id_bin`, `context_user_id_bin`, `context_parent_id_bin`)
  is 16-byte binary, not string: `context_id`/`context_parent_id` are ULID-encoded,
  `context_user_id` is UUID-encoded — decode with the `ulid-transform` PyPI package
  (the same one HA's own recorder uses) plus stdlib `uuid`, never a reimplementation.
  Attributes live in a separate deduplicated `state_attributes` table, joined via
  `attributes_id`, column `shared_attrs` (JSON text). None of this is pinned to one
  `schema_version` number — it has moved a lot (schema v53 on HA's dev branch as of
  this writing; a real HA 2024.6.0 instance backfilled clean at schema v43) — so
  detect column *presence*, not a version-number cutoff.
- **Context attribution**: normally propagates from an automation into the `parent_id`
  of everything it causes — **except that Sun and Time-of-Day triggers set no context
  at all**. This single fact is why the provenance classifier needs three layers rather
  than one. It is the most important platform detail in the project.
- **`automation_triggered` still fires** even when the trigger set no context, which is
  what makes the correlation fallback possible.
- **Node-RED** authenticates as a user account, so its service calls carry a `user_id`.
- **DuckDB concurrency**: a database file is opened in *either* read-write (exactly
  one process, full stop) *or* read-only (any number of processes, none writing) —
  never a mix of one writer and separate readers. Verified against a real Linux
  container after an initial (wrong) reading of DuckDB's own docs assumed otherwise;
  see https://duckdb.org/docs/stable/connect/concurrency and the P2 section above.
- **HA's websocket protocol interleaves messages across subscriptions**: when
  multiple `subscribe_events` commands are in flight, an `event` for an
  already-acknowledged earlier subscription can arrive before the `result` for a
  later one — reliably, right after a reconnect. A client that assumes "the next
  message after I send a subscribe command is that command's result" will
  misinterpret a real event as a failed subscription. See `hia.ha.client`'s
  `_read_into_queue` and the P2 section above.

## Open questions for the user

None of these block P0. Ask when the relevant phase arrives.

1. **Target house.** Which HA install is this developed against — how many entities,
   how much recorder history is retained, SQLite or MariaDB? This sizes P1 and
   determines whether backfill is hours or days.
2. **Node-RED in use?** If so, is its admin API reachable? Changes how much of the
   provenance Layer 2 work is needed. The design assumes it may be present.
3. **Deployment hardware.** HA OS on a mini-PC, a NAS, a Pi? Decides whether training
   runs on the same box or somewhere else.
4. **First decision points.** Which two or three entities is the user willing to let
   this act on first? Should be reversible, low-stakes, and frequently used — porch
   or hallway lighting is the usual answer. Needed by P6, worth asking earlier.
5. **Energy data.** Is there a tariff integration, solar, a battery, an EV? Tier B has
   nothing to optimise without at least a tariff.
6. **License.** Currently TBD in the README.

## Things most likely to go wrong

Ranked by expected pain, not probability.

1. **Provenance misclassification poisoning the reward silently.** It will not throw an
   error; models will just quietly learn the wrong thing. Build the manual inspection
   panel early and actually look at it.
2. **Discovering at P5 that the twin is too inaccurate for Tier B.** The gate exists to
   catch this. If it fails, the honest move is to stay on bandits and ship that — not
   to weaken the gate.
3. **Class imbalance quietly collapsing every model to "predict nothing."** Check the
   predicted-action rate on any new model before believing its accuracy figure.
4. **Scope creep toward a general "AI house assistant."** The out-of-scope list in
   `01-scope.md` is binding. Voice and LLM interfaces in particular will look tempting
   and will consume the project.
5. **Building models before there is enough data to build them on.** The most common
   way this kind of project dies is a beautiful untrained pipeline.

## If you have limited time

The minimum genuinely useful thing this project can be: **P0 → P1 → P2 → P3**, i.e.
reliable ingestion with trustworthy provenance and a UI to look at it. That alone is a
better home-data observability tool than most people have, and it is the substrate
everything else needs. P7 (bandits live) is the first phase that changes how the house
behaves.
