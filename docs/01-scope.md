# Home Intelligent Assistant — Scope

## What this is

A companion service for Home Assistant that learns how a specific household actually
behaves, builds a physical + behavioural model of the home from HA's own data, and
gradually takes over automation decisions — with a web UI that makes the learning
process and the model's reasoning legible.

## What "success" means

Not "an RL agent runs my house." Success is:

1. The system observes for a few weeks and produces a model of the home that
   **predicts** occupancy, lighting preference and thermal response better than
   naive baselines.
2. It proposes actions that the occupants accept more often than they reject.
3. On a small opt-in set of entities, it acts autonomously and the household
   **overrides it less over time**.
4. On energy-bearing loads (HVAC, water heater, EV, battery), it saves measurable
   money without measurable comfort regression, verified by A/B.

If those hold, the RL is doing real work. If they don't, the UI will say so — the
metrics dashboard exists to make failure visible, not to make progress look good.

## Users and deployment targets

| Target | How it ships | Priority |
|---|---|---|
| HA OS / Supervised | Add-on (Docker container managed by Supervisor, sidebar UI via ingress) | P0 |
| HA Container / Core | `docker compose` service + HACS custom integration for the HA-side entities | P1 |
| Development | `uv run hia ...` against a live or replayed HA instance | P0 |

Architectures: `amd64` and `aarch64` only. `armv7` is dropped — PyTorch support is
poor and the target hardware (RPi 3, older Odroid) cannot train anything useful.
Minimum practical host: 4 GB RAM, 4 cores, ~10 GB disk. A separate mini-PC or NAS
is the expected deployment; a Pi 4 can run inference but training will be slow.

## Boundaries

**In scope**
- Read HA state via WebSocket (live) and the recorder database / REST history (backfill).
- Entity / device / area registry sync, including labels and floors.
- A house model ("digital twin") with room geometry, entity placement, and fitted
  physical sub-models.
- Learned policies over a curated set of *decision points*, not raw entity control.
- A safety governor that stands between any policy and any `service.call`.
- Web UI: metrics, decision timeline, twin visualisation (2D first, 3D after), and
  per-entity autonomy control.
- Writing results back into HA as entities so they can be used in normal automations.

**Explicitly out of scope (at least for v1)**
- Voice, LLM chat agents, or natural-language automation authoring.
- Camera / computer vision.
- Any cloud component. All training and inference is local. No telemetry.
- Federated or cross-household learning.
- Security- or safety-critical control (locks, garage doors, alarm, smoke, water
  shutoff, medical). These are permanently denylisted for actuation.
- Replacing HA automations. This runs *alongside* them and can be switched off
  without breaking the house.

## Design constraints that drive everything else

1. **A house produces about one episode per day.** Model-free RL needs 10^5–10^7
   transitions. Anything that requires online exploration in the real home to
   converge will not converge within the life of the project. Learning must happen
   offline, in simulation, or via sample-efficient bandits.
2. **Exploration in someone's home is a product failure, not a hyperparameter.**
   Random actions are unacceptable. Exploration budget is bounded, scheduled, and
   restricted to reversible low-stakes entities.
3. **There is no reward signal in the box.** It has to be constructed, and the
   construction is the hardest design problem in the project. See
   [03-learning.md](03-learning.md).
4. **The house is non-stationary.** Seasons, occupants, new devices. Models must
   retrain continuously and the system must detect its own drift.
5. **If the container dies, the house must stay sane.** No latched weird states,
   no half-applied plans.
