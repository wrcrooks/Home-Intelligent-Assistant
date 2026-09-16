# Home Intelligent Assistant

An AI/ML companion add-on for Home Assistant that learns how a household actually
behaves, builds a physical and behavioural model of the home from Home Assistant's own
APIs, and progressively takes over automation decisions — with a web UI that makes the
learning process and the model's reasoning visible.

> **Status: P0, P1 and P2 all complete.** The Home Assistant websocket client, a
> DuckDB-backed event store with recorder backfill, `hia serve` (owns the store
> outright — ingests, serves REST reads, relays live events over a WebSocket, all
> in one process), a React frontend (live entity view + data quality, served
> directly by `hia serve`), and add-on packaging (`repository.yaml`, `hia/`) are
> all built and verified — including a real browser rendering real live updates,
> and a confirmed install on real aarch64 HA OS hardware: the add-on builds, starts,
> and is reachable through ingress. The repository is public. **P3 (provenance
> classifier) is underway**: slice 1 — context-chain resolution and
> automation-fire correlation — and slice 2's backend — actor classification
> storage, suggestion heuristics, and the API/CLI to confirm one — are both
> built and verified, including live checks against a real house's real user
> accounts. Still missing: the frontend tagging page and admission control. See
> [docs/HANDOFF.md](docs/HANDOFF.md) for exactly where things stand.

## Documents

| | |
|---|---|
| [HANDOFF.md](docs/HANDOFF.md) | **Start here** — current state, next step, decisions already made, open questions |
| [01-scope.md](docs/01-scope.md) | What this is, deployment targets, boundaries, and the constraints that drive the design |
| [02-architecture.md](docs/02-architecture.md) | Components, data flow, repository layout, stack |
| [03-learning.md](docs/03-learning.md) | Why naive RL fails here, the three-tier reformulation, reward design, safety ladder, evaluation |
| [04-roadmap.md](docs/04-roadmap.md) | Twelve phases with measurable exit criteria, critical path, risk register |
| [05-provenance.md](docs/05-provenance.md) | Automated reinforcement, and the provenance filter that keeps automations out of the reward signal |
| [06-model-training.md](docs/06-model-training.md) | The concrete training plan: data pipeline, per-model specs, compute budget, experiment tracking, evaluation |

## Development

The backend lives in `backend/` (Python 3.13, `uv`) — see
[backend/README.md](backend/README.md) for setup, running `hia serve`, and the test
suite. The frontend lives in `frontend/` (React, TypeScript, Vite) — see
[frontend/README.md](frontend/README.md). `compose/dev-ha/` spins up a throwaway
Home Assistant instance to develop against.

## The short version

Reinforcement learning in a real home is hard for reasons that are not about model
architecture: a house produces about one episode per day, exploration is unacceptable
in someone's living room, and there is no reward signal in the box. So the design
avoids the naive framing.

- **Learn offline and in simulation**, not by trial and error in the house. A digital
  twin — fitted thermal, occupancy and behaviour models — is both the training
  environment and the visualisation.
- **Three tiers matched to what actually works**: contextual bandits for immediate
  reversible decisions (lights, fans, blinds), simulation-trained sequential RL for
  thermal and energy scheduling, and plain supervised prediction for everything else.
- **Reinforcement is automatic — no human ever has to rate anything.** Predictions are
  rewarded or punished by whether the predicted action actually happened in the house.
  When the system acts and the occupant immediately undoes it, that is a free, dense,
  honest negative signal, and harvesting it properly is where most of the value lives.
- **Automations are filtered out of the reward.** A prediction "confirmed" by an HA
  automation or a Node-RED flow teaches nothing and can ratchet into a self-reinforcing
  runaway, so every reward event is provenance-checked first — and decision points are
  never created for entities a rule already governs.
- **Autonomy is earned per decision point**, walking a ladder from observe → suggest →
  act-with-undo → autonomous, behind a safety governor with hard constraints, a kill
  switch, and a deadman that hands control back to Home Assistant if the service dies.
- **Baselines are always on screen.** Do-nothing, existing automations, and behaviour
  cloning are first-class policies in every comparison. If the RL does not beat them,
  the dashboard says so.

## License

TBD.
