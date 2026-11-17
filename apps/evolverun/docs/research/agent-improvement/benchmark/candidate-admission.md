---
status: research-draft
source_date: 2026-11-02
prepared_on: 2026-11-17
---

# Candidate production quality and benchmark admission

## Problem and scope

A generation batch can pass every structural check and still contain questions
that are ambiguous, unanswerable under the Actor's permissions, incorrectly
scored, or unsuitable for independent acceptance. Producing a valid artifact,
reviewing its meaning, running an Actor and Judge, and admitting a benchmark
version are different outcomes.

This note proposes an evidence-based admission review. It is not an implemented
admission service, a new question bank, a training authorization, or a claim of
measured production quality. The Bot Genome and platform-owned verification and
promotion boundaries in [ADR 0019](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md)
and [ADR 0020](../../../../../../docs/adr/0020-promotion-is-platform-owned.md)
remain authoritative.

## Historical evidence and its limits

The source is a frozen candidate-production record dated **2026-11-02**. Its
summary, structural-validation record, and production-policy record were read
and their pinned hashes verified while preparing this note. The original run
was not repeated, and this note does not characterize a later batch or a running
service. No candidate questions, answer keys, source excerpts, private paths,
platform identities, or execution logs are included here.

The record distinguishes the following facts:

| Recorded evidence | What it supports | What it does not establish |
| --- | --- | --- |
| Valid schemas, score totals, and preserved historical prefix | Structural consistency and append-only preservation for the checked artifacts | Correct answers, useful coverage, or semantic independence |
| Pinned source and synthetic-fixture verification | Traceability to the recorded input versions; fixtures are explicitly synthetic | Deployed behavior, realistic frequencies, or current platform state |
| No answer leakage found by the recorded projection check | That check found no leakage in its declared scope | Complete access isolation or immunity to derived-answer leakage |
| Human review pending; no Actor or Judge runs recorded | Semantic and execution acceptance had not been demonstrated | That the candidates failed an execution test |
| Training disallowed; holdout purpose not allocated | The batch had no training authorization or independent-acceptance assignment | An enforced ACL or a deployed training gate |

Structural success therefore leaves admission unresolved. Missing execution
records are **not run**, not failed and not accepted. A local file split and a
purpose flag are policy metadata, not proof of deployed access enforcement.

## Admission evidence layers

The following layers are proposed review checkpoints, not new runtime status
values. An owning contract must define transitions before automation uses them.

| Layer | Required evidence | Decision boundary |
| --- | --- | --- |
| Produced | Immutable batch identity, source references, generation policy, attempt and stop accounting | A candidate exists; it has not been accepted |
| Structurally validated | Schema, required fields, score totals, reference integrity, stable IDs, projection checks | The checked representation is coherent |
| Semantically reviewed | Question clarity, supported answer boundary, rubric correctness, feasible Actor route, duplicate review | A reviewer approves meaning for a declared use |
| Execution validated | Version-bound Actor/Judge trials, environment evidence, grading calibration and unresolved-issue accounting | Observed execution supports the declared claim |
| Admitted | Reviewed manifest, intended use and split, approval identity, applicable gates and immutable release identity | The owning benchmark authority accepts that version for that use |

Do not collapse these layers into one `passed` flag. A rejected item retains its
source identity and review reason. An interrupted or unavailable trial remains
unresolved; it must not be counted as an Actor failure or a successful exam.

## Proposed per-candidate review

1. **Question and answer boundary.** Confirm the question names a meaningful
   task, the expected answer follows from the cited source version, and the
   rubric does not require facts absent from that evidence. Conflicting sources
   go to review; do not invent a reference answer to finish a batch.
2. **Reachability.** Check that the declared Actor input, tools, permissions and
   environment permit a valid solution. If recognizing insufficient evidence is
   the intended skill, say so explicitly rather than demanding a hidden fact.
3. **Scoring.** Check outcome and process criteria independently. Weights summing
   to a fixed total do not prove that the criteria are correct or calibrated.
   Accept alternative valid routes when they meet the same evidence boundary.
