# CLAUDE.md

Project instructions, loaded automatically each session. Keep this file short — the
detail lives in `docs/`. New here? Read [docs/HANDOFF.md](docs/HANDOFF.md) first.

## What this is

An AI/ML companion add-on for Home Assistant. It learns how a household actually
behaves from HA's APIs, builds a physical + behavioural model of the home, and
progressively takes over automation decisions. Web UI for metrics and a digital twin.

**Current state: P0 complete.** `backend/` has the package scaffold, the Home
Assistant websocket client (auth, subscribe, reconnect + resubscribe, monotonic
event sequence, bounded-queue backpressure), registry sync, and a `hia` CLI
(`watch` / `registry` / `check`), covered by tests and clean under ruff + mypy —
and its exit criterion (`hia watch` surviving a Core restart) has been verified
against a real running Home Assistant instance, not just the test suite. Next up is
P1 in [docs/04-roadmap.md](docs/04-roadmap.md); see [docs/HANDOFF.md](docs/HANDOFF.md)
for the detail.

## Read before designing anything

| Doc | Why you need it |
|---|---|
| [docs/01-scope.md](docs/01-scope.md) | Boundaries and the out-of-scope list, which is binding |
| [docs/02-architecture.md](docs/02-architecture.md) | Modules, data flow, stack |
| [docs/03-learning.md](docs/03-learning.md) | Why naive RL fails here and what replaces it |
| [docs/04-roadmap.md](docs/04-roadmap.md) | Phases, exit criteria, risks |
| [docs/05-provenance.md](docs/05-provenance.md) | Automated reinforcement and the automation filter |
| [docs/06-model-training.md](docs/06-model-training.md) | Concrete training plan: data pipeline, per-model specs, compute budget, evaluation |

## Non-negotiables

These are settled decisions with reasons behind them. Do not quietly reverse one
because a simpler approach looks tempting mid-task — if you think one is wrong, say so
explicitly and let the user decide.

1. **No single end-to-end RL agent over all entities.** A house yields ~1 episode/day
   against the 10⁵–10⁷ transitions model-free RL needs. Learning happens offline, in
   simulation, or via sample-efficient bandits. See `03-learning.md`.
2. **Nothing calls a HA service except through the governor.** Hard constraints, the
   autonomy ladder, the kill switch and the deadman are not optional layers to add
   later.
3. **Machine-caused events never generate reward.** HA automations, Node-RED,
   device-local rules and our own actions are all filtered out. This is not a nicety —
   counting them creates a self-reinforcing runaway. See `05-provenance.md`.
4. **Abstain rather than guess.** An unclassifiable event produces *no* training
   signal. Never substitute a zero reward for an unknown.
5. **Baselines stay in every comparison** — do-nothing, existing automations, behaviour
   cloning. If they win, the UI says so.
6. **Local only.** No cloud, no telemetry, no phoning home.
7. **Permanently denylisted from actuation:** locks, alarms, garage doors, water
   shutoff, medical equipment.

## Traps that have already cost research time

- **Sun and Time-of-Day triggers set no context.** A sunset-triggered automation
  produces `parent_id = None, user_id = None` — identical to a physical wall-switch
  press. Context-based attribution alone is *not* sufficient; the automation-fire
  correlation layer exists specifically for this.
- **Node-RED actions carry a `user_id`** because it authenticates as a user account.
  "Has a user_id, therefore human" is backwards.
- **Three things cannot be retrofitted:** ingestion, provenance capture (context fields
  + `automation_triggered` on every event), and propensity logging. Ship them early
  even when nothing consumes them yet.
- **"Nothing happened" is ~99.9% of timesteps.** Unbalanced, it collapses every model
  to "predict nothing."
- **Add-ons only exist on HA OS/Supervised.** Container/Core users need the Compose
  path plus the HACS integration.

## Conventions

- Python 3.13, `uv`, ruff, mypy, pytest. The shipped add-on must run **CPU-only** —
  most HA hosts have no GPU — but a training machine with CUDA (this project's dev
  box has an RTX 3060) should be used opportunistically: `torch.device("cuda" if
  torch.cuda.is_available() else "cpu")`, never a hard requirement.
- Target `amd64` and `aarch64`. `armv7` is deliberately unsupported.
- Under ingress: honour `X-Ingress-Path`, bind the declared ingress port, accept only
  `172.30.32.2`.
- The recorder DB is **read-only, always**. Detect `schema_version` and adapt.
- Docs are numbered and cross-linked; update them in the same commit as the code that
  invalidates them.
