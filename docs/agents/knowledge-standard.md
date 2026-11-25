# Repository knowledge layout

Use one repository map and one README per app as the entry points. Keep detailed
knowledge beside its owner and link existing contracts instead of copying them.
Backend, BaaS, BCS and BCSFuse initialize the same app-level entry points:
`README.md`, `AGENTS.md`, `docs/agents/`, `docs/arch/`, `docs/adr/`,
`docs/flows/README.md`, `docs/diagnostics/README.md`, and `specs/README.md`. Populate them with
verified existing knowledge and links; add topic pages when supported content
exists. Other apps retain their current documentation until deliberately brought
into this layout.

## File responsibilities

| File | Records |
| --- | --- |
| Root `AGENTS.md` | Repository working rules and the instruction to read the context map. |
| Root `CONTEXT-MAP.md` | System boundaries, app responsibilities, repository relationships and links to relevant knowledge. |
| Root `CONTEXT.md` | Stable shared vocabulary and semantic distinctions. |
| `docs/arch/` | Architecture constraints and their enforcement. |
| `docs/adr/` | Durable decisions, their rationale and consequences. |
| `docs/flows/` | Important business flows spanning apps: participants, contracts, state changes and failure branches. |
| App `README.md` | The app's purpose, interfaces, source layout, assembly, knowledge navigation and verification. |
| App `AGENTS.md` | Additional working rules and applicable knowledge entry points. |
| App `docs/agents/` | App-specific knowledge standard and access to shared templates. |
| App `docs/arch/` | App-scoped architecture constraints and existing contract navigation. |
| App `docs/adr/` | Durable app-scoped decisions and their lifecycle. |
| App `docs/flows/` | Complex internal flows, when they need their own page. |
| App `docs/diagnostics/` | Symptoms, candidate causes, distinguishing evidence, next actions and limitations. |
| Domain `README.md` or `CONTEXT.md` | Local responsibilities, provided/consumed contracts, dependency boundaries and change impact. |

This page extends [domain documentation](domain.md). Specs, plans and research
keep their existing roles; unresolved incident hypotheses stay in research.
Framework directories contain a README describing their scope; add topic pages
when there is supported content. Existing ADRs, API docs and
machine-parsed Context Boundaries keep their formats and authoritative locations.
Tool-specific instruction files point to shared guidance rather than repeating it.

## Exclusions by file

| File | Do not record |
| --- | --- |
| Root or app `AGENTS.md` | Full architecture, detailed runbooks, raw incident records or duplicated module contracts. |
| `CONTEXT-MAP.md` | Detailed implementations, full specifications, credentials or runtime data. |
| `CONTEXT.md` | Temporary incident conclusions, volatile configuration inventories or implementation walkthroughs. |
| `docs/arch/` | One-off task plans or unverified incident hypotheses. |
| `docs/adr/` | Routine task lists, transient observations or duplicate implementation guides. |
| `docs/flows/` and app flow pages | Complete source listings, raw incidents or unverified runtime events. |
| App README | Complete specifications, duplicated domain contracts or private runtime records. |
| App knowledge standard/templates | Duplicated global rules, private evidence or facts owned by contracts. |
| App architecture/ADRs | Unverified incidents, task plans or copies of system-wide decisions. |
| App diagnostics | Credentials, raw production data, unsupported root causes or unauthorized operational actions. |
| Domain README/CONTEXT | Entire system workflows or rules that override the architecture constitution. |
| `specs/` | Inferred deployment status, rewritten historical evidence or copies of all current documentation. |
| Knowledge standard and templates | App-specific facts that are owned by another contract or domain document. |

Shared `CONTEXT.md` terminology is maintained in Chinese and English together;
keep both definitions aligned when adding or changing a term. Other framework
documents use English.

## Existing docs and change specs

`docs/` holds reusable knowledge; `specs/` records the intent, decisions, execution
and verification of a particular change. Keep existing API, subsystem and runbook
documents in place when they already have a clear owner. Link them from the app
README rather than copying them into a new tree.

Location alone does not establish authority: a design proposal or research note
under `docs/` can still be provisional. Before using it, check its status, scope,
relevant accepted decisions and the target-version contract/source. Retain old
documents as historical references or provide a link to their replacement.

Read only the spec for the task or historical question being investigated. Keep
its existing path and format; a completed spec is not mandatory startup context.
Older specs without reliable status remain unverified history until checked;
neither their date nor a checked task list proves merge, integration or deployment.

Some existing `specs/.../contract.md` files are explicitly referenced as current
contracts. Preserve that authority until a reviewed replacement is adopted and
its consumers/links are updated. Do not classify all specs as historical merely
because of their directory name.

At delivery, promote only the verified reusable result:

| Result of the change | Update its owning knowledge |
| --- | --- |
| Stable term or semantic distinction | `CONTEXT.md` or the relevant domain context. |
| Interface, invariant or responsibility | Owning contract/schema and domain README; update tests as required. |
| Durable architectural decision | The appropriate ADR, preserving its decision lifecycle. |
| Reusable flow or diagnostic finding | Existing flow/runbook/diagnostic page, or a new topic when needed. |
| New navigation or module ownership | App README and, when cross-app, `CONTEXT-MAP.md`. |

Then append the actual outcome to the spec or its existing delivery report:
PR/commit, verification performed and gaps, and links to the knowledge updated
(or why no durable knowledge changed). Preserve prior evidence and rejected
alternatives; append supersession links instead of rewriting history. A delivery
record must distinguish implementation, merge, host integration and deployment.
There is no requirement to copy or relocate the entire spec into `docs/`.

## Agent entry points

Keep root AGENTS.md a short task-triggered index with a few shared invariants.
Each pointer states when its target must be read. Put detailed development, PR
and validation rules in their owning pages; do not preload all of them at task
startup. The context map locates knowledge, while AGENTS locates working rules.

## App README

Cover these six concerns, grouping them to suit the existing document:

1. Responsibilities and boundaries.
2. Provided interfaces and dependencies.
3. Source layout and domain navigation.
4. Assembly, configuration and version sources.
5. Important flows and diagnostic entry points.
6. Development and verification, including change impact.

The README is the app's index; a second knowledge index is unnecessary. Link
formal contracts and existing runbooks. A short module can keep everything in
its README; a large one discloses topic pages on demand.

## Flow and diagnostic pages

A flow names its trigger and scope, participating components, important steps,
state ownership, failure branches and source/verification links. Put an app-local
flow in that app; put a cross-app flow under root `docs/flows/`. A sequence diagram
is useful when it clarifies the participants, but is not required for every page.

A diagnostic page starts from a symptom, lists plausible causes and the evidence
that separates them, then gives the next action and remaining uncertainty. A
current database state is not evidence of a historical event. Keep production
records, credentials and raw incident sessions outside the public repository.

## Versions and maintenance

Use relative links within a checkout. A host reads public knowledge at its
recorded dependency revision; a standalone checkout uses its own revision. A
source checkout, a build manifest and a running instance can describe different
versions. Record the actual revisions and evidence gaps in the task's report.

When a contract, boundary or relevant behavior changes, update its owning page
and navigation in the same change. For a new diagnostic conclusion, identify
the source or test that supports it and its scope. The implementation can expose
a contract violation; it does not silently replace the contract.

Before review, check changed links and code references, follow the affected
navigation from the root map, and compare factual statements with the target
source/configuration/tests. Report separately what was read, what was executed
and what remains unverified. This layout does not introduce a scheduler, a new
metadata registry or a claim that documentation alone improves diagnosis.
