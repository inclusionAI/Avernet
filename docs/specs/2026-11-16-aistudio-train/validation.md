# AIStudio training node — implementation validation


## Latest-dev port checkpoint — 2026-11-17

Target: `codex/aistudio-train-pr`, based on `origin/dev` at
`71163c755a282612202bab7c9dcf9a57fbde7356`. The approved patch was selectively
ported, not transplanted as complete old versions of existing files. The old
source worktree and Engine were not modified; no commit, push, PR, deployment or
real training operation was performed here. This port awaits independent review;
prior source approval is historical evidence, not approval of this target.

### Migration ownership and upstream preservation

The complete central registry extends through 154. Upstream 147/148 remain
flow-run-link migrations, 149 remains retired, and 150–154 retain their ordinary
request, run-marker, AI-draft and Bot-diagnosis behavior. Fresh central **155**
creates `aistudio_train_watches`; **156** creates
`aistudio_train_manual_audits`. Engine versions remain **41/42**. The feature
schema, tests, spec and usage guide use 155/156.

Bootstrap preserves unified-save-before-body-parser ordering, Pack guards,
internal engine authentication and subworkflow routes. Training public IAM
still fails closed without explicit verified-principal and exact ACL providers.
NodeExecutionList preserves subworkflow rendering and suppresses manual training
controls in read-only previews, covered by one new regression test. The legacy
`internal/__tests__/events.test.ts` was separately included: failed or duplicate,
unconfirmed inserts must return 503 rather than a positive persistence ACK.

Scope: the original 39 feature paths, the authorized legacy ACK test, a 17-line
`shared/server/db/migrations-registry.ts`, and five narrow upstream regression
updates that read the latest version from the complete registry. The registry
split keeps `db.ts` at 995 lines. Registrations are collected afresh per run;
upstream dynamic test registration, deferred DDL, supported dialects, concurrent
ledger handling and trigger checks are preserved. Managed fixtures retain all
upstream assertions and explicitly account for the new DDL/ledger entries.
No unrelated Pack assertions or generated `public/public` were copied.

### Dependency isolation

All five `@avernet/*` links point into this PR worktree. Third-party installations
are reused read-only; output and caches are local to this worktree. Initial links
inherited from the old source pointed to a removed worktree and were repaired
only here. The already-declared `unbash@6.0.4` was linked from an existing
installation. No manifest, lockfile or dependency version changed. The final
production build regenerated all public workspace output locally.

### Executed commands and results

Run from `apps/evolverun/clawweb` unless marked repository root. Logs and the
worktree-rooted temporary test/type configs are in `/tmp/aistudio-train-pr-port/`.
Selections overlap; their counts are not additional unique cases.

| Command | Result | Log |
| --- | --- | --- |
| `npm run build` | All five public workspaces passed; existing chunk-size warning | `build.log` |
| `npm run check` | All five no-emit workspace checks and public credential-boundary gate passed | `workspace-types.log` |
| `npm run test --workspace @avernet/workflow -- aistudio-train` | 107/107, 8 files | `canonical-workflow.log` |
| `npm run test --workspace @avernet/clawweb-shared -- aistudio-train` | 14/14, 3 files | `canonical-shared.log` |
| `npm run test --workspace @avernet/clawweb -- aistudio-train` | 3/3, 2 files | `canonical-host.log` |
| `./node_modules/.bin/vitest run --config /tmp/aistudio-train-pr-port/backend.config.mjs` | 92/92, 13 files, including legacy ACK | `backend.log` |
| `./node_modules/.bin/vitest run --config /tmp/aistudio-train-pr-port/migrations.config.mjs` | 74/74: 62 managed + 12 snapshot cases | `migrations.log` |
| `npm run test --workspace @avernet/clawweb-shared -- migration sqlite-trigger ordinary-concurrent sqlite-incompatible` | 232/232, 13 files | `adjacent-migrations.log` |
| `npm run test --workspace @avernet/workflow -- server/routes/internal/__tests__/events.test.ts` | 3/3 | `legacy-event-ack.log` |
| `npm run test --workspace @avernet/clawweb -- aistudio-train public-unified-save-host public-run-subworkflows public-internal-engine public-pack-security` | 21/21, 6 files | `host-boundary-regressions.log` |

