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
| Backend | [Backend guide](apps/backend/README.md), its domain index and diagnostic routes; read the affected domain boundary before its task-specific spec. |
| Backend MCP configuration | [`apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md`](apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md), [ADR 0015](docs/adr/0015-merge-user-and-bot-mcp-headers-per-key.md), and [ADR 0016](docs/adr/0016-mcp-url-and-explicit-headers-are-independent.md) |
| Backend apply engine, Bot Config Manifest and Bot Genome | [`core/apply_engine/README.md`](apps/backend/src/agentclaw/community/core/apply_engine/README.md) (the engine and its typed desired state), [`core/bot_config_manifest/README.md`](apps/backend/src/agentclaw/community/core/bot_config_manifest/README.md) and [`core/bot_genome/README.md`](apps/backend/src/agentclaw/community/core/bot_genome/README.md) (the two compilers; the genome also subscribes to its applies' ends to settle promotions, RSI-04), [ADR 0019](docs/adr/0019-bot-genome-is-the-unit-of-evolution.md), [ADR 0020](docs/adr/0020-promotion-is-platform-owned.md), and [ADR 0022](docs/adr/0022-apply-engine-desired-state-and-observed-drift.md) |
| BaaS | root `AGENTS.md`, the closest local guidance, and the affected protocol or deployment contract |
| Engine | root `AGENTS.md`, the closest local guidance, and the relevant runtime-layout contract |
| BCS | `apps/bcs/AGENTS.md`, `apps/bcs/CLAUDE.md`, then the closest crate `CONTEXT.md` |
| Frontend | `apps/frontend/AGENTS.md` and the affected API/UI contract |

Add a module-level `CONTEXT.md` only when that module has stable vocabulary
that is not already defined here. Add it to this map in the same change.

## Knowledge framework

Use this shared layout to navigate and contribute knowledge. Backend is the
first pilot; other apps adopt it as needed. Optional paths are created when
there is material to maintain, and existing authoritative docs stay in place.

```text
<repo>/
├── AGENTS.md                     # Agent entry and working rules
├── CONTEXT-MAP.md                # Knowledge map and routing
├── CONTEXT.md                    # Shared Chinese/English terminology
├── docs/
│   ├── agents/
│   │   ├── knowledge-standard.md # Responsibilities and maintenance
│   │   └── templates/            # Document templates
│   ├── arch/                     # Architecture constraints
│   ├── adr/                      # Durable decisions
│   └── flows/                    # Cross-app flows
└── apps/<app>/
    ├── README.md                 # App entry and knowledge navigation
    ├── AGENTS.md                 # App-specific rules, when needed
    ├── docs/
    │   ├── flows/                # App-local flows, when needed
    │   └── diagnostics/          # App-local diagnostics, when needed
    ├── specs/                    # Change intent, decisions and evidence
    └── <domain>/
        └── README.md or CONTEXT.md # Domain boundaries and contracts
```

The [knowledge standard](docs/agents/knowledge-standard.md) defines the
responsibilities and exclusions for each location; this tree is a navigation
overview, not a requirement to create empty directories or move existing docs.

- [Knowledge standard](docs/agents/knowledge-standard.md) and [templates](docs/agents/templates/README.md): what each document records and excludes.
- [Architecture](docs/arch/README.md), [decisions](docs/adr/README.md), and [cross-app flows](docs/flows/README.md).
- Backend: [app README](apps/backend/README.md), [local flows](apps/backend/docs/flows/README.md), [diagnostics](apps/backend/docs/diagnostics/README.md), and [change specs](apps/backend/specs/README.md).
- Domain boundaries stay beside their owning code, using an existing README or CONTEXT.md.

## Cross-app flows

- [Backend runtime targeting](docs/flows/backend-runtime-targeting.md): identity, binding, provider transport and runtime failure boundaries.
- [Knowledge layout](docs/agents/knowledge-standard.md): file responsibilities and maintenance; Backend is the first app using this layout.

## Where new knowledge belongs

- Stable shared vocabulary: update `CONTEXT.md`.
- Hard-to-reverse, system-level decision: add an ADR under `docs/adr/`.
- Implementation-ready work scoped to one module: use
  the owning module’s existing specs directory (Backend: `apps/backend/specs/YYYY-MM-DD-<topic>/`).
- A cross-module contract or plan: use `docs/specs/YYYY-MM-DD-<topic>/`.
- Research, diagnosis, and review evidence: keep it beside the consuming spec
  as `research.md`, or in `docs/research/` when it serves multiple specs.

Specifications and research are inputs to a decision. They do not override an
accepted ADR or the architecture rules.