4. **Novelty and coverage.** Check exact identity, retrieve likely near-duplicates
   across the approved baseline, and review semantic overlap. Balanced category
   counts do not establish realistic task distribution or independent cases.
5. **Difficulty.** Treat the initial band as a hypothesis; calibrate against
   representative questions and the stated environment, not question length or
   a single failed run. Keep environment failure separate from difficulty.
6. **Projection and lineage.** Verify Actor/Judge separation, protected derived
   artifacts, source versions, and intended data use. Redaction or rewriting
   alone does not make protected material training-eligible.

A partial review must list reviewed, rejected, unresolved and unreviewed items
separately. Batch approval cannot silently cover the remainder. Sampling may
support a declared estimate, but does not certify every unreviewed question;
record the selection method, sample size and uncertainty with that estimate.

## Execution evidence and grading quality

An execution trial should bind the question revision, source snapshot, Actor
and Judge versions, harness, tools, environment and applicable policies. Keep
model output, tool observations and grading evidence attributable to that run.
A summary score without these identities is not reproducible acceptance.

Before broad use, reviewers should examine a small, representative calibration
set with correct, incorrect, partially correct and alternative-route outputs.
Disagreement between human review and a Judge is a calibration issue to resolve,
not a reason to rewrite reference answers to match the Judge. Record repeatability
and disagreement limits in the owning evaluation specification; this note does
not invent numerical pass thresholds or claim that calibration has happened.

Trials must use an approved safe environment and explicit side-effect policy.
Do not execute a live business operation merely to make a question answerable.
A forbidden write, unavailable fixture or insufficient permission is an explicit
blocker with its own evidence, not a reason to bypass the environment boundary.

## Use assignment, independence and training prohibition

Before admission, an owner assigns a declared purpose: development evaluation
or independent acceptance. A candidate pool is neither by default. Partition by
source/task family and relevant near-duplicate lineage, not only randomized
rows. Keep related sessions, rewritten cases and answer-derived material out of
both sides when they would disclose the acceptance task.

Development feedback may guide a candidate change. Independent-acceptance
questions, answers, detailed traces and Judge explanations remain protected from
that improvement loop. Restricted verdicts do not authorize repeated probing
until the holdout is learned. See [evaluation-data boundaries](../rsi/evaluation-data-boundaries.md)
and [question metadata projections](../benchmark-question-meta.md).

Benchmark admission does not imply training admission. Training remains
prohibited for the historical source batch; changing that policy would require
an independently authorized purpose/lineage review and real enforcement at the
training input boundary. This document grants neither permission nor access.

## Admission manifest and failure handling

A proposed admission manifest should identify:

- the batch and candidate revisions, immutable content hashes and source policy;
- the reviewed candidate list and excluded/unresolved items with reasons;
- semantic-review decisions and the accountable reviewer;
- Actor/Judge execution identities, calibration evidence and known limitations;
- the declared purpose, split assignment and cross-split overlap review;
- the access-enforcement evidence and current training prohibition; and
- the admission authority, decision time and accepted benchmark version.

The manifest references protected evidence rather than publishing answer keys.
A permission label or completed generation job cannot stand in for any missing
approval. On unknown publication outcome, query the original release identity;
do not submit another publication or mark it accepted speculatively.

## Adoption gap and next review

The historical record supports structural validation but not completed semantic
review, Actor/Judge validation, deployed access isolation or formal split
assignment. Those gaps remain explicit. Preparing or merging this note does not
close them or make any candidate benchmark-ready.

The next bounded step is an owner-reviewed calibration slice with synthetic,
non-sensitive fixtures, declared purpose and evidence requirements. Only after
review and controlled trials should an owning specification define admission
gates and implementation work. Related research notes cover [environment reproducibility](environment-reproducibility.md),
[taxonomy accounting](taxonomy-versioning.md), [outcome/process assessment](outcome-process-assessment.md),
and [difficulty calibration](difficulty-calibration.md). They remain proposals,
not proof that their gates have been implemented or passed.
