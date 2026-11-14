---
status: research-draft
source_date: 2026-11-05
prepared_on: 2026-11-14
---

# Agent-directed synthesis request and result contracts

## Problem and scope

A synthesis workflow should let an Agent choose what to investigate, which
approved material to consult, and how to revise a candidate without turning
that freedom into an unbounded write path. The outer caller also needs a
reliable answer to a different question: was the request accepted, what work
was actually attempted, what was rejected, and can the result be resumed or
retrieved without submitting the same work again?

This note proposes a small request/result boundary for Agent-directed
question or evaluation-material synthesis. It is an implementation-neutral
research proposal. It does not add a runtime endpoint, a new Genome artifact,
a second question bank, or an automatic publication path.

The proposal is intentionally compatible with the existing boundaries:

- the Bot Genome remains the unit of evolution;
- platform-owned verification and promotion remain authoritative;
- training, development evaluation, and independent acceptance remain separate
  data-use domains; and
- public research notes do not contain private sessions, credentials, raw
  business data, or platform-only identifiers.

See [ADR 0019](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md),
[ADR 0020](../../../../../../docs/adr/0020-promotion-is-platform-owned.md),
[the outer-loop map](outer-loop-integration.md), and
[the evaluation-data boundary](evaluation-data-boundaries.md).

## Design principles

1. **Autonomy inside a declared envelope.** The Agent may select a useful
   investigation route, choose among approved tools, draft or revise
   candidates, and stop when evidence or budget is insufficient. It may not
   expand its data scope, permissions, publication target, or acceptance set.
2. **Request identity is separate from execution identity.** A caller's
   idempotency key identifies the user's intent; a job/run identity identifies
   one accepted execution. Neither is inferred from a mutable branch, tab, or
   display label.
3. **Accepted is not completed.** A successful submission only means that the
   request was recorded. The caller must query the job and then fetch a result
   manifest before treating synthesis as complete.
4. **Deterministic gates surround probabilistic work.** Schema, scope,
   provenance, budget, exact duplicate, and isolation checks are hard gates.
   Semantic similarity and quality judgments may require review and must not be
   silently converted into approval.
5. **Artifacts are references until approved.** A candidate result may be
   saved for review, but it is not added to a benchmark, training set, or
   production source merely because a run completed.

## Request contract

The following is a conceptual envelope. Field names are illustrative and need
an owning API or Skill specification before implementation.

```yaml
schema: synthesis.request.v1
request_id: caller-generated-idempotency-key
project_id: approved-project
project_version: immutable-domain-package-version
intent:
  objective: "produce candidate questions for one reviewed direction"
  target_scope: "one explicit domain or task family"
  requested_count: 5
  partial_delivery: allowed
inputs:
  baseline_ref: immutable-approved-baseline
  source_refs:
    - approved-knowledge-snapshot
    - approved-trajectory-index
constraints:
  max_attempts: 20
  time_budget_seconds: 900
  excluded_topics: []
  required_metadata: [question, answer_boundary, evidence_refs, tags]
policy:
  actor_material: allowed-input-projection
  judge_material: unavailable
  acceptance_material: unavailable
  publication: review_only
capabilities:
  - inspect_baseline
  - search_approved_sources
  - read_bounded_evidence
  - draft_candidate
  - validate_candidate
  - compare_against_baseline
  - checkpoint
```

### Required request properties

- `request_id` is stable across retries. Re-submitting the same request must
  return the existing job or an explicit conflict, not create a second run.
- `project_version`, `baseline_ref`, and every source reference resolve to an
  immutable version before execution begins. A current pointer alone is not
  enough.
- `target_scope`, `requested_count`, exclusions, and required metadata are
  explicit. The Agent may refine a route inside that scope but cannot widen it
  by inference.
- The caller declares whether partial delivery is acceptable. A smaller result
  must report its unfilled count and stop reason rather than silently claiming
  the requested quantity.
- The policy projection identifies what the synthesis Agent can read and what
  the resulting Actor or Judge must not see. A label is useful evidence, not an
  access control; the backing tool and storage permissions enforce the boundary.

## Result contract

A result is a versioned manifest, not just a list of generated strings.

```yaml
schema: synthesis.result.v1
request_id: caller-generated-idempotency-key
job_id: immutable-execution-id
status: partial
project_id: approved-project
project_version: immutable-domain-package-version
baseline_ref: immutable-approved-baseline
counts:
  requested: 5
  attempted: 12
  retained: 3
  rejected: 6
  review_required: 2
  unfilled: 2
artifacts:
  candidates_ref: review-scoped-artifact
  manifest_ref: result-manifest
  evidence_refs:
    - bounded-evidence-reference
quality:
  schema_valid: true
  provenance_complete: true
  exact_duplicate_gate: passed
  similarity_gate: review_required
  actor_judge_isolation: passed
publication:
  dataset_published: false
  approval_state: review_required
stop:
  reason: "remaining candidates were uncertain or lacked sufficient evidence"
  budget_exhausted: false
warnings:
  - "semantic similarity is not an automatic duplicate verdict"
```

The result must preserve enough information to reconcile quantities and
provenance without exposing protected content:

- the exact request and project versions used;
- baseline and source snapshot identities;
- attempted, retained, rejected, review-required, and unfilled counts;
- candidate and evidence references with hashes where the storage contract
  permits them;
