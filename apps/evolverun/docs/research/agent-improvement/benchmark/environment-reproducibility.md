---
status: research-draft
source_date: 2026-10-20
prepared_on: 2026-11-07
---

# Reproducible evaluation environments and actor-visible evidence

## Problem and scope

An immutable candidate does not make an evaluation reproducible if its world,
permissions, retrieval behavior, or observation timing can change. Conversely,
a frozen world is not a fair test if the evaluator expects an actor to know
facts that its approved tools and initial context cannot reveal.

This note proposes an environment review boundary for agent-improvement
research. It follows the [candidate binding](../rsi/candidate-version-binding.md)
and [data-use boundaries](../rsi/evaluation-data-boundaries.md) notes, but focuses
on what the actor can observe and how the environment behaves. It does not
create a task set, answer key, executable simulator, or new platform contract.

## Ownership and source status

The Bot Genome remains the unit of evolution under
[ADR 0019](../../../../../../docs/adr/0019-bot-genome-is-the-unit-of-evolution.md).
Verification and promotion remain platform-owned under
[ADR 0020](../../../../../../docs/adr/0020-promotion-is-platform-owned.md).
[ADR 0022](../../../../../../docs/adr/0022-apply-engine-desired-state-and-observed-drift.md)
supersedes the older compiled-document/digest mechanism: desired state is not
proof of observed runtime state. This note cannot grant a strategy permission
to alter verification, a live workspace, or a promotion ref.

The source is an environment-design review dated **2026-10-20**, with SHA-256
`07a0019a3424eee43107db7d8e0c852a71d83262291fc5d5aea9fb7f154cff2d`.
Its proposal and historical inspections are not evidence of a deployed
benchmark or a current product capability. This public consolidation omits
private service details, identities, source payloads, and operational examples.

The conceptual labels below are not new persisted artifacts or wire schemas:

- **A, actor definition:** the permitted candidate, model, harness, memory, and
  budget, resolved through their existing owning contracts.
- **E, environment:** facts, corpus, tools, identity scope, clock, initial state,
  failure scenarios, and permitted user responses.
- **J, evaluation:** assertions, grader, safety gates, adjudication, and the
  restricted verdict that may be returned to the improvement process.

Record these associations in the existing evaluation/run records. Changing E
or J is not an actor-only improvement and must not silently relabel an old run.

## Declare the evaluation track before comparing scores

| Track | Actor receives | What a result can support | What it cannot establish |
| --- | --- | --- | --- |
| Fixed evidence | Request, necessary history, and an approved evidence bundle | Interpretation and response quality for the supplied evidence | Retrieval ability, interactive tool use, or delivery to a real user |
| Frozen read-only interaction | Request, history, and scoped tools; no pre-supplied target return | Retrieval, clarification, diagnosis, and responses within the declared world | Current production correctness or end-to-end product delivery |
| Isolated product channel | A separately approved test entry and product protocol | Identity propagation, execution, and observable test delivery | That every infrastructure failure is an actor reasoning failure |

Do not combine these tracks into an unexplained leaderboard. Generated output,
a test sink receipt, and actual user-visible delivery are separate observations.
Moving to a broader track needs its own admission and isolation review.

## Freeze the smallest coherent dependency subgraph

Start from the allowed task outcomes, not a whole deployment clone or the
number of files available. Every necessary fact must be reachable under the
actor's approved identity, or the task must allow clarification or uncertainty.

| Dependency | Reproducibility evidence to review | Insufficient substitute |
| --- | --- | --- |
| Knowledge and retrieval | Corpus scope and versions, access rules, chunking/index build, retriever/reranker identity, parameters, cache and query clock | Only the reference answer documents or a mutable search endpoint |
| Identity and authority | Requester, owner, tool-authentication principal, resource subject, role and tenant conditions | A default identity or untrusted message metadata |
| Tool behavior | Input/output/error contracts, valid parameter domain, pagination, time semantics, effect classification, adapter version | A tool name, installed script, or HTTP verb alone |
| Product and object state | Approved specification, implementation/deployment association where relevant, initial objects and consistent relationships | Local source HEAD treated as deployed behavior |
| Interaction | Necessary history, released memory, compression visibility, allowed user-response facts and timing | A simulator with access to the reference solution |
| Execution | Runtime/dependencies, seeds where supported, budgets, stop/retry rules, reset strategy and observed loading | A successful preparation request or an unverified environment label |

A stable address without a stable implementation identity remains a limitation.
A frozen corpus with an uncontrolled retrieval service does not guarantee
repeatable retrieval. If a bounded replacement is used, record that as an
environment change rather than attributing all gains to the candidate.

