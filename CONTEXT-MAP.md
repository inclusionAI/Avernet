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
| Backend | root `AGENTS.md`, the closest local guidance, and the relevant `apps/backend/specs/` directory |
| Backend MCP configuration | [`apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md`](apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md), [ADR 0015](docs/adr/0015-merge-user-and-bot-mcp-headers-per-key.md), and [ADR 0016](docs/adr/0016-mcp-url-and-explicit-headers-are-independent.md) |
| Backend apply engine, Bot Config Manifest and Bot Genome | [`core/apply_engine/README.md`](apps/backend/src/agentclaw/community/core/apply_engine/README.md) (the engine and its typed desired state), [`core/bot_config_manifest/README.md`](apps/backend/src/agentclaw/community/core/bot_config_manifest/README.md) and [`core/bot_genome/README.md`](apps/backend/src/agentclaw/community/core/bot_genome/README.md) (the two compilers; the genome also subscribes to its applies' ends to settle promotions, RSI-04), [ADR 0019](docs/adr/0019-bot-genome-is-the-unit-of-evolution.md), [ADR 0020](docs/adr/0020-promotion-is-platform-owned.md), and [ADR 0022](docs/adr/0022-apply-engine-desired-state-and-observed-drift.md) |
| BaaS | root `AGENTS.md`, the closest local guidance, and the affected protocol or deployment contract |
| Engine | root `AGENTS.md`, the closest local guidance, and the relevant runtime-layout contract |
| BCS | `src/bcs/AGENTS.md`, `src/bcs/CLAUDE.md`, then the closest crate `CONTEXT.md` |
| Frontend | `apps/frontend/AGENTS.md` and the affected API/UI contract |

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
