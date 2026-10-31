# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**Avernet** (`inclusionAI/Avernet`) is the open-source infrastructure layer for
persistent, coordinated, multi-agent systems at organizational scale. It is the
**upstream / community** codebase that ocb consumes as a git submodule; this
checkout is standalone (no `agentclaw.corp`, no MOSN, no company-internal
services). When changing code here, you are changing the OSS project — keep it
public-clean and reproducible from public dependencies.

The two Git remotes are **not interchangeable**:
- `github` → `github.com/inclusionAI/Avernet` — **push here** (the real source).
- `origin` → `code.alipay.com/mirrors/Avernet` — AntCode **mirror, fetch-only**.
  Pushing to `origin` is rejected for lack of write permission *and* an email
  check; do not rewrite the `dev` upstream history to work around either.

Integration branch is **`dev`** (rebase, never merge). Some CI gates
(`architecture-checks.yml`) only run on `main`; `unit-tests.yml` /
`frontend-nextgen.yml` run on `dev`, `main`, and `REL*`. Spawn fix/feature
branches from `dev`, not from a `REL` tip — a `REL`-based PR diff swallows the
full release history.

## Read before touching architecture-sensitive code

The `AGENTS.md` at the root is the binding contributor contract. For boundary
work also read:
- `CONTEXT-MAP.md` — the entry point that routes you to the right module context.
- `CONTEXT.md` — shared product vocabulary (Skill lifecycle, Spaces, Bot
  consumption, external publication). This glossary is the authority for naming
  in product, code, schemas, and review.
- `docs/arch/arch.rules.md` — the **Microkernel Architecture Constitution v3.1**
  (contracts are the single authority; every plugin Protocol has `local/` and
  `prod/` implementations plus `Noop`/`Mock`; conformance tests under
  `tests/contracts/`; configuration-driven wiring — no scattered `if is_local`).
- `docs/adr/` — accepted/superseded system decisions.
- Module-local `AGENTS.md` / `CLAUDE.md` override the root only within their
  module. Currently: `apps/bcs/CLAUDE.md` + `apps/bcs/AGENTS.md`,
  `apps/frontend-nextgen/CLAUDE.md`, `apps/frontend/AGENTS.md`.

## Big-picture architecture

```
Frontend workbench ──▶ Backend (agentclaw.community: bot lifecycle, sessions,
  apps/frontend,        auth, skills, Skill Center) + BaaS (device/sandbox
  apps/frontend-nextgen) lifecycle) + Gateway (signed X-Avernet-Principal auth)
                              │
                              ▼
                     Engine adapter (port 20003: OpenClaw / Moltis)
                              │
                              ▼
                     BCS (Rust, port 21000): coordination plane —
                     bot registry, discovery, group chat, context fusion,
                     routing, friendships, collaboration runtime.
```

**Entity ownership is strict and cross-service state stays where it owns:**
- Bot configuration, capabilities, chat history → **Engine**
- User assets, bindings, metadata → **Backend**
- Bot friendships, routing, coordination state → **BCS**

### BCS layering (Rust, `apps/bcs/`)

Hexagonal, single call direction. Violating direction breaks architecture CI:

```
delivery adapters (http/ws/bcs-cli)  →  service-api (application / core / port)
                                          →  services/* + services/*-store
                                              →  plugin-api → plugins/*
```

- Delivery adapters own their protocol definitions (HTTP routes, WS frames,
  CLI args); those never move into `service-api`.
- Core service contract is named **`core`**, not `domain`.
- Store services implement `::port::repo`; they call `plugin-api`, never
  adapter crates.
- Plugin APIs (`plugin-api/bcs-cache-api`, `bcs-db-api`, `bcs-auth-api`, …)
  are independent crates; concrete impls live in `plugins/*` (local/sqlite,
  mysql/redis, anthropic/openai, alipay/github/google/wechat oauth, …).
- **No global `cargo fmt`.** Format only the lines you change; avoid import
  reordering / whitespace churn.
- Migrations are **frozen** once committed — never edit a numbered migration,
  add a new numbered one instead. Preserve migration names, numbers, checksums.
- Tests prefer `cargo nextest`; crates test beside code (`tests/` or inline
  `#[cfg(test)]`); shared fixtures in `bcs-test-support`.

### Skills architecture (Backend / Skill Center)

