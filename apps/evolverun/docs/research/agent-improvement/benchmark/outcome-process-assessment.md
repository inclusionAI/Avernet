---
status: research-draft
source_date: 2026-10-21
prepared_on: 2026-11-12
---

# Evidence for outcome and process assessment

## Problem and scope

An answer can be correct by chance, while a careful investigation can end with
an unresolved environment failure. A single impression-based score hides these
differences. Counting tool calls also rewards activity rather than supported
work, and a missing trace can look like a missing action.

This note consolidates an October 21 discussion about observable assessment
evidence. It proposes distinctions and review questions, not a scoring formula,
an implemented grader, an adopted schema, a release gate or measured evaluation
quality. Weights, numeric bands and the relationship between process correctness
and efficiency remain open. The existing [question metadata
boundary](../benchmark-question-meta.md) is unchanged.

## Evidence and roles

The Actor attempts the task. A separate Judge assesses the recorded attempt
against the question's reviewed facts and rubric. The Judge does not execute the
Actor's task, invent missing observations or substitute personal preferences for
verified domain rules.

| Material | What it can establish | What it cannot establish alone |
| --- | --- | --- |
| User request and conversation context | Requested result and information already supplied | A verified root cause or permission to act outside the request |
| Reviewed facts, reference answer and rubric | Applicable constraints and evidence conditions | Truth merely because an answer was written in a dataset |
| Actor messages, tool actions and results | Observable work, responses and subsequent decisions | Hidden reasoning or effects absent from the available record |
| Target-state or acceptance evidence, when applicable | Whether an external action achieved the requested state | General capability or correctness on other tasks |

Reference answers need provenance and review too. A conflict between a reference
answer and reliable evidence should produce a reviewable discrepancy, not a
quiet replacement of the reference or a fabricated Actor error.

Judge-only facts and reference material remain outside the Actor projection.
Follow the [environment evidence boundary](environment-reproducibility.md) and
[data isolation note](../rsi/evaluation-data-boundaries.md); assessment evidence
must not become an answer channel or a source of held-out-case leakage.

## Two assessment dimensions

**Outcome** asks whether the final response is supported by facts, addresses the
user's request and states necessary limitations. Confidence, fluency, formatting
and answer length are not substitutes for factual support. A task involving a
state change additionally needs appropriate observable result evidence; a
successful command or request does not by itself establish that result.

**Process** asks whether the observable attempt completed the task's necessary
evidence gathering, clarification, analysis and decisions. A final statement
such as "I checked the logs" does not establish a check. A tool call establishes
an attempted action, while its target, scope, response and use in the next step
still need assessment.

Neither dimension determines the other automatically:

| Hypothetical attempt | Outcome question | Process question |
| --- | --- | --- |
| Correct conclusion without required investigation | Does reviewed evidence support the conclusion? | Were necessary evidence conditions met, or was the answer a guess? |
| Relevant investigation, unresolved tool failure | Is uncertainty stated instead of claiming success? | Was the failure handled appropriately within authorization? |
| Many successful requests against the wrong object | Does the final answer concern the requested object? | Were identity and query scope verified? |
| One request yields sufficient evidence | Does the answer follow from that evidence? | Is any additional action actually necessary? |
| Partial work with an incomplete trace | What result can be established? | Which conditions remain unassessable rather than disproved? |

These are synthetic discussion examples, not observed evaluation results. They
do not assign scores or make every task require logs, tools or a state change.
A factual question whose sufficient evidence is already in context need not
acquire redundant tool evidence solely to satisfy a preferred reference path.

## Evaluate evidence conditions, not one mandatory route

A process reference can describe the key conditions required for a defensible
answer: correct object, relevant facts, supported interpretation, conflicts
addressed and missing information acknowledged. Multiple routes may satisfy
those conditions. A direct query, a sequence of narrowing queries or a needed
clarification can all be valid under their stated applicability conditions.

Equivalent outcomes do not excuse missing prerequisites. If a later action
requires a verified identifier, a permission grant or an earlier observation,
that dependency and its order matter. Assessment should distinguish these real
prerequisites from an author's incidental tool order. A route outside the
reference needs enough evidence to establish equivalent conditions, not an
unreviewed exception or automatic extra credit for novelty.

