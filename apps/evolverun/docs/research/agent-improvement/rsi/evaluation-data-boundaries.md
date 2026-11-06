---
status: research-draft
source_date: 2026-11-02
prepared_on: 2026-11-06
---

# Separating training, development evaluation, and independent acceptance data

## Problem

An improvement loop needs feedback to choose the next change, but the
feedback used to develop a candidate must not silently become training data or
an independent acceptance answer key. A final score is not enough evidence of
that separation: the same question, trajectory, answer, summary, or rewritten
excerpt can cross a boundary even when the original file has been removed.

This note defines a reviewable data-flow boundary for the existing improvement
and evaluation architecture. It is a research proposal, not an assertion that
the boundary is already enforced by every current service.

## Existing ownership and evidence limits

The Bot Genome remains the unit of evolution and promotion remains
platform-owned. This note does not introduce a second experiment ledger,
change a Genome schema, or give an improvement strategy access to a live bot,
production memory, or an acceptance set.

The source design used for this note is dated **2026-11-02** and has a recorded
SHA-256 of
`3a2a182fab0a77cf552a199417f5c88cadb0bd606902a6cab254f6575d0f5dfb`.
Repository and platform observations in that source were historical inspection
material; they were not re-run as a deployment or business acceptance test for
this document.

## Three data-use domains

| Domain | Purpose | Permitted feedback | Must remain unavailable |
| --- | --- | --- | --- |
| Training material | Train a model or optimize a reusable configuration | Explicitly approved source versions and required training metadata | Independent-acceptance questions, answers, detailed trajectories, Judge explanations, and their derived or rewritten forms |
| Development evaluation | Diagnose a candidate and guide a bounded change | Detailed failures and regression evidence from the approved development set | Treating the result as an independent exam or silently promoting its answers into training |
| Independent acceptance | Check generalization after a candidate reaches the stage gate | Only the declared aggregate or restricted verdict returned by the acceptance profile | Questions, answers, detailed logs, scoring explanations, and repeated probes that reveal the holdout |

The domain is part of the source identity. A file, row, event, summary, or
rewrite that inherits a protected parent remains protected; changing its format
or removing a label does not make it training-eligible.

## Required provenance at each handoff

Every derived artifact used by the loop should retain enough metadata to answer:

- which source version and source domain produced it;
- whether training is allowed and who approved that decision;
- which task, question family, session, and candidate/run it belongs to;
- whether the transformation was filtering, deduplication, summarization,
  redaction, or rewriting; and
- where the authoritative evidence and the current review status live.

A label is useful metadata, but it is not an access control. The training
entrypoint, evaluation service, storage permissions, and identities must reject
an artifact whose source or derived lineage is unknown or disallowed.

## Boundary-preserving flow

```text
source review and approval
          |
          v
versioned source manifest + purpose restriction
          |
    +-----+------------------+
    |                        |
    v                        v
training material      development evaluation
    |                        |
    +------------+-----------+
                 |
                 v
      candidate and regression evidence
                 |
                 v
      independent acceptance profile
                 |
                 v
       restricted verdict / decision
```

The independent acceptance branch must not return detailed holdout evidence to
the training or improvement identity. A score or aggregate can still leak
information through unlimited retries, so the acceptance profile needs a call
budget, a stable run association, and a stop rule.

## Checkpoints

1. **Registration** — record purpose, source version, training permission, and
   the owning review decision before a dataset or run is usable.
2. **Partitioning** — separate training, development, and acceptance by
   session, task, question family, and near-duplicate group; paraphrasing is
   not a sufficient split.
3. **Transformation** — propagate the most restrictive source rule through
   cleaning, aggregation, summaries, redactions, and rewrites.
4. **Execution** — use a dedicated evaluation identity, session/storage scope,
   and runtime memory boundary; do not write evaluation traces to production
   memory or a shared training export.
5. **Training admission** — accept only an approved manifest and immutable
   source identities; reject unknown, mixed, or protected lineage.
6. **Acceptance response** — return only the fields defined by the acceptance
   profile and account for retries; preserve the detailed evidence in the
   protected acceptance domain.
7. **Read-back** — after a run, verify the actual loaded source manifest,
   candidate/run association, and session ownership. A submitted request or a
   successful job state is not proof of the loaded data.

## Safe validation strategy

Boundary tests should use synthetic rows and deliberately marked decoys rather
than real conversations, business data, or acceptance answers. A useful local
regression set includes:

- an explicitly approved training item that is admitted;
- a protected acceptance item that is rejected;
- a summary and rewrite derived from that protected item that are also rejected;
- a mixed-source artifact whose lineage is incomplete and is rejected; and
- a near-duplicate pair that remains in one partition instead of being split
  by wording alone.

The test should inspect both the admission decision and the source manifest
that was actually loaded. It must not run a platform task, change a schedule,
or claim production enforcement from a local pass.

## Roles and allowed views

| Identity | May read | Must not read |
| --- | --- | --- |
| Tested agent | Current question, normal permitted materials, and tool results | Acceptance answers, Judge explanations, other holdout questions, or shared long-term evaluation memory |
| Judge / acceptance service | The answer key, scoring rules, and the complete actual trajectory | Forwarding those details to training or the improvement agent |
| Improvement agent | Development failures and the restricted acceptance verdict | Acceptance questions, answers, detailed logs, or an unrestricted storage identity |
| Training task | Approved training manifest and required configuration | Development or acceptance sets and their protected derivatives |
| Human reviewer | Evidence allowed by the review role | Reclassifying inspected acceptance material as independent without a new decision |

## Open questions

1. Which existing run record should carry source-purpose, candidate, and
   observed-load references without duplicating the Genome or experiment
   contract?
2. Which storage and service identities enforce the three views in each
   supported execution path?
3. How are near-duplicate families computed without exposing protected text to
   the training identity?
4. Which aggregate acceptance response is sufficient for the next decision
   while remaining resistant to repeated probing?

These questions require contract-specific implementation and live acceptance
work. They are not resolved by this research note.

## Compatibility and risk

Research only: no Service API, Plugin API, Genome schema, evaluation gate,
permission, runtime configuration, scheduling, or promotion behavior changes.
No private platform identifiers, raw sessions, business rows, credentials, or
acceptance answers are included. A local synthetic regression can establish
that a proposed transformation preserves its declared rule; it cannot prove
that every deployed identity or storage path enforces the boundary.