`skills-repo` and `skills-local` are **complete content stores**. The active
directory must expose **only** the Skills activated for the current Bot — never
add bridges from the active directory back to a full content store. Treat
`center://` as a governed, versioned content source; a source prefix alone is
not proof that publication/distribution/activation completed. See
`apps/backend/src/agentclaw/community/core/skill_center/AGENTS.md` before
changing Skills management, publication, mounting, or runtime activation.

### frontend-nextgen open-core boundary

`apps/frontend-nextgen` is a **public open-source artifact** exported from the
internal TeamClaw source (`OPEN_CORE_MANIFEST.json` records the source commit).

- Never add internal registries (`registry.antgroup-inc.cn`), `npm:@alipay/*`
  aliases, `@alipay/bigfish`, company-only domains, private endpoints, or
  credentials. `scripts/check-open-core.cjs` fails the build on these.
- Forbidden paths: `.internal-paths`, `src/internal`, `src/extensions/internal.ts`,
  `config/internal`, `config/routes.internal.ts`.
- Internal-only variance is injected at defined seams — **Capabilities**
  (`src/capabilities/`, extended then sealed at boot) and the **extensions
  alias** (`@/extensions` → `src/extensions/empty` in Open Core). Read variance
  via `getCapabilities()`; wrap, don't fork, components.
- Changes here must be carried back to the TeamClaw Open Core baseline before
  the next export.

## Common commands

### Singlebox — the only local entry point

`./singlebox/singlebox.sh` is the supported local stack (BAAS + Backend +
BCSFuse + BCS + 5 OpenClaw bots + demo bot + Frontend). It always runs in
**isolated mode** (writes under `singlebox/.dependencies/standalone/` and
`.standalone-openclaw/`); `--local` / `--dev` are removed.

```bash
./singlebox/singlebox.sh install-tools     # interactive: installs node/uv/openclaw/rust/protoc as needed
./singlebox/singlebox.sh check             # preflight deps + ports, no build/start
./singlebox/singlebox.sh                   # default: setup + start all (frontend → http://127.0.0.1:8000/)
./singlebox/singlebox.sh status            # show all services
./singlebox/singlebox.sh restart bcs       # restart one service (baas|backend|bcs|bcsfuse|frontend|bots|gateway|engine)
./singlebox/singlebox.sh stop
```

Groups: `all` (default), `bcs_bots` (BCS + 5 bots), `bcs_frontend` (BCS +
Frontend, E2E), `bbs_demo`, `hybrid` (OpenClaw + optional Claude Code bots).
Start BCS alone with `start bcs`; BCS + 5 bots with `start bcs_bots`. Select
the nextgen workbench with `FRONTEND_VARIANT=nextgen` (see
`docs/singlebox-nextgen-local.md`). Each start kills anything on its own port
and rebuilds the 5-bot profile, so you don't manually `stop` first. Local
overrides go in `singlebox/.env.local` (gitignored; never commit credentials).
`USE_CN_MIRROR=1` switches npm/PyPI/rustup/corepack to public CN mirrors.

### BCS (Rust 1.91+, `apps/bcs/`)

```bash
cd apps/bcs
cargo build
cargo run --package bcs                              # or: cargo run -- -c configs  (local: SQLite, loopback auth)
cargo test --workspace                               # prefer: cargo nextest run
cargo test --package bcs                              # one crate
cargo test --package bcs -- test_route_to_driver      # one test

cargo run --package bcs-cli -- health                # CLI smoke
cargo run --package bcs-cli -- onboard --name "Bot" --summary "Helper"
cargo run --package bcs-admin -- db migrate --dialect mysql --check-files   # migrations
cargo run --package bcs-admin -- db migrate --dialect mysql --apply
```

Tests use in-memory SQLite; reduce parallelism on lock errors:
`cargo nextest run -j 2`. For BCS E2E coverage (instrumented build):
`./singlebox/singlebox.sh start bcs_bots --with-bcs-coverage`. In offline /
non-git builds set `BCS_STATIC_VERSION=1` (build.rs reads git for
`GIT_COMMIT_HASH` / `BUILD_DATE`).

### DeepSeek Harness BCN plugin (Node, `apps/bcs/crates/plugins/deepseek-harness-channel-bcn/`)

```bash
cd apps/bcs/crates/plugins/deepseek-harness-channel-bcn
npm install --ignore-scripts --no-package-lock
npm run ci
```