Semantic consistency, behavioral coverage, and similarity to the intended
task distribution are different claims. A coherent snapshot does not prove
representativeness, and a replay of a few reference paths does not prove
coverage of other valid actions. State the measured scope and unknowns.

## Actor visibility is a timed access boundary

| Information class | Release condition | Boundary to preserve |
| --- | --- | --- |
| Initial request and necessary history | At the declared start | No hidden solution or another held-out task embedded in metadata |
| Ordinary approved knowledge | Through initial context or scoped retrieval as declared | Preserve legitimate shared knowledge; do not remove it merely because it contains useful facts |
| Tool evidence | Only after the actor's legitimate action produces the result | Record what was actually delivered, including pagination and truncation |
| User clarification | After an eligible question under the scenario's response rules | The responder knows only allowed user facts, not evaluator reasoning |
| Private evaluation material | Never to the actor or optimization identity | Isolate answer assertions, reference routes, grader details, labels, and protected derivatives |

Visibility needs filesystem, storage, network, and tool enforcement; a prompt
saying not to read an answer key is not isolation. Check caches, memories,
error stacks, replay metadata, and summaries as potential derived channels.
Do not require the actor to guess a fact merely because the evaluator can see
it. Conversely, a historical failure to retrieve it does not establish that
it is unavailable in the declared environment.

## Tool coverage, side effects, and observation evidence

A valid non-reference query, synonym, or page must receive the contractually
appropriate behavior. An unsupported adapter input is not an empty search
result; a partial result is not complete coverage; an authorization failure is
not proof that an object does not exist. Undeclared coverage gaps require
review rather than an invented response designed to keep the run moving.

A supposedly read-only operation may have effects. Apply the same policy to
MCP, shell, scripts, direct HTTP, subprocesses, forwarding, and message sinks.
No production credentials, default-deny outbound access, read-only sources,
isolated scratch space, and bounded execution are proposed controls, not
capabilities this documentation change has installed.

For a relevant operation, distinguish the attempted action, enforcement
response, actual execution, and observed effect. A blocked attempt can violate
a policy without having performed the external write. Missing logs or an
uncontrolled exit path cannot certify absence of side effects. Reconcile an
unknown action against its original receipt before permitting another action;
never replay a write to obtain a clearer result.

Actor reproduction, evaluator inspection, and public research summaries need
different views of the same evidence. Associate events with run/session,
attempt, causal parents, source/version, scenario clock, completeness,
payload reference/hash, and actual actor delivery using the existing record
owners. A redacted preview must not silently replace evidence a verdict needs.
No internal chain of thought is required or scored.

## Admission checks proposed for a future implementation

The following are **test designs, not tests run by this documentation change**.
Use synthetic identities, records, and decoys; do not copy real conversations.

1. Reset the world twice and compare relevant initial-state identities,
   approved access, and observed loaded versions. Report nondeterminism rather
   than inventing a seed or demanding identical language-model wording.
2. Show an independent solver can reach necessary facts with the same actor
   view and permissions, without consulting private evaluation material.
3. Exercise a valid alternative query, pagination, no-match, denied, partial,
   timeout, and unsupported path; verify distinct status and coverage evidence.
4. Change a relevant fact within legal scenario constraints and check the
   expected conclusion changes. Change irrelevant wording and check that the
   acceptance rule does not become tied to one reference trajectory.
5. Place synthetic private decoys in forbidden views and verify their absence
   from actor mounts, retrieval, memory, errors, summaries, and outputs.
6. Check all declared effect channels and record attempt/denial/execution/
   state evidence. An unchanged database alone is not a successful answer.
7. Preserve assessable evidence while marking missing components explicitly.
   Separate a measured actor failure, intended recovery scenario, unexpected
   environment invalidity, and grader abstention; do not turn null into zero,
   drop invalid runs silently, or renormalize incomplete evidence into success.

## Remaining decisions and compatibility

Owners still need to approve the task scope, source authority and conflicts,
legal tool domain, reset limits, identity propagation, evidence release,
coverage criteria, and the existing records that will carry these references.
A learned world simulator would be a separate environment experiment requiring
contract, cross-step state, authority, and transfer validation; it is not the
default source of evaluation truth or a reason to invent unknown state.

Research only: no Service/Plugin API, Genome schema, grader, permission, gate,
runtime configuration, scheduling, model, or platform behavior changes. No
benchmark, training, agent, Judge, or production operation was executed. Local
documentation checks and a green PR do not establish environment reproduction,
actor isolation, scientific improvement, or production acceptance.