For a hypothetical "resource is not responding" investigation, confirming the
correct resource, finding a relevant failing stage and obtaining evidence for a
specific cause are different achievements. They are not a required three-step
ladder for every task, and finding a failing stage need not prove the root cause.

## Partial completion is not missing evidence

A proposed assessment record should preserve each required condition, its
applicability, the relevant record location and the Judge's bounded conclusion.
These are review concepts, not new mandatory metadata fields.

| Evidence state | Meaning | Avoid |
| --- | --- | --- |
| Satisfied | Available evidence supports the applicable condition | Treating a self-report as its own verification |
| Partly satisfied | Observable useful work falls short of the full condition | Arbitrary numeric credit without a reviewed rubric |
| Not satisfied | Sufficient records establish that required work was absent or invalid | Assuming absence from a known-incomplete trace proves non-execution |
| Unassessable | Missing or ambiguous records prevent a conclusion | Silently converting observation failure into zero ability |
| Not applicable | The reviewed task conditions do not require this work | Dropping a difficult applicable condition to improve a score |

Do not normalize a summary only over assessable successes. Report applicable,
assessable and unassessable populations separately, with the trace limitations.
The final policy for whether an incomplete attempt is eligible for numeric
aggregation remains to be reviewed. Observation completeness and Actor quality
are distinct facts; neither should erase the other.

A judgment should point to the action/result or response that supports it and
state the relevant rule. A tool failure is not automatically an Actor failure:
review whether the request was valid, whether prerequisites were available and
how the Actor responded. Conversely, an environment error does not excuse an
unsupported success claim or an unauthorized retry.

## Correctness and efficiency remain separate questions

The source discussion tentatively prioritizes making evidence-based process
correctness stable before deciding how efficiency affects assessment. It does
not settle that decision. Repeated unnecessary queries may be inefficient, but
more calls are not inherently better or worse than one sufficient query.

A later efficiency proposal would need comparable task conditions and explicit
resource accounting. This note does not introduce a call-count penalty, latency
budget, token reward, efficiency weight or permission to skip necessary checks.

## Proposed calibration and acceptance work

Before adopting a grader, domain owners would need to review examples and
counterexamples for each evidence condition. Candidate checks include:

1. Repeat assessment on the same pinned attempt and rubric, tracking material
   disagreements rather than claiming repeatability without measurements.
2. Compare distinct valid routes that satisfy the same evidence conditions;
   check that incidental sequence and verbosity do not determine the judgment.
3. Contrast full records proving non-execution with truncated records that leave
   execution unknown. Check that missing observations do not become false facts.
4. Review a correct guess, a wrong-object query, a relevant tool failure and a
   partial investigation. Explain outcome and process judgments independently.
5. Trace every judgment to available evidence and the applicable rubric; route
   reference conflicts and uncertain judgments to review.

Version-bind the question, rubric, Actor attempt, evidence completeness and
Judge configuration when comparing results. This is a proposal for future
calibration, not a claim that these tests ran or that human agreement is known.
No numeric thresholds, sample size or acceptance target are established here.

## Architecture and decision boundary

Assessment evidence can inform the existing evaluation flow; it does not create
a parallel experiment ledger, Genome or delivery path. [Bot Genome remains the
unit of evolution](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md),
and [promotion and verification remain
platform-owned](../../../../../../docs/adr/0020-promotion-is-platform-owned.md).
A favorable research assessment cannot move an active ref, relax a platform
floor or expose verification cases to an improvement strategy.

## Source and remaining decisions

Source: a process/outcome assessment discussion dated 2026-10-21, read again on
2026-11-12. SHA-256:
`52fb1dea222db09d6ec74a90065acd9ece6edd80178cb81f7683f42f1b3c4126`.
It is an uncommitted local design snapshot, not an adopted scoring contract.
This public note omits internal identities, endpoints, raw trajectories and
business data; the digest identifies the design input, not execution evidence.

Still unresolved: how correctness and efficiency interact, partial-credit bands,
when numeric aggregation is allowed, environment-failure treatment, alternative
route admission, reference-error arbitration and independent calibration.
No model evaluation, production or business acceptance was performed for this
note. Implementation would require its own reviewed contract and tests.