### Backend (Python 3.12, `apps/backend/` — `agentclaw.community`)

```bash
cd apps/backend
uv sync --frozen
DEPLOY_PROFILE=test uv run pytest tests/community -v            # main suite
DEPLOY_PROFILE=test uv run pytest tests/community/path/to/test_x.py -v   # one file
DEPLOY_PROFILE=test uv run pytest tests/community/path/to/test_x.py::TestClass::test_method -v
./scripts/ci_test.sh                                            # CI gate (pytest + changed-line cov ≥ 80%, total ≥ 75%)
RUN_ACCEPTANCE=1 uv run pytest tests/community/acceptance/ -v   # acceptance suite (needs baas/mosn/registry; skipped by default)
```

`pytest.ini` excludes `tests/community/acceptance/` by default — they need a
spawned environment. `pythonpath = . src` (no corp, no submodule bridging).

### BaaS (Python, `apps/baas/`)

```bash
cd apps/baas
uv sync
just test-ci                      # ./scripts/ci_test.sh
just test                         # full pipeline (bare e2e-sqlite)
just test-full                    # sqlite + zdas overlays
just test-e2e-one <test_name>     # single E2E test
```

### Engine adapter (Python, `engine/adapter/`)

```bash
cd engine/adapter
uv sync
./scripts/run.sh --port 20003 -l                 # local mode, default openclaw engine
./scripts/test.sh unit                            # group-driven; uses launch.json cases
./scripts/test.sh e2e --group=admin --cases=foo,bar   # filter by group + cases
./scripts/test.sh unit --include-no-ci            # include no-CI cases
./scripts/ci_test.sh                              # CI gate
python start.py --port 20003                     # bare uvicorn entry: engine.community.api.app:app
```

`test.sh` dispatches by `presentation.group` / case name in `launch.json`; pass
`--dry-run` to print the plan without running.

### Gateway (Python, `apps/gateway/`)

```bash
cd apps/gateway
uv sync
just test-ci              # ./scripts/ci_test.sh --group ci
just test                 # full pipeline
just dump-openapi         # regenerate/publish OpenAPI
```

### Frontend — legacy (`apps/frontend/`, UmiJS Max, package name `open-claw`)

```bash
cd apps/frontend
npm i
npm run devs:local:oss     # OSS dev server (MOCKS=none PRESET=local)
npm run lint               # max lint
npm test                   # jest
```

### Frontend — nextgen (`apps/frontend-nextgen/`, name `avernet-frontend-nextgen`)

```bash
cd apps/frontend-nextgen
npm ci                                  # postinstall runs `max setup`, generates src/.umi
TEAMCLAW_GW_BASE=http://127.0.0.1:8888 npm run dev:local
npm run check:open-core                 # ← run FIRST; open-core boundary gate
npm run typecheck                       # tsc --noEmit
npm run lint                            # max lint
npm test                                # jest (tests live in test/, node env by default)
npx jest test/foo.test.ts -t "case name"   # single test
npm run ci                              # typecheck + lint + test --runInBand + build  (the CI gate)
```

DOM tests need a `/** @jest-environment jsdom */` docblock at the top of the
file. Run `npm run check:open-core && npm run ci` before pushing.

## Git workflow & PR conventions

- **Rebase**, never merge. Branch from `dev`.
- **Title** (becomes the squash-merge commit message):
  `<type>(<scope>): <concise outcome>` — e.g.
  `feat(backend): add whitelist observed state`,
  `fix(bcs): reject routing updates for unknown bot ids`.
  Type: `feat|fix|refactor|docs|test|ci|build|chore|perf`. Scope: the module
  touched (`backend`, `baas`, `engine`, `bcs`, `frontend`, `frontend-nextgen`,
  `gateway`, `arch`, `ci`, …). Outcome describes *what* the change accomplishes,
  not which files moved. No vague titles (`fix bug`, `sync`, bare branch name).
- **Description** sections, in order (`.github/pull_request_template.md`):
  `## Problem` / `## Solution` / `## Validation` /
  `## Compatibility and risk` (opt) / `## Spec` (opt) / `## Related issues` (opt).
  Delete optional sections that don't apply rather than leaving them empty.

### Pre-push hook

Install in every worktree: `devops/hook/install_git_hooks.sh` (sets
`core.hooksPath=.githooks`). Skip one command with `OCB_SKIP_GIT_HOOKS=1`.

