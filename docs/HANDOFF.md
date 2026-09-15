# Handoff

For an agent or developer picking this up cold. Written 2026-09-09, updated 2026-09-15
to add [06-model-training.md](06-model-training.md), updated again 2026-09-15 as P0
got underway.

## Where things stand

**P0 is in progress.** `backend/` (Python 3.13, managed with `uv`) now has:

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

**What is *not* yet done, and is the actual remainder of P0's exit criterion:**
running `uv run hia watch` against a genuinely live Home Assistant instance and
restarting Core mid-stream to confirm it reconnects and keeps going. This was not
completed this session because Docker Desktop wasn't running on the dev machine and
starting it wasn't done unprompted — see `compose/dev-ha/docker-compose.yml` for the
exact commands. The unit-test reconnect coverage is solid, but it is still a fake
server; treat the real-instance check as outstanding until someone has actually run
it.

Nothing about the *design* has been validated against a running Home Assistant
instance either — the WebSocket protocol details (auth handshake, `subscribe_events`,
message-id rules, the five registry-list command names) were confirmed against HA's
own developer docs before being implemented, but that's a documentation check, not a
live one.

## What to do next

1. **Finish P0's real-instance check** (above) — the one piece of verification this
   session couldn't complete.
2. Move to **P1 in [04-roadmap.md](04-roadmap.md)**: event storage (DuckDB + Parquet)
   and — critically — start capturing `context` fields and `automation_triggered` /
   `call_service` on every event from day one, since that capture cannot be
   retrofitted (see "Traps" in `CLAUDE.md`).

Do not skip ahead to the interesting parts. P1 starts a data clock that cannot be
rewound, and every model in the project is bottlenecked on how long it has been
running. Getting ingestion live on the user's real house is worth more than any amount
of early model work.

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

## Platform facts worth not re-deriving

Verified during design; all cited in the docs.

- **Add-ons**: `config.yaml` with `ingress: true`, Supervisor API via `SUPERVISOR_TOKEN`
  at `http://supervisor/`, connections restricted to `172.30.32.2`, honour
  `X-Ingress-Path` for base-URL rewriting.
- **Recorder schema**: `entity_id` is normalised into `states_meta`; long-term rollups
  live in `statistics` / `statistics_short_term` keyed by `statistics_meta`. Context
  columns (`context_id_bin`, `context_user_id_bin`, `context_parent_id_bin`) arrived in
  schema v36. The schema migrates across HA releases, so detect `schema_version`.
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