The lowercase filter does not select both component filenames. They were also
run explicitly through the canonical workflow configuration:

```sh
npm run test --workspace @avernet/workflow -- \
  web/api/__tests__/aistudio-train.test.tsx \
  web/components/__tests__/AIStudioTrainWatchCard.test.tsx \
  web/components/__tests__/NodeExecutionListAIStudioTrain.test.tsx \
  web/components/subworkflows/__tests__/SubworkflowPanel.test.tsx
```

Result: **75/75** (62 training API/component cases plus 13 upstream subworkflow
cases); `ui-canonical-and-subworkflow.log`. The separate source-alias UI run
also passed 62/62 (`ui.log`). The prior 61 training cases remain, plus the new
read-only case.

Additional no-emit checks all passed with these commands:

```sh
./node_modules/.bin/tsc --noEmit -p /tmp/aistudio-train-pr-port/server.tsconfig.json
./node_modules/.bin/tsc --noEmit -p /tmp/aistudio-train-pr-port/host-tests.tsconfig.json
./node_modules/.bin/tsc --noEmit -p /tmp/aistudio-train-pr-port/shared-tests.tsconfig.json
./node_modules/.bin/tsc --noEmit -p /tmp/aistudio-train-pr-port/workflow-server-tests.tsconfig.json
./node_modules/.bin/tsc --noEmit -p /tmp/aistudio-train-pr-port/ui-tests.tsconfig.json
```

Corresponding logs: `server-source-types.log`, `host-tests-types.log`,
`shared-tests-types.log`, `workflow-server-tests-types.log`, `ui-tests-types.log`.
The focused source check is strict. Test checks use their owning workspace
settings (host/shared strict; workflow non-strict), correct ambient declarations
and package identities. No repository compiler gate was relaxed. Preliminary
mixed ad-hoc configs failed due to incompatible settings, absent ambient types
and duplicate source/dist identities; those attempts are not reported as passes.
The five existing version-only regression adjustments were executed in the
232-case run, not included in the focused test type selection.

### Hygiene and limits

- All 43 added/touched TypeScript sources/tests are under 1,000 lines; the largest
  is `db.ts` at 995. `git diff --check` passed. The full 46-path inventory and
  source hashes/line counts are in the local artifact directory.
- The repository's latest `devops/hook/check_secrets.py` passed against all added
  feature/integration lines, using a candidate tree with a temporary
  `GIT_INDEX_FILE`. Command (repository root):
  `python3 devops/hook/check_secrets.py --base 71163c755a282612202bab7c9dcf9a57fbde7356 --head <candidate-tree>`.
  Log: `secrets.log`. The real index stays unstaged; the tree is not a commit.
  No scanner modification, waiver or hook bypass was used. `.githooks/pre-push`
  remains configured and executable.
- No skipped/only tests, placeholder TODOs, private endpoints, credentials,
  production runtime paths, dependency changes or generated output were added.
- The entire ClawWeb suite and full repository suite were **not** rerun on this
  base. Prior full-suite failures below are historical old-base evidence, not
  a green result or a new failure baseline for latest dev.
- Engine/full cross-repository HTTP verification was not rerun in this Web port.
  No live IAM integration, MySQL/ZDAS execution, production schema rollout or
  real a6o task was exercised. Standalone MCP remains explicitly unavailable
  without the durable recovery capability required by the existing contract.

## Historical pre-port source-worktree checkpoint

Everything below records earlier implementation and old-base verification.
Its independent approvals and full-suite comparisons do not describe or approve
the latest-dev port above.

Started: 2026-11-16. Final local verification: 2026-11-17 (Asia/Shanghai).
Branch in both working copies: `codex/aistudio-train`.
Status: scoped implementation and local verification complete; independent
source/test review approved. This is not a production or real-service approval.

## Scope and preservation

The engine is implemented in the ClawMind repository; this repository contains
ClawWeb's signed storage adapter, authenticated public route composition and
RunDetail integration. The preexisting visual-editor, pack-script and taskguard
changes in this working tree are outside this feature. No commit, push, release,
production migration or real training task has been performed.

## Executed checks before final review fixes