`.githooks/pre-push` always scans added lines for plaintext credentials and
blocks the push on private keys, recognizable provider tokens (incl. Alibaba
Cloud AccessKey/STS), bearer/JWT, or high-entropy credential fields. Beyond
that it runs **lint-only by default** (fast Python SAST; `cargo` modules and
`apps/frontend` run nothing in lint-only mode). Full gates need:

```bash
OCB_PRE_PUSH_RUN_CI=1 git push
```

The hook models the PR change set against a merge target (default
`origin/dev`; the target must be `<remote>/<branch>` form). Override for one
push with `AVERNET_PRE_PUSH_MERGE_TARGET=origin/main git push`, or persistently
with `git config --worktree avernet.prePush.mergeTarget origin/main`. It refuses
to fall back to a stale target if the configured branch can't be fetched.

Module gates selected from the committed diff:

| Changed path            | Pre-push gate (full mode) |
| ----------------------- | ---------------------------------------- |
| `apps/backend/`         | SAST, unit tests, changed-line + singlebox coverage |
| `apps/baas/`            | SAST, unit tests, changed-line + singlebox coverage |
| `engine/adapter/`       | SAST, unit tests, changed-line coverage |
| `apps/bcs/`             | BCS unit tests (fast-fail), then unified singlebox coverage w/ E2E |
| `apps/frontend/`        | Frontend CI |
| `apps/proxy/`           | sandbox-proxy lint, unit tests, changed-line coverage |
| singlebox + Backend/BaaS acceptance or E2E | singlebox coverage |

## Editing discipline

- Do **not** format existing code as a side effect of a focused change — no
  `cargo fmt`, `prettier`, `ruff format`, or IDE format-on-save unless asked.
  Keep whitespace/style edits to the lines that must change. If a formatter
  rewrites unrelated lines, revert that noise before committing.
- Don't add features that weren't requested; no speculative abstraction or
  configurability; no refactors of unrelated code. Match local style in the
  files you touch. Remove only dead imports/vars/helpers caused by your own
  change.
- Propagate DB / persistence write failures as errors — never swallow a failed
  write and return success.
- **Python type contracts:** use `T | None` only when `None` is an
  intentional, valid domain state or at an external input boundary. Don't
  widen a required type to `T | None` for defensive programming, uncertain
  callers, test convenience, or a fallback that hides missing input.
- For BCS hot paths / loops / queues / recovery scans, estimate DB round trips
  and rows per op (including max batch size, concurrency, sustained failures);
  trace the actual store/cache impl — a repo call is not necessarily a DB
  query. Avoid N+1, fetching full rows to count, repeated scans of growing
  queues; prefer bounded pagination, aggregate queries, chunked batch writes.
  Hold locks only briefly; don't hold DB transactions/locks across external
  network delivery.
- Frontend uses semantic shadcn tokens built on the canonical primary
  `#2563eb` → `--primary` / `--brand` (see `apps/frontend-nextgen/src/global.css`).
  Prefer `bg-primary` / `text-primary-foreground` / `bg-card` / `bg-muted` /
  `border-input` over raw hex or `text-white` / `bg-white` as a primary surface.

## Testing rules (pick by risk)

- Narrow impl change → closest unit tests.
- Contract change → update + run the relevant conformance tests.
- Cross-module behavior change → all affected module tests.
- Architecture boundary change → architecture tests + update docs.
- BCS Rust changes → read `apps/bcs/AGENTS.md` first, then the relevant Cargo
  tests.

If you cannot run a required test, state exactly what was not run and why.

## File hygiene

Do not commit secrets/tokens/cookies/private keys, local databases, runtime
state, generated logs, cache dirs, machine-specific agent config, private
endpoints, or private package indexes. Open-source defaults must be
reproducible from public dependencies or clearly marked as TODO.

## Where new knowledge belongs

- Stable shared vocabulary → update `CONTEXT.md`.
- Hard-to-reverse system-level decision → new ADR under `docs/adr/`.
- Implementation work scoped to one module → `apps/<m>/specs/YYYY-MM-DD-<topic>/`.
- Cross-module contract or plan → `docs/specs/YYYY-MM-DD-<topic>/`.
- Research / diagnosis / review evidence → beside the consuming spec as
  `research.md`, or `docs/research/` if it serves multiple specs.

Specs and research are inputs to a decision; they do not override an accepted
ADR or the architecture rules.
