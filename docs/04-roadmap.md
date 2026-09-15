# Roadmap

Twelve phases. Each has an **exit criterion** that is a measurement, not a feeling.
Durations assume one developer working part-time; wall-clock is dominated by the phases
where you have to *wait for the house to generate data*, which is why data collection
starts as early as possible.

The ordering has one non-negotiable rule: **ingestion, provenance capture and
shadow-mode logging start before anything clever**, because every model downstream is
limited by how much trustworthy history exists, and history cannot be created
retroactively.

---

### P0 — Foundations · ~2 weeks

Repo scaffolding (`uv`, ruff, mypy, pytest, CI), settings/config, structured logging.
`ha/` client: WebSocket auth, subscribe, reconnect, registry sync. A dev harness that
runs against either a live HA or a throwaway HA container with the demo integration.

**Exit:** `uv run hia watch` streams live state changes from a real HA instance and
survives a Core restart without losing the subscription.

---

### P1 — Ingestion, storage and provenance capture · ~2–3 weeks

Event store (DuckDB + Parquet), schema, retention. Recorder backfill reading
`states` / `states_meta` / `statistics` with schema-version detection. Data quality
report.

Critically, this phase **captures** provenance even though it does not yet interpret
it: every ingested state change records `context.id`, `context.user_id` and
`context.parent_id`, and the client subscribes to `automation_triggered`,
`script_started` and `call_service` alongside `state_changed`. Capture is cheap;
reconstructing it later is impossible.

**Exit:** 30+ days of backfilled history queryable in the same schema as the live
stream; live stream runs 72 hours without a gap; every live state change carries its
context fields and every automation firing is logged.

> Status: ingestion, storage and backfill are built and verified live; the 72-hour
> soak test is the one exit criterion still outstanding. See docs/HANDOFF.md.

> From this point on, **leave it running**. Everything later is bottlenecked on
> accumulated data.

---

### P2 — Web UI shell and observability · ~2 weeks

FastAPI + React under ingress, packaged as an installable add-on. Live entity view,
data quality page, ingestion metrics. Deliberately early: you cannot build feature
engineering for a house you cannot see.

**Exit:** the add-on installs from a local repository on real HA OS hardware, appears
in the sidebar, and shows live state.

> Status: the backend (`hia serve` — ingest + REST + live WebSocket relay, one
> process; see docs/HANDOFF.md for why not a separate reader) is built and verified
> live. The frontend and add-on packaging are not started; the exit criterion needs
> both of those plus real HAOS hardware this project hasn't had access to yet.

---

### P3 — Provenance classifier · ~2–3 weeks

The three-layer classifier from [05-provenance.md](05-provenance.md): context-chain
resolution, automation-fire correlation against parsed HA automation and Node-RED
targets, and per-actor account classification with the setup UI for tagging users.
Plus `automation_share` per entity and the admission-control gate.

Placed here because everything downstream that involves reward depends on it, and
because it is what makes the data being collected since P1 *trustworthy* rather than
merely plentiful.

**Exit:** on a real house, a hand-labelled sample of 200 state changes is classified
with >95% precision on the `human` classes, **including at least 20 changes caused by
sun- or time-triggered automations** — the case that context alone cannot detect.
Unknown rate below 15%.

---

### P4 — Features and twin v0 · ~3 weeks

Per-domain encoders, temporal features, occupancy inference. Area graph from the
registry. 2D floorplan editor with room polygons and entity placement, plus
motion-co-occurrence adjacency inference as the auto-layout fallback.

**Exit:** a versioned observation vector builds reproducibly from history, and the
floorplan for a real home can be drawn in under 15 minutes.

---

### P5 — Predictive baselines · ~3 weeks

Behaviour cloning of occupant actions, occupancy/arrival prediction, per-zone thermal
RC fitting with validation plots. All trained on provenance-filtered human actions.
Concrete model choices, data floors and validation metrics are in
[06-model-training.md](06-model-training.md) §1–2.

**Exit:** BC beats a persistence baseline on next-action prediction, and the thermal
model is under ~0.5 °C RMSE on a 6-hour horizon for the main living zones. Until this
passes, Tier B RL is not worth attempting.

---

### P6 — Governor, shadow mode and automated reinforcement · ~3 weeks

Decision points defined, admitted through the P3 gate. Governor with the autonomy
ladder, hard constraints, kill switch and deadman — built now, before anything can
actuate. Policies emit **stochastic proposals with logged propensities**. Nothing acts.

The automated reward loop closes here: predictions are scored against
provenance-verified human actions, with the weighting and subsampling from
[05-provenance.md](05-provenance.md) §5. Decisions timeline in the UI, with the
optional thumbs control available but off by default.

**Exit:** two weeks of decisions logged with propensities; shadow agreement measured
against real human behaviour; **zero rewards traceable to machine-caused events** in an
audit of the decision log.

---

