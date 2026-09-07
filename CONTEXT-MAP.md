# Avernet context map

This is the entry point for product, architecture, and implementation work.
Read only the sources relevant to the boundary being changed.

## System-wide domain

- [`CONTEXT.md`](CONTEXT.md) defines shared product vocabulary, currently
  centred on the Skill lifecycle, Spaces, Bot consumption, and external
  publication.
- [`docs/adr/`](docs/adr/) contains accepted and superseded system decisions.
- [`docs/arch/`](docs/arch/) remains the binding architecture constitution and
  CI contract.

## Module-local context

| Area | Read before changing it |
| --- | --- |
| Backend | root `AGENTS.md`, the closest local guidance, and the relevant `src/backend/specs/` directory |
| BaaS | root `AGENTS.md`, the closest local guidance, and the affected protocol or deployment contract |
| Engine | root `AGENTS.md`, the closest local guidance, and the relevant runtime-layout contract |
| BCS | `src/bcs/AGENTS.md`, `src/bcs/CLAUDE.md`, then the closest crate `CONTEXT.md` |
| Frontend | `src/frontend/AGENTS.md` and the affected API/UI contract |

Add a module-level `CONTEXT.md` only when that module has stable vocabulary
that is not already defined here. Add it to this map in the same change.

## Where new knowledge belongs

- Stable shared vocabulary: update `CONTEXT.md`.
- Hard-to-reverse, system-level decision: add an ADR under `docs/adr/`.
- Implementation-ready work scoped to one module: use
  `src/<module>/specs/YYYY-MM-DD-<topic>/`.
- A cross-module contract or plan: use `docs/specs/YYYY-MM-DD-<topic>/`.
- Research, diagnosis, and review evidence: keep it beside the consuming spec
  as `research.md`, or in `docs/research/` when it serves multiple specs.

Specifications and research are inputs to a decision. They do not override an
accepted ADR or the architecture rules.
