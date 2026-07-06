# AGENTS.md

This file is the repository-wide instruction set for human contributors and AI
coding agents working on OCB.

## Source of Truth

Before changing code, read the files that define the boundary you are touching.
For architecture-sensitive work, these documents are mandatory:

- `docs/arch/arch.rules.md` — the OCB architecture constitution.
- `docs/arch/ci.enforce.md` — required CI gates for enforcing the constitution.
- `docs/arch/context-boundary-format.md` — required context boundary metadata
  for backend modules.
- `docs/arch/protocol-contract-tests.md` — required conformance test shape for
  plugin protocols.

If a module has its own `AGENTS.md`, follow it in addition to this file. The
more specific file controls only within that module.

## Project Overview

OCB is a monorepo for a multi-bot AI workbench. It includes:

- bot lifecycle management
- multi-bot coordination through BCS
- chat and runtime adapter services
- a frontend workbench
- contract and architecture validation documents

## Repository Layout

```text
ocb/
├── docs/
│   ├── arch/                 # Architecture constitution and CI constraints
│   └── open-source/          # Open-source readiness and design notes
├── scripts/                  # Local orchestration and utility scripts
├── src/
│   ├── backend/              # Python backend service
│   ├── frontend/             # TypeScript frontend workbench
│   ├── engine/               # Python engine adapter
│   ├── bcs/                  # Rust coordination service
│   └── tui/                  # Rust terminal client
└── tests/                    # Cross-module tests
```

## Module Responsibilities

| Module | Directory | Responsibility |
| --- | --- | --- |
| Frontend | `src/frontend/` | Web workbench UI |
| BCS | `src/bcs/` | Bot coordination, group chat, routing |


Entity ownership:

- Bot configuration, capabilities, and chat history belong to engine-facing
  services.
- User assets, bindings, and metadata belong to backend-facing services.
- Bot relationships, routing, and coordination belong to BCS.

## Architecture Rules

The architecture constitution is binding. In practice:

- Contracts are the authority for inter-component behavior.
- Service APIs and Plugin APIs must be defined, documented, versioned, and
  tested separately.
- Core logic must stay transport-agnostic.
- Delivery adapters translate protocol details; they do not own domain policy.
- Composition roots are the approved place to select concrete implementations.
- Raw environment access belongs in configuration loading, bootstrap,
  composition roots, or tests.
- Hardcoded external URLs, tokens, and private endpoints must not be introduced.
- Contract changes require matching docs and conformance or compatibility tests.
- Waivers for invariant violations must be explicit, owned, reviewed, and
  time-bounded.

## CI Expectations

Changes should preserve or improve the gates described in
`docs/arch/ci.enforce.md`:

- dependency boundary checks
- forbidden transport/framework usage in core
- restricted environment access
- configuration schema validation
- conformance test execution
- structural PR checklist completion
- red-flag detection for hardcoded URLs, tokens, and boundary drift

Do not weaken these checks to make a change pass. If a check is wrong, fix the
check and document why.

## Development Guidelines

Start from the requirement and the existing contract. Keep changes small and
traceable.

- Do not add features that were not requested.
- Do not add speculative abstraction or configurability.
- Do not refactor unrelated code.
- Match local style in the files you touch.
- Remove only dead imports, variables, or helpers caused by your own change.
- Propagate database and persistence write failures as errors; never silently
  swallow failed writes and return success.
- Prefer structured parsers and APIs over ad hoc string manipulation.
- Keep public-local development free of company-only services, registries,
  domains, credentials, and runtime state.

## Testing Rules

Choose tests based on risk:

- Narrow implementation change: run the closest unit tests.
- Contract change: update and run the relevant conformance tests.
- Cross-module behavior change: run all affected module tests.
- Architecture boundary change: run the architecture tests and update docs.
- BCS Rust changes: read `src/bcs/AGENTS.md` first, then run the relevant Cargo
  tests.

If you cannot run a required test, state exactly what was not run and why.

## Common Commands

```bash
# Root Python workspace
uv sync

# BCS
cd src/bcs
cargo test --workspace
```

Frontend public setup is still being finalized. Do not replace unresolved
public setup gaps with private registry or company-network assumptions.

## File Hygiene

Do not commit:

- secrets, tokens, cookies, or private keys
- local databases or runtime state
- generated logs and cache directories
- machine-specific agent configuration
- private service endpoints or private package indexes

Open-source defaults must be reproducible from public dependencies or clearly
marked as TODO.