- ClawMind feature/validation tests: 132 passed, none skipped.
- ClawWeb backend contracts, signature, routes, repositories, runtime/bootstrap
  and schema tests: 60 passed before follow-up regression cases were added.
- ClawWeb card/API/node-row tests: 49 passed before cache-race regression cases.
- Managed migration and workflow-snapshot compatibility: 37 passed.
- Targeted source and test TypeScript checks: passed.
- ClawWeb full workspace production build: passed; existing large-chunk warning.
- Two real cross-repository HTTP tests: passed, using ClawMind's ApiClient and
  ApiWatchStore against ClawWeb's real Ed25519 verifier, router, SQLite schema
  and repository. CLI replies and verified-principal providers are test doubles.

### Full engine regression baseline

The final frozen-source full `npm test` run reported **4,848 tests: 4,712
passed, 130 failed, 6 preexisting skipped**. An isolated `git archive HEAD`
baseline with the same dependencies, built first, reported 4,607 tests: 4,469
passed, 131 failed, one cancelled, 6 skipped. Every failing name in the final
feature run also failed on that unmodified baseline; no feature-only failure
names remained. The baseline had two extra unstable failure entries (timeout
cancellation and temporary Git-directory cleanup).

This comparison is evidence against new failures in that run, not a claim that
the full suite is green. Existing failures include workflow version recovery,
callback fixtures, compression modules and legacy bundle expectations. A run
started while the final lifecycle patch was still being authored observed one
intermediate replacement-test failure; that run was not accepted as final. Both
the final focused suite and the subsequent frozen-source full rerun passed that
case. The 76 feature source/test/manifest hashes remained unchanged through the
accepted final full-suite verification. The subsequent canonical host-test fix
changed only two test fixture import paths and added one host-local fixture;
production sources remained unchanged. Both canonical host tests and the
source-alias backend selection were then rerun successfully.

A separate callback/recovery regression selection reported 193/199 passing;
all six failing names were also present in the unmodified full baseline.

### Full ClawWeb workflow regression baseline

The final canonical workflow workspace test command reported 1,990 tests:
1,948 passed, 42 failed across four files (an earlier run was 1,926/1,968). A separately archived and built unmodified HEAD
reported 1,422 tests: 1,383 passed, 39 failed. All 39 automation/daily-draft
failures recur in that baseline. Three additional failures are in preexisting
dirty pack-script/Git tests outside this feature: linked-pack rejection, an
annotated tag accidentally following a push, and temporary directory cleanup
(the exact case affected by cleanup varied between the two runs).
They have not been repaired or relabeled as passing. This is not a green full
workspace result; training-specific checks and their final reruns are recorded
separately. The baseline's package links and build output are isolated from the
working tree.

## Final Web verification

- Backend contracts/routes/signature/runtime/bootstrap/schema and reserved-event
  writer checks: **92/92 passed**.
- Frontend API/card/NodeExecutionList: **61/61 passed**.
- Managed migration and workflow snapshot compatibility: **37/37 passed**.
- Targeted source/test TypeScript and all production workspace builds: passed.
- Independent Web reviewer: PASS, 179 formal tests plus 18 independent cases.
- Canonical package test entry points after production workspace builds:
  workflow **107/107**, shared **14/14**, and host application **3/3** passed.
  These selections overlap the backend/UI totals; do not add them as unique cases.
- The host tests use a host-local fixture with the production `WatchRecord` type,
  not an unbuilt workflow `__tests__` package subpath. Package exports, published
  files and test assertions were not relaxed. The source-alias backend rerun
  also passed **92/92** after this test-boundary correction.
- Expanded real cross-repository HTTP proof: **2/2 passed**. It uses the actual
  ClawWeb better-sqlite3 adapter and ClawMind's real manual controller/event API
  repository; a refused audit retains actor/mailbox and leaves no marker, then a
  successful retry and 1001 unrelated events still yield one exact audit record.

## Final engine checks after authoring completed

- Feature, controller/recovery, watcher/store, actual MCP startup/stop, OpenClaw
  shutdown, CLI/validation, SQLite isolation/migrator and nearby validation-command
  tests: **244/244 passed**, with no skipped tests.
- Existing env/tag migration compatibility, updated to run through v41/v42:
  **12/12 passed**, with v40 idempotence and nullable/error behavior retained.
