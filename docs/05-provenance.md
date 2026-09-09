# Provenance and automated reinforcement

**Automated reinforcement** lets the system learn without any human ever pressing a
thumbs-up. A prediction is rewarded or punished purely by whether the predicted action
actually happened in the real house.

That only works if the system can answer one question reliably: **who did this?** A
prediction confirmed by a Home Assistant automation or a Node-RED flow is worthless —
it is the system congratulating itself for predicting a rule that is sitting in a YAML
file it could simply read. Every reward therefore passes through a provenance filter
first.

---

## 1. The loop we are preventing

Suppose an automation turns on `light.porch` at sunset.

- The system observes the pattern and predicts "porch light on at sunset."
- The automation fires. The prediction "comes true." The system is rewarded.
- It scores near-100% on that decision point and learns nothing — the reward measures
  the automation's determinism, not the model's usefulness.

It gets worse on promotion. Once the system is allowed to act on `light.porch`, it
acts at sunset, the automation also acts, and the automation's action reads as
*confirmation* of the system's own action. If the system drifts slightly early, it is
confirmed slightly early, and the estimate ratchets earlier every day. This is a
genuine runaway, not a theoretical one, and it is created entirely by counting
machine-caused events as evidence.

Two independent mechanisms block it:

- **Reward gating** (§3–5) — automation-caused events never generate reward.
- **Admission control** (§6) — decision points are not even created for entities that
  are already governed by a rule.

---

## 2. The provenance taxonomy

| Class | Examples | Evidence? |
|---|---|---|
| `human_physical` | Wall switch, dimmer, physical button | **Yes** — strongest |
| `human_ui` | HA app, web dashboard, tablet | **Yes** |
| `human_voice` | Assist, Alexa, Google via bridge accounts | **Yes** |
| `automation_ha` | Automations, scripts, scenes, blueprints | **No** |
| `automation_external` | Node-RED, AppDaemon, n8n, custom scripts | **No** |
| `device_local` | Zigbee binding, thermostat's own schedule, vacuum docking | **No** |
| `hia_self` | Our own actions | **No** — tracked as outcome, never as evidence |
| `unknown` | Unclassifiable | **No** — abstain |

Two rules govern this table.

**Abstention is not a zero reward.** An unclassifiable event produces *no training
signal at all*. Recording it as reward-zero would be a false statement about the world;
dropping it is merely a smaller dataset. When in doubt, drop.

**`hia_self` is the most obvious self-referential loop and the easiest to miss.** The
system's own actions carry a context it issued and can always recognise, so this one is
exact.

---

## 3. Why context alone is not enough

Home Assistant attaches a `Context` to every state change, carrying `id`, `user_id` and
`parent_id`. When an automation runs, the context normally flows into the `parent_id`
of everything it causes, which is exactly the attribution chain we want.

**But there is a hole, and it sits directly under the most common automations in any
house.** Sun and Time-of-Day triggers do not set a context. An automation triggered at
sunset produces state changes with `parent_id = None` and `user_id = None` — which is
byte-for-byte the signature of someone physically flipping a wall switch.

This is the worst possible collision. Time- and sun-triggered automations are the most
common kind of home automation in existence, and a physical switch press is the single
most valuable human signal available. Naive context-based classification would
systematically mistake the former for the latter, and would preferentially corrupt the
very decision points we most want to learn.

Node-RED punches a second hole. It talks to HA over the WebSocket API using a
long-lived token belonging to a *user account*, so its service calls arrive carrying a
`user_id` — making external automation look like deliberate human action. The naive
test "has a `user_id`, therefore a human did it" is exactly backwards here.

So context is the fast path, never the only path.

---

## 4. Three-layer classifier

Evaluated in order; first confident answer wins. Each layer emits a confidence, and
only classifications above threshold generate reward.

### Layer 1 — Context chain (exact when present)

Resolve each event's context to a root by walking `parent_id`, depth-capped and
cycle-guarded, with a TTL cache of `context_id → origin`. Live subscriptions to
`automation_triggered`, `script_started` and `call_service` populate the map, so any
context rooted in an automation is identified exactly.

Covers: automations with entity/event/device triggers, UI actions, our own actions.
Misses: anything sun- or time-triggered.

### Layer 2 — Automation-fire correlation (closes the sun/time hole)

The critical observation: even when a trigger sets no context, **the
`automation_triggered` event still fires on the bus** with the automation's entity_id.

So the system reads every automation's configuration and extracts its *action targets*
— the entities it is capable of touching. Then: if automation `X` fires at time `T`,
and entity `L` is in `targets(X)`, and `L` changes within a short window after `T`, the
change is attributed to `X` regardless of what its context says.

Node-RED gets the same treatment through its admin HTTP API. Pointing the system at a
Node-RED instance lets it enumerate `api-call-service` nodes and recover the same
target sets. Where the API is unavailable, the affected entities can be listed manually.

This layer is what makes automated reinforcement trustworthy in a real house. Without
it, sun-triggered automations quietly poison the reward.

### Layer 3 — Actor classification (catches external automation)

Every observed `user_id` is classified once as human, voice bridge, or service account,
and the mapping is stored. The UI lists all HA users alongside any user IDs seen in the
event stream and asks the owner to tag them — a two-minute setup task, done at install.

