---
status: research-draft
source_date: 2026-10-21
prepared_on: 2026-11-08
---

# Separating question metadata from Actor and Judge inputs

## Problem

A benchmark question has at least three different representations:

1. the question and permitted materials shown to the tested Actor;
2. the metadata needed to reproduce, score, and audit the question; and
3. the answer key and process rubric consumed by a Judge or acceptance service.

If these representations are bundled together, an Actor can receive reference
answers, hidden scoring rules, holdout identifiers, or execution-only fields.
That is both an evaluation-validity problem and a data-boundary problem. A
metadata schema should therefore describe the complete question definition while
keeping Actor input assembly and Judge input assembly as separate projections.

This note proposes that boundary for the existing improvement and evaluation
architecture. It is a research proposal, not a claim that every current
benchmark runner already enforces the separation.

## Existing architectural boundary

The Bot Genome remains the unit of evolution and promotion remains
platform-owned. This note does not introduce a second Genome, experiment ledger,
runtime evaluator, or promotion path. It complements the existing research
boundary in [`evaluation-data-boundaries.md`](rsi/evaluation-data-boundaries.md):
training material, development evaluation, and independent acceptance remain
different data-use domains.

The source specification for this note was confirmed on **2026-10-21** and has
SHA-256 `caae5fcf5b1c66dbb97451519a8b5a108291510f5bd3ddbefc6b41b75e1ddc87`.
The source was used as design material; this document does not assert that a
benchmark run, deployed evaluator, or production acceptance flow was executed
for this note.

## Canonical question metadata

A complete question definition is one JSON object. Batch exports may use JSONL,
with one object per line and the same structure. The top-level fields are
intentionally limited to the following:

| Field | Purpose | Boundary note |
| --- | --- | --- |
| `id` | Stable question identifier | Safe to expose only when the evaluation profile permits it; otherwise use a run-scoped opaque reference. |
| `question` | The natural-language task given to the Actor | Must not contain the reference answer or hidden rubric. |
| `tags` | Knowledge and capability nodes covered by the question | Classification metadata, not a runtime permission. |
| `sources` | Traceable facts and question provenance | Source evidence is for controlled assembly and review, not automatically for the Actor. |
| `answer` | Evidence-based reference answer | Judge/acceptance input only. |
| `answer_criteria` | Itemized final-answer scoring rules | Judge/acceptance input only. |
| `process_criteria` | Itemized investigation or interaction scoring rules | Judge/acceptance input only; evaluates observable trajectory evidence. |
| `difficulty` | Four-dimensional question difficulty | Metadata for calibration and analysis; not an Actor instruction. |

The schema deliberately does **not** add `scope`, `usage`, `approval`,
`run_id`, execution conditions, allowed/forbidden actions, scoring status, or
version fields to every question. Those are either part of the question text,
source-manifest metadata, run record, execution policy, or shared scoring
configuration. Duplicating them in every question creates competing authorities
and makes it easier for a projection to leak execution-only state.

### Source provenance

Each source entry should record:

- `type` — a consistent source category such as `code`, `document`,
  `conversation`, `log`, or `ui_config`;
- `name` — the concrete file, document, record, or module;
- `reference` — a reproducible location, revision, and section or line range
  where available;
- `observed_at` — when the evidence was actually read or checked; and
- `evidence` — the fact supplied by that location, distinguishing a rule,
  user report, observation, and proposal.

A source reference answers “where should a reviewer check?”; its evidence
answers “what was found there?”. A search hit, historical answer, or mutable UI
page is not automatically authoritative. When a source changes, preserve a
revision, snapshot, or content hash rather than treating the read date as an
immutable version.

Sources may establish the question and its evidence boundary, but the Actor
projection must include only the materials intentionally permitted for that
run. A source entry is not itself an authorization to expose the source.

### Answer and process criteria

`answer_criteria` evaluates the final answer. Each criterion should distinguish
full, partial, and zero credit using observable content rather than vague
quality labels. `process_criteria` evaluates what happened during the run. Each
process criterion should identify:

- the event evidence to inspect in the trusted trajectory;
- the observable behavior that earns full credit;
- a bounded partial-credit case; and
- the evidence that establishes zero credit.

A final response claiming “I checked the source” is not process evidence. The
Judge must inspect tool parameters, tool results, follow-up actions, and the
question/run association. A successful request proves that a result was
returned; it does not prove that the Actor used it correctly.

### Difficulty

Difficulty is metadata, not an Actor score. The proposed dimensions are:

- `evidence_location` — how difficult it is to find the correct evidence;
- `rule_reasoning` — how difficult it is to apply the relevant rules;
- `information_linking` — how difficult it is to connect identities, versions,
  dates, states, and sources; and
- `path_decision` — how difficult it is to choose the next investigation step.

Each dimension contains a `score` from 0 to 3 and a concrete `reason`. The
aggregate difficulty and coarse difficulty band should be computed by shared
calibration code rather than copied into each question object.

## Actor projection

The Actor receives a purpose-built projection, not the canonical metadata
object. At minimum, the projection contains:

```json
{
  "question": "<current user task>",
  "permitted_materials": [
    "<material explicitly allowed by this evaluation profile>"
  ],
  "interaction_policy": "<profile-specific read-only or action boundary>"
}
```

