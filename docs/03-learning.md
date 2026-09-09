# Learning design

This is the part of the project most likely to fail, so it gets the most attention up front.

## Why the obvious framing does not work

The intuitive design — "one RL agent, observation = all entity states, action space =
all services, reward = user happiness" — fails for four independent reasons. Each one
alone is fatal.

1. **Sample efficiency.** A house generates roughly one episode per day and a few
   thousand meaningful transitions. PPO/DQN on a problem this size needs 10^5–10^7
   transitions. Learning online in the real home would take decades.
2. **Exploration is unacceptable.** An agent that tries actions to see what happens is
   an agent that turns the lights off during dinner. In a home, exploration is not a
   hyperparameter, it is a product defect.
3. **The reward does not exist.** Comfort, cost, and annoyance trade off against each
   other, with no ground truth and per-person weights. A misspecified reward here does
   not converge to something mediocre, it converges to something actively hostile.
4. **Non-stationarity.** Occupants change habits, seasons swing the thermal problem,
   devices come and go. A policy trained on last winter is wrong by spring.

## The reformulation

Three tiers, matched to what each technique is actually good at. Control is over a
curated set of **decision points**, never raw entity access.

### Tier A — Contextual bandits (ships first)

Discrete, immediate-consequence, reversible decisions:

- Should this light be on right now, and at what brightness?
- Should the extractor fan run?
- Should the blinds close?
- Should this room's speaker follow the person into it?

Context: time features, inferred occupancy, illuminance, recent activity, who is home.
Reward is measured over a short window (seconds to minutes) so credit assignment is
nearly trivial. LinUCB or Thompson sampling, with a neural bandit once there is enough
data.

These converge in **weeks, not decades**, and they are where the project earns its
first real wins. Most household automation genuinely is a bandit problem wearing a
sequential-decision costume.

### Tier B — Sequential RL, trained in simulation

Problems with genuine temporal credit assignment, where doing the right thing now pays
off in two hours:

- HVAC setpoint scheduling, pre-heating and pre-cooling.
- Water heater scheduling against a variable tariff.
- EV charging and home battery dispatch.
- Load shifting against solar generation.

This is exactly the class where building-control RL has published results (BOPTEST,
Sinergym and similar benchmarks). It is tractable **because it can be simulated**: the
thermal RC model plus a tariff and a weather forecast is a real environment, and the
policy can take millions of simulated steps overnight on a CPU.

Training: SAC or PPO in the twin, plus IQL/CQL trained directly on logged real
transitions as a check on the simulator. Deploy through the governor, and keep
comparing against the fitted schedule baseline forever.

### Tier C — Supervised prediction, not RL

- Occupancy and arrival prediction.
- Next-action prediction (behaviour cloning of the occupants).
- Thermal and illuminance forecasting.

These are plain supervised problems. They feed Tiers A and B as features, they power
the twin, and they provide the baseline that any RL policy has to beat. **If behaviour
cloning beats the RL policy, ship the behaviour clone.** The dashboard is built to make
that outcome visible rather than embarrassing.

## The reward function

Composed per decision point, weights editable by the user in the UI:

```
r = + w_agree     * agreement            # human did what we predicted (observe mode)
    - w_override  * override_penalty     # human reverted our action within T
    - w_energy    * (kWh * tariff)
    - w_comfort   * comfort_violation    # outside band while occupied
    - w_churn     * actuation_count      # relay wear, noise, annoyance
    + w_feedback  * explicit_feedback    # OPTIONAL, off by default
```

### Automated reinforcement is the default

The system learns without anyone ever pressing a thumbs-up. Every term above except
the last is derived automatically from what the house actually did, which means
reinforcement runs continuously from first install rather than only when a human
bothers to rate something.

This works only because every candidate reward event is filtered by **provenance** —
who or what caused it. A prediction "confirmed" by an HA automation or a Node-RED flow
is worthless, and counting it creates a self-reinforcing loop that can genuinely run
away. That subsystem is substantial enough to have its own document:
**[05-provenance.md](05-provenance.md)**, which also covers why context-based
attribution alone is insufficient, and why decision points are never created for
entities a rule already governs.

Explicit human feedback remains available as an optional accelerator, off by default.

### The override signal is the crown jewel

If the system turns a light on and the occupant turns it off 40 seconds later, that is
an unambiguous, unprompted, free negative reward — and houses generate them all day.
It is the densest honest signal available, and most of the value of this project comes
from harvesting it properly.

It also has to be attributed carefully. A reversal counts as an override only if it
lands on the same entity (or an entity in the same area), inside an attribution window,
and was not itself triggered by an existing HA automation. Getting attribution wrong
poisons every downstream model, so the attribution logic gets its own tests and its own
panel in the UI for manual inspection.

### Guarding against reward hacking

The obvious degenerate solution to "minimise overrides" is **do nothing forever**. The
do-nothing baseline is therefore always in the comparison set, and a policy that merely
matches it is reported as no better than nothing. Comfort and energy terms are what
make action worthwhile; the churn term is what stops it thrashing.

## Log propensities from day one

**This is the decision that is expensive to retrofit.** Offline policy evaluation
(importance sampling, doubly-robust estimators) requires knowing the probability with
which the logging policy chose each action. If shadow mode logs only deterministic
argmax proposals, those logs can never support unbiased offline evaluation, and every
model promotion becomes guesswork.

So from the first day of shadow mode, proposals are sampled stochastically and the
propensity is written into the decision log — even while nothing is being actuated.
Effective sample size is reported alongside every offline estimate, honestly, because a
near-deterministic logging policy makes those estimates nearly worthless and the UI
should say so rather than print a confident number.

## The autonomy ladder

Every decision point walks this ladder independently, and can be demoted automatically:

| Level | Behaviour |
|---|---|
| `off` | Not evaluated. |
| `observe` | Proposals computed and logged with propensities. Nothing acts. Agreement with the human is measured. |
| `suggest` | Actionable HA notification with accept/reject. Accept executes; both answers are strong labels. |
| `act-with-undo` | Acts, then posts a one-tap undo. An undo is a maximal-weight negative. |
| `autonomous` | Acts silently. Still logged, still reversible, still measured. |

Promotion is earned, per decision point, on evidence: sustained agreement in `observe`,
then an acceptance rate above threshold in `suggest`, then a falling undo rate. A
regression demotes automatically.

## Evaluation

Nothing is trusted on training curves alone.

- **Offline policy evaluation** on logged propensities, with effective sample size.
- **Shadow agreement**: how often the proposal matches what the human actually did.
- **Twin fidelity**: predicted vs actual per zone. Target under ~0.5 °C RMSE over a
  6-hour horizon before any Tier B policy is trusted.
- **A/B in the real house**: alternate days between policy-on and baseline, compare
  reward components with confidence intervals. Slow, but the only ground truth.
- **Override rate over time** — the headline number, and the one the whole system is
  ultimately judged on.

## Explainability, because it is also the debugger

Each decision stores enough to reconstruct itself: which features mattered (attribution
over the observation vector), the predicted value of acting versus not acting, the
policy's forecast trajectory, and the actual outcome. The twin view replays this with a
time scrubber, so a bad decision can be inspected as "here is what it believed, here is
what it expected, here is what happened."

This is a user-trust feature, but it is mostly a development tool. Without it, a policy
that behaves oddly at 6 a.m. is essentially undebuggable.