### P7 — Bandits live · ~3 weeks

Tier A decision points move `observe → suggest → act-with-undo` on a small opt-in entity
set. Override attribution joins prediction agreement in the same reward pipeline.
Actionable notifications via the custom component. Collision detection against
automations that target the same entities. Bandit algorithm, context-vector size and
exploration policy are specified in [06-model-training.md](06-model-training.md) §2.

**Exit:** on at least one decision point, a measurable fall in override rate versus the
do-nothing and existing-automation baselines, over a four-week window.

**This is the first phase that delivers real user value.** If the project stalls here,
it has still produced something worth running.

---

### P8 — Simulation and sequential RL · ~4–6 weeks

`HouseEnv` over the twin. SAC/PPO in simulation, IQL/CQL on logged transitions. Offline
policy evaluation harness. Model registry with promotion gates. Target: HVAC setpoints,
water heater, tariff-aware load shifting. Training loop, compute budget, offline-eval
gate and the sim-vs-offline-RL disagreement check are specified in
[06-model-training.md](06-model-training.md) §2–4.

**Exit:** a sim-trained policy beats the schedule baseline in simulation *and* holds up
in a two-week real A/B — energy down, no comfort regression, no rise in overrides.

---

### P9 — 3D twin and explainability · ~3 weeks

react-three-fiber extrusion of the 2D polygons. Decision replay with a time scrubber,
counterfactual overlay, feature attribution, predicted-vs-actual trajectories. Each
replayed decision shows the provenance of the evidence that rewarded it.

**Exit:** any logged decision from the past 30 days can be replayed and explained.

---

### P10 — Packaging and release · ~2–3 weeks

Add-on repository, multi-arch build (`amd64`, `aarch64`), HACS custom integration,
model backup/restore, schema migrations, install and safety documentation.

**Exit:** a clean install on unfamiliar hardware reaches shadow mode with no manual
intervention beyond entering a token, tagging user accounts, and drawing a floorplan.

---

### P11 — Continuous operation · ongoing

Drift detection and scheduled retraining, automatic demotion on regression, seasonal
adaptation, new-device onboarding, re-running admission control as the household's
automations change.

---

## Critical path

```
P0 ─ P1 ──────────────────────────────────────────────────────► (data accumulating)
       └─ P2 ─ P3 ────────────────────────────────────────────► (data now trustworthy)
                 └─ P4 ─ P5 ─ P6 ──────────► (decisions accumulating)
                                    └─ P7 ─ P8 ─ P9 ─ P10 ─ P11
```

P1, P3 and P6 all start clocks that cannot be shortened later. Everything else is
ordinary engineering.

## Three things that cannot be retrofitted

1. **Ingestion** (P1) — history cannot be created retroactively.
2. **Provenance capture** (P1) — context fields and automation firings must be recorded
   with every event from the first day. Backfill recovers context columns from the
   recorder's `states` table but not the automation firings needed to detect
   sun- and time-triggered rules.
3. **Propensity logging** (P6) — deterministic shadow logs can never support unbiased
   offline evaluation.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Not enough history to learn anything | Fatal | Start ingestion in P1 and never stop; backfill recorder; Tier C supervised models as the fallback deliverable |
| Reward misspecification produces hostile behaviour | Fatal | Autonomy ladder, hard constraints, always-visible baselines, user-editable weights, kill switch |
| **Automation-caused events counted as reward** | **Fatal — self-reinforcing runaway** | **Three-layer provenance filter (P3); admission control on `automation_share`; abstain-on-unknown; P6 exit audit requires zero machine-caused rewards** |
| Sun/time-triggered automations look identical to wall-switch presses | Silently corrupts the best signal | Layer 2 automation-fire correlation; explicitly tested in the P3 exit criterion |
| Node-RED actions carry a `user_id` and look human | Silently corrupts reward | Actor account classification; Node-RED admin API target extraction; temporal regularity flagging |
| Provenance filtering leaves too little signal | Decision points cannot learn | `effective human events per week` reported per decision point; auto-disable below floor with an explicit message |
| Override/agreement attribution is wrong | Poisons every model | Dedicated tests, manual inspection panel, conservative attribution window |
| Twin too inaccurate for Tier B | Blocks sequential RL | P5 exit gate; if it fails, stay on Tier A bandits and ship that |
| Scope creep into "AI house assistant" | Never ships | Out-of-scope list in [01-scope.md](01-scope.md) is binding |
| RL never beats simple baselines | Embarrassing but survivable | Dashboard reports it honestly; behaviour cloning and bandits still deliver value |
| Add-on resource limits on low-end hardware | Poor UX | amd64/aarch64 only, CPU-only models, training off the critical path, documented minimum spec |

## Deliberately deferred

Voice and LLM interfaces, camera/CV, cloud training, cross-household federated
learning, and any actuation of locks, alarms, garage doors, water shutoff, or medical
equipment.
