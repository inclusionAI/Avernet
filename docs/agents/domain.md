# Domain documentation

## Read before exploring

Start with `CONTEXT-MAP.md`. Then read the relevant shared glossary, ADRs,
architecture rules, and module-local instructions before proposing a design or
changing code.

Use the vocabulary defined in `CONTEXT.md` in specifications, issue titles,
tests, API contracts, and reviews. If a needed term is missing, first check
whether an existing term already covers it. Add a term only when it represents
a stable domain concept with a distinct lifecycle or ownership boundary.

If a proposal conflicts with an accepted ADR, name that conflict explicitly.
Do not silently replace the decision; create a superseding ADR when the
decision itself changes.

## Document roles

| Artifact | Purpose | Location |
| --- | --- | --- |
| Context | Stable shared vocabulary and semantic boundaries | `CONTEXT.md`, or a mapped module `CONTEXT.md` |
| ADR | A durable, hard-to-reverse decision and its rationale | `docs/adr/NNNN-<slug>.md` |
| Spec | An implementation-ready contract and acceptance boundary | `src/<module>/specs/YYYY-MM-DD-<topic>/spec.md` |
| Plan | Ordered implementation slices, dependencies, and validation | beside its `spec.md` as `plan.md` |
| Tasks | Independently executable work items derived from a plan | beside its `spec.md` as `tasks.md` |
| Research | Evidence, code reading, experiments, or incident findings | `research.md` beside its consumer, or `docs/research/` |

Cross-module specifications belong in `docs/specs/YYYY-MM-DD-<topic>/`; they
must identify the participating modules and link each module-local contract.

## `/grill-with-docs` outcome rules

At the end of a grilling session, record only the outcome that has become
shared knowledge:

1. Update `CONTEXT.md` for a settled vocabulary or boundary.
2. Add an ADR for a system-level decision whose reversal needs an explicit
   migration or compatibility plan.
3. Write or update a spec when implementation is authorised or needs later
   handoff.
4. Keep hypotheses and source material in research; mark them as unresolved
   rather than presenting them as decisions.

Each spec must link its relevant ADRs, name its owning module, describe public
contract changes, and state its validation evidence. A completed spec records
the resulting PR or commit and any unverified deployment or runtime boundary.

## ADR lifecycle

Use front matter `status: proposed`, `accepted`, `superseded`, or `rejected`.
An accepted ADR remains readable after replacement; the replacement ADR links
back to it. Do not delete ADRs merely because their implementation changes.
