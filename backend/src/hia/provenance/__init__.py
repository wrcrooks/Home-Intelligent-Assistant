"""The provenance classifier (P3, docs/05-provenance.md and docs/04-roadmap.md).

Answers one question for a given state change: was this caused by a machine
(an automation, a script, or a bare service call), or not? That's the question
automated reinforcement needs answered correctly before it can trust any reward
signal at all (CLAUDE.md non-negotiable #3: "Machine-caused events never generate
reward").

This slice builds Layer 1 (:mod:`hia.provenance.chain`) and Layer 2
(:mod:`hia.provenance.correlate`) of the three-layer classifier in
docs/05-provenance.md §4. Layer 3 (per-actor classification — human vs. voice
bridge vs. service account, and the setup UI for tagging HA users) is deliberately
not built yet; see :mod:`hia.provenance.classify` for what that means for this
slice's output.
"""
