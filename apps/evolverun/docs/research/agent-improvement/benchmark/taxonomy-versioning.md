---
status: research-draft
source_date: 2026-10-21
prepared_on: 2026-11-11
---

# Taxonomy versions and coverage accounting

## Problem and scope

A classification directory is not evidence that every node has usable questions.
A later batch can use a different directory from the global design document,
and one question can cover several nodes without becoming several questions.
Comparing coverage percentages without the taxonomy snapshot and eligibility
rule therefore changes the denominator silently.

This note proposes explicit version binding, reviewed migration and separate
coverage counts. It is public research, not an adopted schema, a new runtime
service or evidence that an evaluator already implements these checks. The
[question metadata boundary](../benchmark-question-meta.md) remains unchanged;
Bot Genome and platform-owned promotion remain the existing architecture.

## Compared design snapshots

Two source snapshots were read on 2026-11-11. They describe taxonomy intent, not
accepted question counts, executed evaluation or measured capability coverage.
The baseline document is dated 2026-10-21; the later batch snapshot refers to an
October 25 review and was retained with a November 2 batch. Its local batch scope
does not replace the global document or rewrite older questions.

| Source snapshot | SHA-256 | Top-level classes | Second-level nodes |
| --- | --- | ---: | ---: |
| Baseline classification design | `f67f837288875a05602ac72d2aa9b6591ad919c61f0805dd4fcc5d6a64e006ab` | 6 | 32 |
| Later batch classification design | `884a4b02a0d8cba18fa4d51b1009a16d1a8f8ebecc774b4984947e7df48deaf0` | 6 | 41 |

The baseline count comes from its available-label table; the later count comes
from its node definitions. Repeated labels in examples are not counted again.
The local snapshots were not inside a Git checkout, so these are content-hash
identities, not invented source commit IDs or publication dates. Restricted
source locations and batch records are not copied into this public note.

| Conceptual class | Baseline nodes | Batch nodes |
| --- | ---: | ---: |
| Product functions | 6 | 8 |
| Business rules | 5 | 8 |
| Solution design | 5 | 7 |
| Operational troubleshooting | 6 | 8 |
| Fact lookup | 5 | 5 |
| Support handling | 5 | 5 |
| **Total** | **32** | **41** |

At the exact label-string level, 29 labels are shared, three occur only in the
baseline and 12 only in the batch: `32 - 3 + 12 = 41`. An unchanged label string
does not prove unchanged semantic boundaries; descriptions also require review.
Neither the extra nodes nor a batch's requested question count proves that
usable questions exist for those nodes.

## Multiple labels describe different dimensions

The first five classes describe task content, while support handling describes
cross-cutting behavior. They are intentionally non-exclusive. One question can
ask about an access rule, require checking the current role and evaluate safe
handling of a blocked query. It still has one canonical question identity.

Labels apply only to content the question actually tests, not every keyword in
the prompt. Do not attach safety, clarification or troubleshooting labels to
all questions by default. A label records intended coverage; observable task
evidence and an appropriate rubric are needed to establish measured coverage.

Source type, environment dependency, access policy, difficulty, answer quality
and execution result remain separate metadata dimensions. In particular:

- a troubleshooting label does not imply that a real environment is broken;
- an authorization label does not authorize a write or expose hidden materials;
- a difficult question is not necessarily a multi-label question; and
- counting a label does not prove the relevant process criterion was observed.

## Version binding without widening question metadata

Keep the canonical question fields described by
[question metadata](../benchmark-question-meta.md). Do not add a second authority
by injecting taxonomy version, approval state or execution policy into every
question. Instead, the dataset or run manifest should bind:

1. the immutable question-set revision or content hash and stable question IDs;
2. the taxonomy snapshot identity, content hash and effective batch scope;
3. the eligible population rule and review/acceptance state used in the report;
4. any mapping revision used for a cross-version comparison; and
5. the scoring/environment revisions when reporting executed evaluation.

These are proposed manifest responsibilities, not new mandatory API fields.
Version names are human-readable labels; a name alone is not an immutable
snapshot. A later file with the same filename must not silently change an old
run's taxonomy. Preserve old snapshots and reports rather than rewriting them.
The [data-use separation](../rsi/evaluation-data-boundaries.md) still applies to
training, development evaluation and independent acceptance.

## Migration requires semantic review

The three baseline-only concepts are not interchangeable spelling variants:

| Baseline concept | Later design | Required migration review |
| --- | --- | --- |
| Network admission and usage limits | Network/access boundaries plus resource/execution limits | Decide which part a question really tests; do not copy every old label into both children. |
| Task automation and effect optimization | Automation/long-horizon orchestration plus evaluation/optimization | Separate automation mechanics from the design of an evaluation or improvement goal. |
| Collaboration, scheduling and channel delivery faults | Collaboration/channel delivery plus scheduled-task/workflow faults | Determine whether the failure is routing/delivery or scheduling/workflow execution. |

The later snapshot also adds explicit nodes for tasks/workflows, evaluation
features, calling identity and delegated authorization, task/message semantics,
API/asynchronous solution design and instance/runtime problems. These are
classification extensions, not new implemented product capabilities.

A mapping may be exact, split, merged, newly introduced, retired or unresolved.
Record source and destination snapshot identities, the mapping decision and
review evidence. Even exact-string matches need description checks before
calling them semantically equivalent. Preserve unresolved mappings as gaps.

Do not rewrite historical labels in place, generate extra question IDs merely
to fill nodes or infer a missing label from a keyword. A reviewed relabeling
creates a new dataset revision; the old revision remains reproducible. Derived
reports explicitly name that revision and must not be presented as the original
run's results. This note does not perform a relabeling or approve any dataset.

## Coverage uses explicit populations and units

For a fixed dataset revision, taxonomy and eligibility rule, define:

- `Q`: the set of eligible **unique canonical question IDs**;
- `T`: the declared set of second-level nodes in that taxonomy scope; and
- `L(q)`: the set of reviewed, valid labels for question `q`, restricted to `T`.

A duplicated export row does not create another question. Conflicting content
for the same ID is a data-quality error to resolve, not permission to pick an
arbitrary row or inflate the denominator. Deduplicate repeated labels on one
question before counting hits. Unknown labels are reported as validation errors
and must not silently expand `T`.

| Measure | Definition | Interpretation |
| --- | --- | --- |
| Eligible question count | `N = |Q|` | Unique question population, not label or attempt count. |
| Label-hit count | `H = sum(|L(q)| for q in Q)` | Can exceed N because labels are non-exclusive. |
| Per-node question count | `n(t) = |{q in Q: t in L(q)}|` | Counts a question once per node. |
| Covered nodes | `C = |{t in T: n(t) > 0}|` | At least one eligible labeled question, not measured success. |
| Node coverage | `C / |T|` | Requires the exact taxonomy denominator; empty T is N/A. |
| Per-node question share | `n(t) / N` | Non-exclusive shares need not sum to 100%; empty Q is N/A. |

Parent-class counts use the union of matching question IDs, not the sum of child
counts. Parent labels and second-level labels are not counted twice as separate
node coverage. Report zero-hit declared nodes, unlabeled questions and unknown
labels separately; do not remove empty nodes from the denominator to improve a
percentage.

### Synthetic accounting example

Let `T = {A, B, C, D}` and three eligible questions have label sets `{A, B}`,
`{B}` and `{}`. Then N=3, H=3, C=2 and node coverage=2/4. A has one question,
B has two, and C/D have zero. Two children under the same parent can still cover
only two unique questions at that parent. The unlabeled question stays visible
in the population-quality report. A duplicate export of the first question
changes none of these counts. This is a synthetic example, not a benchmark result.

## Comparing runs and acceptance levels

Report candidate, reviewed, accepted and executed/scorable populations
separately, each with its eligibility rule. Raw generated candidates are not
accepted benchmark questions. Execution attempts and successful answers are
separate measures from question coverage. Missing evidence or an inapplicable
process criterion is not automatically full credit, zero credit or a valid
comparison; record the scoring policy and the gap.

Within one taxonomy, compare per-node counts and zero-hit nodes using equivalent
population rules. Across taxonomies, either keep separate versioned reports or
use a reviewed common comparison scope and disclose unmapped nodes/questions.
Do not compare a 32-node denominator with a 41-node denominator as a capability
improvement. Mapping coverage, question coverage and observed performance are
three distinct claims.

## Validation and remaining work

For this note, local checks compare the two snapshot hashes, distinct labels,
class counts and exact-string differences, and exercise the synthetic
deduplication/counting example. Documentation links, scope and sensitive-content
checks do not establish dataset acceptance or runtime enforcement.

Before adoption, domain owners still need to review semantic mappings, choose
manifest ownership, verify question eligibility and label validity, and agree on
reporting treatment for empty populations and unresolved mappings. No question
set was regenerated, relabeled or promoted; no evaluator, model, platform task
or production acceptance was run. This remains `research-draft`.
