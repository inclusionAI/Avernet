# Repository knowledge layout

Use one repository map and one README per app as the entry points. Keep detailed
knowledge beside its owner and link existing contracts instead of copying them.
The Backend is the first app organized this way; other apps retain their current
documentation until they are deliberately brought into this layout.

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
| App `AGENTS.md` | Additional working rules that apply only to that app, when needed. |
| App `docs/flows/` | Complex internal flows, when they need their own page. |
| App `docs/diagnostics/` | Symptoms, candidate causes, distinguishing evidence, next actions and limitations. |
| Domain `README.md` or `CONTEXT.md` | Local responsibilities, provided/consumed contracts, dependency boundaries and change impact. |

This page extends [domain documentation](domain.md). Specs, plans and research
keep their existing roles; unresolved incident hypotheses stay in research.
Create a directory only when it has useful content. Existing ADRs, API docs and
machine-parsed Context Boundaries keep their formats and authoritative locations.
Tool-specific instruction files point to shared guidance rather than repeating it.

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
