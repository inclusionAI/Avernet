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

## Pull Request Conventions

A pull request is read by external contributors and reviewers who do not share
your context, and its title becomes the commit message when the PR is squash
merged. Both must carry meaning on their own.

### Title

Use `<type>: <concise outcome>` or `<type>(<scope>): <concise outcome>`:

```text
feat: add whitelist observed state
feat(backend): add whitelist observed state
fix(bcs): reject routing updates for unknown bot ids
docs(arch): document plugin protocol conformance shape
```

| Type | Use for |
| --- | --- |
| `feat` | New functionality |
| `fix` | Bug fix |
| `refactor` | Restructuring with no external behavior change |
| `docs` | Documentation |
| `test` | Tests |
| `ci` | CI/CD |
| `build` | Build system or dependencies |
| `chore` | Other maintenance |

The optional scope is the module or area you touched, such as `backend`,
`baas`, `engine`, `bcs`, `frontend`, `gateway`, `arch`, or `ci`. Include it
when one module or area clearly owns the change. The outcome describes what the
change accomplishes, not which files moved.

Do not use vague or context-free titles such as `fix bug`, `update code`,
`sync`, a bare branch name, or an issue number with no summary.

### Description

Use these sections, in this order:

```markdown
## Problem
## Solution
## Validation
## Compatibility and risk (optional)
## Spec (optional)
## Related issues (optional)
```

- **Problem** — the observed defect, gap, or requirement, and why it matters.
- **Solution** — the approach taken, and the alternatives rejected when the
  choice is not obvious.
- **Validation** — the tests, gates, and manual checks you actually ran, with
  their results. State explicitly what you could not run and why.
- **Compatibility and risk** — contract, schema, config, or migration impact,
  and the rollback path.
- **Spec** — the contract or design document this change implements.
- **Related issues** — issues this closes or relates to.

`.github/pull_request_template.md` holds this skeleton. Fill in every required
section; delete the optional sections that do not apply rather than leaving
them empty.

## Pre-push Module Selection

Install the repository hooks separately in every Git worktree:

```bash
scripts/install_git_hooks.sh
```

By default the pre-push hook runs in **lint-only** mode: for changed Python
modules it runs the fast `python_sast_local.sh` SAST/lint gate, but skips the
heavier unit tests, changed-line coverage, and Singlebox E2E. Set
`OCB_PRE_PUSH_RUN_CI=1` to run the full gates for a push, or run
`scripts/ci/pre_push.sh` manually. The module-gate table below describes the
full behavior; in lint-only mode only the SAST/lint step of each Python module
runs, and modules without a standalone lint step (`src/gateway`, `src/frontend`,
`src/bcs`, and the singlebox coverage paths) run nothing.

```bash
OCB_PRE_PUSH_RUN_CI=1 git push
```

The pre-push hook models the change set of a pull request. Its merge target
defaults to the remote branch `origin/dev`. Override it persistently for the
current worktree or repository when the eventual PR targets another branch:

```bash
git config --worktree avernet.prePush.mergeTarget upstream/release/2026-07
```

Use `AVERNET_PRE_PUSH_MERGE_TARGET` for a one-command override; it has higher
priority than Git config:

```bash
AVERNET_PRE_PUSH_MERGE_TARGET=origin/main git push
```

Target values must use `<remote>/<branch>` form. Before selecting modules, the
hook fetches the target branch, resolves its latest commit SHA, calculates
`git merge-base <target-sha> <local-sha>`, and diffs that merge base against
the local SHA. Do not replace this with a direct `git diff origin/dev HEAD`:
when `dev` has advanced but the feature has not rebased, a direct tree diff
incorrectly includes target-only paths.

The hook fails the push if the configured remote branch cannot be fetched or
resolved, or if it has no merge base with the pushed commit. It must not fall
back to a stale target, the pushed branch's old remote SHA, or the root commit,
because those ranges can run unrelated module tests.

Module gates are selected from the committed files in the resulting diff:

| Changed path | Pre-push gate |
| --- | --- |
| `src/backend/` | Backend SAST, unit tests, changed-line coverage, and singlebox coverage |
| `src/baas/` | BaaS SAST, unit tests, changed-line coverage, and singlebox coverage |
| `src/engine/` | Engine SAST, unit tests, and changed-line coverage |
| `src/bcs/` | BCS/BCN unit tests in fast-fail mode, then unified singlebox coverage with BCS user-story E2E |
| `src/frontend/` | Frontend CI |
| `src/proxy/` | sandbox-proxy lint, unit tests, and changed-line coverage |
| singlebox scripts and Backend/BaaS acceptance or E2E paths | singlebox coverage |

The hook only checks committed changes in the pushed ref. Uncommitted working
tree changes are outside the natural boundary of a pre-push hook.

The unified `scripts/ci/singlebox_coverage.sh` starts one standalone product
stack and reuses it for Backend acceptance and BCS user-story E2E. BCS runs as
an LLVM-instrumented server; the gate requires all BCS E2E stories to pass,
runtime line coverage of at least 40%, method coverage of at least 36%, and
100% HTTP endpoint and bcs-cli leaf-command coverage. Its canonical artifacts
are copied to `scripts/.dependencies/coverage/singlebox/reports/bcs/` and are
included in `summary.json`, `summary.md`, and `dashboard.html`. Keep pre-push
and `.github/workflows/singlebox-coverage.yml` pointed at this same entrypoint,
then run `verify_singlebox_coverage_artifacts.py` against the generated report
directory so local pushes and GitHub PRs enforce the same artifact baseline.

### Singlebox Coverage Details

`scripts/ci/singlebox_coverage.sh` reads
`scripts/ci/singlebox_coverage_modules.yaml`. With no `--module` arguments it
runs every registered module; focused diagnosis can select one or more modules
with repeated `--module <name>` arguments. The runner starts one standalone
product stack, shares it across Backend acceptance stories and BCS user-story
E2E, then calculates the Core, Router API, and Plugin API denominators declared
by each module.

The per-module non-regression results and the shared-stack evidence are written
to `scripts/.dependencies/coverage/singlebox/reports/`: `summary.json`,
`summary.md`, `dashboard.html`, acceptance JUnit/logs, Backend and BaaS
coverage reports, plus the copied BCS reports under `bcs/`. GitHub's
`singlebox-coverage-artifacts` artifact uploads that same directory. The
artifact verifier must run against this report directory after the coverage
runner, so local pre-push and PR CI enforce the same result.

When adding a module, add meaningful live acceptance stories, declare the
complete Core/Router/Plugin denominators in
`scripts/ci/singlebox_coverage_modules.yaml`, establish thresholds from a
fresh focused run, then run the default all-module gate to catch shared-stack
interference. Do not inflate a result by excluding production Core paths or by
adding test-only calls to domain logic.

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

### Python Type Contracts

- Use `T | None` only when `None` is an intentional, valid state in the domain
  contract or at an external input boundary.
- Keep required configuration values, request fields, constructor arguments,
  and service method parameters non-optional end to end.
- Do not widen a required type to `T | None` merely for defensive programming,
  uncertain call sites, test convenience, or a fallback that hides missing
  input. Validate at the boundary and fail clearly instead.
- Before introducing an optional type, verify that a real caller can omit the
  value and that the receiving code defines meaningful behavior for `None`.

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

## Skills Architecture

Before changing Skills management, publication, mounting, or runtime
activation, read
`src/backend/src/agentclaw/community/adapters/http/skill_center/CLAUDE.md`.

- `skills-repo` and `skills-local` are complete content stores. An active
  Skills directory must expose only the Skills explicitly activated for the
  current Bot; do not add bridges from the active directory to a full content
  store.
- Do not add new engine-specific filesystem paths to Backend code. Physical
  layout ownership belongs to Engine Runtime and its versioned layout contract.
- Treat a `center://` source as a governed, versioned content source. A source
  prefix alone is not evidence that publication, distribution, and activation
  have completed.

# Code Review

1. **Code Review Standards Integration**:
   - You MUST read and follow all rules, scope restrictions, and guidelines defined in the global code review configuration file located at `/tmp/CODE_REVIEW.md`.
   - If `/tmp/CODE_REVIEW.md` exists, strictly apply its guidelines to this PR review.
   - If `/tmp/CODE_REVIEW.md` does not exist, perform normal processing.