The exact envelope is an implementation contract to be defined by the owning
runtime. The important invariant is negative: the Actor projection must not
contain `answer`, `answer_criteria`, `process_criteria`, hidden acceptance
questions, Judge explanations, protected holdout identifiers, or unrestricted
source storage credentials.

The Actor must not receive the full `sources` array merely because the metadata
object contains it. A source can be used by a controlled question assembler,
filtered to an allowed excerpt, or withheld when it is part of the hidden
acceptance set. Redaction, summarization, or format conversion does not remove
the need to preserve the restrictive lineage rule.

## Judge and acceptance projection

The Judge receives the answer key and the rubric together with the complete
trusted trajectory for the associated question/run:

```json
{
  "question_ref": "<stable or run-scoped question reference>",
  "answer": "<reference answer>",
  "answer_criteria": [
    "<itemized answer checks>"
  ],
  "process_criteria": [
    "<itemized trajectory checks>"
  ],
  "trajectory": "<trusted event record for this run>"
}
```

This is a conceptual projection, not a finalized wire contract. It makes the
ownership direction explicit: the Judge can inspect protected scoring material;
the Actor and improvement identity cannot. The Judge must preserve the
question/run association and report the scoring result separately from the
question metadata. Detailed acceptance evidence should remain in the protected
acceptance domain unless the acceptance profile explicitly permits a restricted
aggregate to leave it.

A Judge must not forward the answer key, hidden rubric, detailed holdout trace,
or scoring explanation to the Actor, training task, or unrestricted improvement
identity. An aggregate verdict can still leak information through unlimited
retries, so independent acceptance needs a call budget, stable run association,
and a stop rule.

## Assembly and lineage rules

The boundary is best implemented as an explicit projection pipeline:

```text
question metadata + source manifest
                |
                v
        purpose and access check
          /                  \
         v                    v
   Actor projection     Judge projection
         |                    |
         v                    v
   actual Actor run      restricted scoring result
```

The implementation should enforce these checkpoints:

1. **Registration** — record the question revision, source purpose, permitted
   materials, and owning review decision before a run is usable.
2. **Projection** — construct Actor and Judge inputs in separate code paths;
   never serialize the full metadata object as the Actor prompt by default.
3. **Partitioning** — keep training, development, and acceptance sets separate by
   session, task, question family, and near-duplicate group. Rewording alone is
   not a sufficient split.
4. **Execution** — bind the Actor, Judge, storage scope, and run identifier to
   the intended evaluation profile. Do not write evaluation traces to
   production memory or a shared training export.
5. **Scoring** — let the Judge inspect the complete trusted trajectory, while
   returning only the fields allowed by the acceptance profile.
6. **Read-back** — verify the actual loaded question revision, projection type,
   candidate/run association, and storage scope. A submitted request or a
   successful job state is not proof that the intended projection was loaded.

Derived artifacts inherit the most restrictive source rule. A cleaned row,
summary, redacted excerpt, embedding, or rewritten question derived from a
protected acceptance item remains protected unless a separately authorized
review decision changes its classification.

## Safe validation strategy

Validation should use synthetic questions and marked decoys, not real
conversations, business rows, credentials, or hidden acceptance answers. A
minimum local regression set should verify that:

- a permitted Actor projection contains the question but no answer or rubric;
- the Judge projection contains the answer and criteria;
- a protected acceptance source is rejected from the training projection;
- a summary or rewrite derived from that protected source is also rejected;
- a mixed-source artifact with incomplete lineage is rejected; and
- a near-duplicate question remains in one partition instead of being split by
  wording alone.

The test should inspect both the projection decision and the source manifest
actually loaded. A local synthetic pass can validate projection logic; it
cannot prove that every deployed identity, storage path, or acceptance service
enforces the boundary.

## Roles and allowed views

| Identity | May read | Must not read |
| --- | --- | --- |
| Tested Actor | Current question, permitted materials, and tool results | Answer, hidden rubric, holdout questions, Judge explanations, or unrestricted evaluation memory |
| Judge / acceptance service | Answer key, scoring rules, and complete trusted trajectory | Forwarding protected scoring material to Actor or training |
| Improvement agent | Development failures and restricted acceptance verdict | Acceptance questions, answers, detailed holdout logs, or unrestricted storage |
| Training task | Approved training manifest and required configuration | Development or acceptance sets and their protected derivatives |
| Human reviewer | Evidence allowed by the review role | Reclassifying inspected acceptance material without a new decision |

## Open questions

1. Which existing run record should carry question revision, projection type,
   candidate, and observed-load references without duplicating the Genome or
   experiment contract?
2. Which storage and service identities enforce the Actor/Judge views on each
   supported execution path?
3. How should near-duplicate families be computed without exposing protected
   question text to the training identity?
4. What restricted acceptance response is sufficient for the next decision while
   remaining resistant to repeated probing?
5. Which schema and projection contract should be versioned first, and where
   should compatibility tests live?

These are implementation and live-acceptance questions. They are not resolved
by this research note.

## Compatibility and risk

Research only: no Service API, Plugin API, Genome schema, runtime evaluator,
permission, scheduling, promotion, or production behavior changes. No private
platform identifiers, raw sessions, business rows, credentials, hidden
acceptance answers, or internal service endpoints are included. The proposed
Actor/Judge projections require contract-specific implementation and conformance
tests before they can be treated as an enforced boundary.