- Full ClawMind build and TypeScript check: passed.
- The new platform training, SQLite isolation and env/tag compatibility files are
  included in the standard package test discovery, not only hand-run commands.

## Independent failure-injection review

Reviewers found additional cases beyond initially green tests. The fixes below
are implemented and covered by targeted regression tests:

- Deleted-run orphan records could poison a due batch or starve a live watch.
- Delayed manual-command snapshots could overwrite newer nonterminal GET data.
- Interrupted submission could be automatically retried as a new attempt.
- Redundant engine-local HTTP controls did not meet public user auth boundaries.
- Manual audit acknowledgement and pagination-based deduplication were unsafe.
- Lease expiration needed atomic database-clock fencing, not read-then-write.
- Controller continuation needed normal validation, rerun, branch finalization
  and flow-control release semantics.
- Shutdown/re-registration needed to drain accepted submissions and preserve
  the old runtime's event persistence target until its work finished.

## Final independent verdict and hygiene

- Independent engine/controller/runtime/MCP/OpenClaw review: **APPROVE**, with
  no unresolved confident P1/P2 or CRITICAL/HIGH finding within this scope.
  The reviewer ran 249 engine cases, 179 Web cases, the 2 cross-repository HTTP
  cases, retained failure-injection proofs and no-emit TypeScript checks. These
  overlap the mainline checks and are not additional unique case counts.
- The final host-local fixture change preserves all assertions and does not
  expose test internals through production package exports.
- Both repositories passed `git diff --check`. No new TODO placeholder,
  `test.skip` or `test.only` was added in the feature files. The existing MCP
  TaskFlowAdapter TODO is unchanged from HEAD and is not claimed implemented;
  standalone training remains explicitly unavailable without durable recovery.
- All added feature source modules and the touched ClawWeb source files are
  below 1,000 lines. Existing ClawMind controller/bootstrap/validation/type/tool
  files exceed that size and received narrow integration bridges only; their
  unrelated historical refactor is outside this change. The engine schema is
  1,000 lines, with new migrations kept in separate focused modules.
- No full-repository lint or live enterprise architecture/production gate is
  claimed by this local verification. Full test-suite failures remain recorded
  above. No commit, staging, push, deployment or real training call was made.

## Browser evidence

The local browser fixture mounts the actual production `NodeExecutionList` and
`AIStudioTrainWatchCard`, with only same-origin API responses replaced by local
fixtures. Clicking the training row expands its detail immediately below the row
in a nine-column cell. A manual 202 renders pending feedback in both row and
expanded card, keeps the node waiting and leaves downstream evaluation pending.
The fixture is not a substitute for the deployed RunDetail, IAM or AIS checks.

## Reproducing the focused checks

In ClawMind, build and run the focused selection (256 cases at this checkpoint):

```sh
npm run build
node --import tsx --test tests/aistudio-train-*.test.ts \
  tests/platform/aistudio-train*.test.ts tests/db/sqlite-database.test.ts \
  tests/db/sqlite-transaction-isolation.test.ts tests/db/migrator.test.ts \
  tests/db/flow-run-env-tag-migration.test.ts tests/validate-command.test.ts
```

In the ClawWeb workspace, build its package boundaries before canonical checks:

```sh
npm run build
npm run test --workspace @avernet/workflow -- aistudio-train
npm run test --workspace @avernet/clawweb-shared -- aistudio-train
npm run test --workspace @avernet/clawweb -- aistudio-train
```

Managed-migration/legacy-event ACK compatibility and source-alias type checks
were also run separately. Full workspace commands still have the explicitly
reported failures above; do not replace their gates with the focused selection.

## Remaining deployment checks

- Host-provided verified principal, exact Bot/Owner ACL and execute permission.
- Configured production Ed25519 keys and managed-schema rollout in both systems.
- Real MySQL/ZDAS execution, not only SQL-shape and SQLite tests.
- Installed a6o CLI, its credential mechanism and one controlled end-to-end run.
- Standalone MCP remains explicitly unavailable without the programmatic durable
  owner/revision-CAS recovery capability documented in ClawMind. It must not use
  an in-memory adapter or borrow the most recent tool caller's session.
