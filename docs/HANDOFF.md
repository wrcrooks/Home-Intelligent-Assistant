# Handoff

For an agent or developer picking this up cold. Written 2026-09-09; updated
2026-09-15 several times as work actually landed — see the bottom of "Where things
stand" for the latest.

## Where things stand

**P0, P1 and P2's backend+frontend are done, all verified live** — including a real
browser rendering real live updates against a real house. **P2's packaging is
started but blocked on a real decision only the user can make: the repository is
currently private, and Supervisor adds an add-on repository the same way it would
clone any other git URL — unauthenticated.** A private repo cannot be added as an
add-on source by anyone, including the owner, not just by this project's own build
process. See the P2 packaging section below before doing anything else here — it's
not a detail, it's a precondition for the roadmap's own exit criterion.

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

No add-on packaging (Dockerfile, `config.yaml`, s6, ingress deployment) yet — what's
left of P2's full exit criterion ("installs on real HA OS hardware, appears in the
sidebar"), which also needs actual HAOS hardware this session doesn't have, same
caveat as the soak test.

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

### P2, part 3 — add-on packaging (started, blocked on a real decision)

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
to route around — flagged to the user rather than resolved unilaterally. If/when
the repo goes public, the Dockerfile's `git clone` step should just start working
as written; nothing else changes.

## What to do next

1. **Resolve the repository-visibility question above** before doing anything else
   with add-on packaging — everything past this point assumes it's settled.
2. Once resolved: get a real build of `hia/Dockerfile` (unsubstituted base image)
   working — needs either a newer Docker Engine on a dev machine, or building
   elsewhere (GitHub Actions runners don't have this project's local Docker Engine
   version problem).
3. **Run `hia serve` (not `hia ingest` — it supersedes it for normal operation)
   against a real house for 72+ hours, unattended**, to close out P0/P1's soak-test
   criteria for real — the one piece of verification no single session can
   complete honestly.
4. Consider running `/run-skill-generator` to capture the Playwright-based
   screenshot verification path as a project skill, since it wasn't available
   out of the box this time.
5. `statistics` table backfill and live/backfill de-duplication (P1, above) are real
   gaps worth closing, but neither blocks P2 — track them, don't let them stall
   forward progress.

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
