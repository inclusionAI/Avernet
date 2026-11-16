---
status: research-draft
source_date: 2026-11-02
prepared_on: 2026-11-04
---

# Mapping an outer improvement loop to the existing Bot evolution architecture

## Problem

An agent improvement effort crosses experience collection, candidate changes,
version binding, execution, evaluation, verification, and promotion. A status
reported by one stage does not prove that the complete loop is closed: a file
write does not prove that a bot loaded the change, a completed run does not
prove that its evidence is complete, and a development score does not prove
independent acceptance.

This note gives maintainers a small, implementation-neutral map for those
responsibilities. It does not introduce a new runtime layer or claim that the
loop is already fully implemented.

## Existing architectural boundary

The existing Bot Genome is the unit of evolution. A candidate revision should
therefore be represented and reviewed through the existing Genome and Manifest
contracts rather than through a parallel “RSI artifact”. The platform owns
verification and promotion; an improvement strategy may propose a candidate,
but it must not move a live reference or bypass the platform gate.

See [ADR 0019](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md) and
[ADR 0020](../../../../../../docs/adr/0020-promotion-is-platform-owned.md).

The term “outer loop” in this note describes coordination between existing
capabilities. It is not a new level in the platform and should not be treated
as a replacement for the accepted Genome or promotion terminology.

## Proposed evidence flow

```text
experience and feedback
        |
        v
improvement objective, candidate strategy, and budget
        |
        v
candidate patch or approved model/configuration change
        |
        v
pinned bot revision, model, environment, and evaluation set
        |
        v
observable execution and evidence-complete scoring
        |
        v
comparison, decision, and platform-owned verification/promotion
```

Each transition should preserve enough provenance to answer:

- what objective was being improved;
- which candidate and base revision were used;
- which model, configuration, environment, and evaluation-set versions were
  bound;
- which execution actually ran;
- whether the observed trajectory and score evidence were complete; and
- who or what made the continue, stop, verify, or promote decision.

## Two candidate routes

There are two useful routes, but neither is mandatory for every improvement:

1. **Configuration or skill change:** keep the model fixed, submit a typed
   candidate patch, and compare the resulting revision under the same evaluation
   contract.
2. **Model change:** keep the bot configuration and evaluation contract fixed,
   bind the candidate model explicitly, and compare it against the pinned
   baseline.

When both configuration and model change, the binding must make the combination
explicit. Otherwise an observed difference cannot be attributed to either
factor.

## Responsibilities

| Stage | Evidence that should be produced | What it must not claim |
| --- | --- | --- |
| Experience and evidence | source, scope, version, completeness, and use restrictions | historical answers are automatically correct or all logs are training-safe |
| Improvement decision | objective, proposed change, budget, and stop conditions | a strategy may loosen acceptance rules or production permissions |
| Candidate preparation | typed patch or pinned model/configuration reference | writing a file or finishing a training job proves the bot used it |
| Execution and scoring | observable run, evaluation-set version, evidence status, and result | a final answer can replace missing process evidence |
| Verification and promotion | platform verdict, approval, applied revision, and rollback reference | a development-set gain is independent acceptance |

## Minimum comparable run

A first useful comparison should fix a baseline and an approved development set,
then change one clearly stated factor. Record failures, missing evidence, cost,
and regressions as well as successful cases. For example, changing one
retrieval strategy while keeping the model, bot revision, environment, and
evaluation set fixed produces a more interpretable result than changing all of
them together.

A missing trajectory or an unknown publication result is an evidence state to
reconcile, not a success signal. The comparison should stop or remain
inconclusive until the missing state is explained.

## Data and acceptance isolation

Training material, development evaluation, and independent acceptance must be
separate. Restrictions attached to a source must remain attached when the
source is summarized, rewritten, or passed to a grader. A label alone does not
replace an authorization or data-flow check.

Development feedback may support iteration. Independent acceptance remains
platform-owned and should expose only the feedback permitted by its verification
profile; strategies must not receive holdout answers or use repeated probes to
infer the acceptance set.

## Open questions

1. Which existing records provide the revision, model, environment, and
   execution associations required above?
2. How does each evaluator represent incomplete trajectories, partial success,
   and an unknown publication result?
3. Which contract binds a candidate model or configuration to a reproducible
   environment?
4. What is the rollback reference for a candidate that passes development
   evaluation but fails independent verification?

## Compatibility and risk

This document is research-only. It does not change service APIs, Genome
schemas, evaluation gates, promotion permissions, runtime configuration, or
production data. Implementations should be proposed separately with the
appropriate contract and conformance changes.