Auto-suggestions come from HA's `system_generated` flag, name matching
(`node-red`, `appdaemon`, `homekit`, `alexa`, `google`, `n8n`), and a behavioural test:

> **Temporal regularity.** Compute the entropy of an actor's inter-event intervals and
> time-of-day distribution. Automations are sharply peaked — the same action at
> 17:30:00 ± 0.4 s, every day, including at 04:00. Humans are diffuse and sleep.
> Low-entropy actors are flagged for review.

The regularity test is a *suggestion*, never an automatic classification. It is a
heuristic about behaviour, and a shift worker's routine can look mechanical.

### Falling through

An event that no layer classifies confidently is `unknown` and produces no signal.
`device_local` cannot be detected automatically at all — a Zigbee-bound bulb is
invisible to HA's context system — so it is a per-entity configuration list, surfaced
in the UI whenever an entity shows frequent unexplained changes.

---

## 5. Turning filtered events into reward

With provenance settled, prediction outcomes map to reward:

| Prediction | Human (provenance-verified) did | Signal |
|---|---|---|
| Action `A` | `A`, within window `W` | **Strong positive**, scaled by temporal proximity |
| Action `A` | Conflicting action (`¬A`) | **Strong negative** |
| Action `A` | Nothing | **Weak negative**, low weight |
| No action | Took action | **Negative** — missed |
| No action | Nothing | **Weak positive** — heavily subsampled |

Three details decide whether this works.

**Absence of action is weak evidence.** If the system predicts "turn the light on" and
nobody does, that may mean the prediction was wrong — or that nobody was in the room,
or nobody cared enough to get up. Weighting this like a real rejection teaches the
system that action is generally punished, and the converged policy is paralysis. It
gets a low, separately configurable weight.

**Nothing-happened dominates everything.** In a house, ~99.9% of timesteps contain no
action at all. Left unbalanced, the true-negative class swamps the gradient and every
model collapses to "predict nothing." The bottom row is aggressively subsampled and
class weights are set explicitly.

**The distribution is deliberately biased, and that is fine.** Filtering out
automation-caused events means training on the *residual* — the manual behaviour that
existing rules do not cover. That is precisely the behaviour worth learning. But it
means accuracy figures are computed on a filtered subpopulation and cannot be compared
against baselines computed over all events. Every metric in the UI is labelled with the
population it was measured on.

---

## 6. Admission control: do not predict what a rule already does

Reward gating stops bad learning. Admission control stops pointless learning.

For each candidate entity, over a trailing 30-day window, compute
`automation_share` — the fraction of its state changes with machine provenance:

| Share | Treatment |
|---|---|
| **> 0.8** | Excluded by default. Flagged *"already automated"*, listing the specific automations or flows responsible. Opt-in override available. |
| **0.3 – 0.8** | Allowed, with a warning that effective sample size is reduced. |
| **< 0.3** | Normal. |

Because the system parses automation configs anyway (§4), it can be more precise than a
blanket exclusion: it knows the *conditions* under which a rule fires, so it can
exclude only the tautological region — "porch light at sunset is covered, porch light
at 02:00 is not" — rather than abandoning the entity entirely. Whole-entity exclusion
ships first; conditional exclusion is a refinement.

**Collision detection.** If the system is promoted on an entity that an automation also
targets, that is flagged as a conflict and the system defers to the automation. Two
controllers fighting over one relay is a bad outcome even when both are individually
correct.

### The headline metric

Every decision point reports **effective human events per week** — the count surviving
provenance filtering. This is the number that determines whether a decision point can
learn anything at all. Below a floor (~5/week), it is disabled with an explicit
message: *not enough human signal to learn from*. Silently producing confident-looking
noise from four data points is the failure mode this metric exists to prevent.

---

## 7. Capture provenance from the first event

**This is the third thing in the project that cannot be retrofitted**, alongside
starting ingestion early and logging propensities.

Context must be recorded on every ingested state change from day one. It cannot be
reconstructed later.

Backfill is only a partial substitute. The recorder's `states` table does carry
`context_id_bin`, `context_user_id_bin` and `context_parent_id_bin` (added in schema
version 36), so historical rows retain some attribution. But `automation_triggered`
events are not reliably retained, so **Layer 2 correlation cannot be reconstructed from
history** — and Layer 2 is exactly what covers sun- and time-triggered automations.

The practical consequence:

- **Historical data** → good enough for supervised prediction and thermal fitting.
- **Live-captured data only** → trustworthy enough for automated reinforcement.

The automated reward loop starts accumulating usable data the day live ingestion
starts, and not one second earlier.

---

## 8. Where human feedback still fits

Automated reinforcement is the default and the system reaches full autonomy without a
single manual rating. The thumbs-up/down control and the accept/reject notification
buttons remain, but they are **optional accelerators, off by default** — high-quality,
low-volume labels useful for breaking ties on ambiguous decision points and for
bootstrapping a new one faster than passive observation allows.

This also unifies the reward design across the autonomy ladder. Automated
reinforcement is not a separate mechanism from the override signal — it is the same
signal at a different rung:

| Rung | Automated signal |
|---|---|
| `observe` | Did the human do what we predicted? (agreement) |
| `suggest` | Accepted or rejected? |
| `act-with-undo` | Was it undone? |
| `autonomous` | Was it overridden? |

One provenance-filtered question — *did a human endorse or reverse this?* — asked
consistently from first install to full autonomy.