- the gate outcome and reason for every rejection or manual-review item;
- whether a reviewer or publication gate is still outstanding; and
- the stop reason, budget consumed, and resumability state.

If a candidate or evidence item is protected, the public result should expose a
scoped reference and redacted metadata rather than copying the source text.
The result must never imply that a successful artifact write proves that a
candidate was loaded, accepted, published, or used in production.

## Lifecycle and idempotency

The outer contract should make the state transitions observable:

```text
preflight
   | accepted
   v
queued -> running -> (complete | partial | review_required | failed)
                         |
                         +-> cancel_requested -> cancelled
```

- `preflight` validates schema, version references, capability policy, and
  resource limits without starting synthesis.
- `accepted` means the request has a durable job identity. It does not mean an
  Agent has run.
- `queued` and `running` report execution state; neither is a quality result.
- `complete` means the requested quantity passed the declared gates. It still
  requires the separate review/publication decision when policy says so.
- `partial` is a valid terminal result only when the request allows partial
  delivery and the counts plus stop reason are complete.
- `review_required` is the safe outcome for uncertain semantic duplicates,
  ambiguous evidence, or a policy decision outside the Agent's authority.
- `failed` reports an execution or infrastructure failure and must not be
  interpreted as zero valid candidates.
- `cancel_requested` is a distinct command from a result query. A cancelled
  job must preserve the request identity and the last durable checkpoint.

A resume operation uses the same `job_id`, request fingerprint, and immutable
input versions. It may continue from a checkpoint while preserving spent
budget; it must not replay an unknown write or silently start a replacement
job. A new intent requires a new `request_id`.

## Agent capability boundary

The initial capability set should be narrow and composable:

| Capability | Agent may do | Agent may not do |
| --- | --- | --- |
| Baseline inspection | Read an approved baseline manifest and bounded metadata | Alter the baseline or claim semantic uniqueness from a single lexical match |
| Source search/read | Search and read approved, bounded sources | Read acceptance material, credentials, arbitrary private paths, or unrestricted SQL |
| Draft/revise | Produce a candidate with required metadata and evidence references | Publish directly, mutate an existing accepted question, or hide a failed attempt |
| Validation | Run deterministic schema, provenance, scope, and duplicate checks | Lower thresholds because the requested count was not reached |
| Checkpoint/result | Save a manifest and report progress or stop | Mark a candidate accepted, training-ready, or production-ready |

The capability registry should declare the caller, input projection, output
schema, side effects, and failure semantics for each tool. The Agent should
learn available capabilities through a versioned description rather than
assuming a tool exists because a command or UI control is visible.

## Novelty and quality gates

Synthesis should use several gates with different meanings:

1. **Exact identity gate:** reject duplicate IDs and exact content already in
   the selected baseline.
2. **Approximate retrieval gate:** retrieve likely near-duplicates across the
   whole approved baseline, not only within the requested tag. This is a
   candidate-recall step, not a semantic verdict.
3. **Semantic review gate:** compare the actual task, evidence boundary, and
   required reasoning. Similar wording can represent a different skill; new
   wording can still test the same underlying case. Uncertain items go to
   `review_required`.
4. **Evidence gate:** every retained candidate has an answer boundary and
   traceable evidence references. Missing or conflicting evidence is a
   rejection or review item, not a guessed answer.
5. **Isolation gate:** the synthesis Agent cannot read the independent
   acceptance set or write directly to the Actor/Judge projection.
6. **Quantity and stop gate:** the result manifest accounts for every attempt
   and explains why the Agent stopped, including evidence shortage, duplicate
   rate, budget exhaustion, or policy refusal.

The baseline may be refreshed by the owner while the synthesis service is
being developed. A refresh is a new immutable baseline version; it is not a
silent overwrite of an in-flight job. The result must identify which baseline
was actually used.

## Compatibility and adoption path

This proposal can be adopted in stages without adding a parallel evolution
system:

1. Freeze the request/result schemas and capability discovery as a reviewed
   Skill or Service API contract.
2. Implement a local, deterministic harness with synthetic fixtures for
   idempotency, checkpoint/resume, count conservation, rejection, and
   Actor/Judge isolation.
3. Add one bounded runtime adapter that maps the contract to an approved
   execution environment. Keep the adapter thin; the policy and quality
   gates remain in testable core services.
4. Run review-only trials for one narrow scope. Compare the manifest and
   evidence, not only the number of generated candidates.
5. Add an explicit human or platform-owned publication step only after the
   request/result and isolation contracts have passed conformance tests.

The following are intentionally not claims of this note:

- that a production synthesis endpoint already exists;
- that a live model call, knowledge-base query, or platform job has been run;
- that semantic duplicate detection is solved by lexical similarity;
- that a candidate result is approved for training or benchmark use; or
- that an Agent may bypass existing Genome, evaluation, promotion, or access
  controls.

## Open questions for an owning contract

- Which service or Skill owns `project_version` and baseline references?
- What evidence representation can be safely returned for review without
  exposing acceptance material?
- Which semantic-review workflow owns `review_required` items and how are its
  decisions versioned?
- What is the minimum checkpoint that permits safe resume after a worker or
  container is replaced?
- Which publication operation, if any, is platform-owned and separately
  authorized?

Until those questions are answered by an owning contract and conformance suite,
this document remains `research-draft`.
