# Handoff

For an agent or developer picking this up cold. Written 2026-09-09; updated
2026-09-15 several times as work actually landed — see the bottom of "Where things
stand" for the latest.

## Where things stand

**P0 and P1 are both done, both verified live.** The one thing genuinely left open is
the 72-hour unattended soak test, which no single session can complete honestly (see
below) — everything buildable in one sitting is built.

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

## What to do next

1. **Run `hia ingest` against a real house for 72+ hours, unattended**, to close out
   P0/P1's soak-test criteria for real — the one piece of verification no single
   session can complete honestly. `hia ingest` is built to run indefinitely and
   reconnect through anything; actually doing it is what's left.
2. Then **P2 in [04-roadmap.md](04-roadmap.md)**: the web UI shell.
3. `statistics` table backfill and live/backfill de-duplication (above) are real
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
