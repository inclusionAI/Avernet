---
status: research-draft
source_date: 2026-11-02
prepared_on: 2026-11-05
---

# Binding candidate configuration, model, and environment versions

## Problem

A harness change and a model change can both alter an agent's behavior. A result
cannot be attributed or reproduced if a run names only a bot, a mutable model
alias, or a workflow candidate label. Even an immutable definition does not
prove that the evaluated process loaded that definition or model.

The [outer-loop map](outer-loop-integration.md) identifies the two improvement
routes. This note proposes the evidence needed when those routes converge on
one candidate. It does not define a new artifact, API schema, or delivery path.

## Accepted boundaries and current evidence

The Bot Genome remains the sole evolution artifact, and the platform owns
verification and promotion. A strategy proposes a typed patch against a named
base revision; it does not edit a live bot, move its refs, or loosen its policy.
See [ADR 0019](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md)
and [ADR 0020](../../../../../../docs/adr/0020-promotion-is-platform-owned.md).

The [Genome Registry documentation](../../../../../../apps/backend/src/agentclaw/community/core/bot_genome/README.md)
describes immutable revisions, provenance, compare-and-swap refs, compilation,
and apply completion. These provide existing ownership and recording seams,
not proof that every model or environment dependency is already pinned.

The [Genome design](../../../../../../apps/backend/docs/bot-evolution/01-genome.md)
currently describes its compiled desired state as leaving `engine_config` and
`cli_tools` undeclared. This research must not assume that promotion applies a
model configuration merely because a revision can describe engine settings.
The owning configuration and runtime contracts must establish that capability
before a candidate depending on it is eligible to execute.

The [workflow-local candidate service](../../../../clawweb/public/modules/workflow/server/services/evolve/candidate-version-service.ts)
accepts injected evaluation and deployment ports and records workflow candidate
lineage. It is not an immutable definition of an entire bot, model service, and
environment. Its class name or local score does not replace Genome provenance
or the platform promotion gate.

These observations are from repository inspection on 2026-11-05, not a runtime
or deployment acceptance test. The conceptual source is a design dated
2026-11-02, which reorganized an earlier 2026-10-28 capability snapshot. Its
historical claims are not promoted to current deployment facts; this note is a
public research consolidation, not a claim of original implementation authorship.

## Proposed binding evidence

Use existing run, revision, and evaluation records for these associations. The
rows below are evidence categories for review, **not** a second Genome or an
accepted wire format. New persisted fields require a separately reviewed spec.

| Category | What must be unambiguous | What is insufficient |
| --- | --- | --- |
| Base and candidate | Existing base revision, candidate revision, typed patch, and lineage | A branch name or a mutable `candidate/*` ref without its resolved revision |
| Harness | Pinned persona, skill/code, curated memory, tool and resource dependencies covered by the revision | A copied Markdown file or a workspace path |
| Model | Immutable artifact digest or provider-supported version identity, serving configuration, and permitted inference settings | A mutable endpoint URL, alias, training-job success, or decreasing loss |
| Environment | Runtime/adapter version, dependency identities, environment kind, initial state, and approved capability scope | A clone label, reachable service, or assumption that the actor sees everything the author sees |
| Loaded state | Runtime observation associating the actual revision and model with the run/session | Desired configuration or successful save/apply submission alone |
| Evaluation | Evaluation-set and grader versions, run/session association, evidence completeness, and the comparison baseline | A score detached from the executed candidate |

Credentials remain controlled references and are never embedded in this
research note, candidate evidence, or public artifacts. Recording an access
scope does not authorize expanding it.

## Two routes, one comparison entry

The following symbols are hypothetical labels, not real revision or model IDs.
Every comparison fixes the evaluation contract and states any environment drift.

| Route | Candidate combination | Interpretation |
| --- | --- | --- |
| Harness only | New revision H1 with the baseline model M0 and environment E0 | A harness comparison if all other relevant conditions are held fixed |
| Model only | A revision/configuration allowed by its owning contract with model M1 and environment E0 | A model comparison only after actual loading is demonstrated; keep the harness fixed |
| Both | Explicit new harness revision H1 with model M1 and environment E0 | A joint comparison, not evidence of either factor's isolated contribution |

An unchanged model is recorded as the resolved baseline model, not omitted or
silently replaced by the latest service alias. An unchanged harness likewise
keeps an explicit revision. Where model selection needs a Genome patch, use
that existing typed patch path; where the supported delivery contract is
missing, record a blocker rather than bypassing it with an out-of-band edit.

For factor attribution, compare the baseline, harness-only, and model-only
combinations before interpreting a joint change. If budget permits only a
joint run, report that limitation instead of attributing the gain to training.

## Preparation and execution checkpoints

1. Resolve the base revision and candidate patch under the existing policy.
   Resolve allowed dependencies to immutable identities, or explicitly record
   why an external service cannot provide one.
2. Prepare an isolated evaluation target using capabilities the owning modules
   actually support. Keep session state, runtime memory, storage, and write
   scopes separate from production and from other candidate runs.
3. Verify the runtime-loaded revision and model, approved tool access, and
   usable initial state. A request being accepted is not a loading receipt.
4. Freeze the comparison association before execution. A change to harness,
   model, serving configuration, or a material dependency needs a new recorded
   candidate/run association; do not rewrite the meaning of an existing score.
5. Associate the execution ID with its actual session and complete trajectory.
   A final answer or a run ID alone does not establish process evidence.
6. Compare only under the declared evaluation contract. Verification and any
   later promotion remain separate platform-owned decisions.

Reconcile an unknown preparation or submission result against the original
operation before proceeding. Do not resubmit it merely because a client lost
the receipt. Failed persistence or missing provenance is not a successful bind.

## Reproducibility and incompleteness

A model service may be dynamic even when its URL remains unchanged. If it
cannot expose a stable version, mark the comparison as not fully reproducible;
do not invent a weight digest. Preserve the observation time and allowed
version evidence without exposing provider credentials or private endpoints.

Likewise, record external-state changes that cannot be reset. Mock/replay and
live environments must be distinguished in results; a mock score cannot be
reported as a measured improvement in a live business environment.

Missing model identity, unresolved configuration delivery, unknown loaded
state, or mismatched session ownership blocks a complete comparison. Missing
trajectory evidence remains incomplete, not a zero process score or success.
A preflight failure and a measured candidate failure are different outcomes.

## Open questions and acceptance work

- Which existing run record should carry model, environment, and observed-load
  references without duplicating the experiment ledger or Genome?
- What immutable identity does each supported model provider expose, and which
  configuration/delivery contracts can apply and observe that identity?
- How is drift between preparation and execution detected, including a changed
  external dependency behind an unchanged service address?
- Can a baseline and candidate be reset and replayed with the stated isolation
  guarantees, and can every score be traced to the actually loaded combination?
- Can the platform recover the previous known-good combination through its
  approved promotion/rollback path without trusting a strategy to move refs?

These require contract-specific implementation and real execution evidence.
This documentation change runs no agent, model, training job, evaluation, or
production operation and claims no measured capability improvement.

## Compatibility and risk

Research only: no Genome or Service/Plugin API schema, configuration, runtime,
evaluation gate, permission, or promotion behavior changes. No private platform
identities, raw sessions, or business data are included. Implementations must
follow the existing owners' specs and conformance requirements; this note does
not grant a strategy a new ability to publish or promote.
