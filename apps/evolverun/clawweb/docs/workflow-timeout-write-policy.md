# Workflow timeout write policy

## Who decides a timeout?

The executor decides **whether and when** a run times out. Clawweb decides only
**whether the reported failure can still be written to this run**. This is the
release contract for the internal run routes and `FlowRunRepository`, not a
statement about which version is deployed.

Neither first settlement nor duplicate acknowledgement requires Clawweb to
reconstruct a budget from `state_json.effectiveFlowTimeoutMinutes`,
`workflowSnapshot.flowTimeoutMinutes`, its legacy `timeoutMinutes`, or a default.
A missing, disabled or invalid budget is not a server veto. Clawweb does not
compare `timeoutSecs`, `ranForSecs`, `total_duration_ms` or `completed_at` with
`started_at` and its own clock. Missing or inconsistent timing evidence alone
does not disqualify an otherwise eligible write. On first timeout settlement
Clawweb records its own `completed_at` in whole seconds; this is the time of
the write, not proof that a deadline has passed.

## Terminal admission contract

The same admission and snapshot CAS apply to **all** failed/succeeded
completions, not only timeout reports. The caller supplies an intent; a rejected
intent is not silently converted to a successful no-op.

| Write | Result |
| --- | --- |
| Failed or succeeded `/completion` against an unsettled nonterminal run | The matching generation can settle once. A timeout reason (including JSON wrapped in `message`) only selects the server-recorded settlement timestamp; it does not authorize the write or prove expiry. |
| `/completion` against a terminal run | An identical settlement in the matching generation can be acknowledged with HTTP 200, using a **read-only SELECT**, not an UPDATE. A different status, result or explicitly supplied detail conflicts with HTTP 409. Failed rows with NULL `completed_at` are still terminal, not a supplement path. |
| `/fail-if-running` with a nonempty string `reason` | Requires the saved status to be exactly `running`, not `waiting` or `blocked`. The first matching snapshot gets `claimed: true`; all lost/ineligible claims get `claimed: false`. HTTP 200 alone does not mean that the caller won. |
| `/result-json` and `/started-at` | Deprecated, read-only at both route and direct repository entry points. Every existing run returns HTTP 409; missing 404, unavailable DB 503, read error 500. No result, start, completion timestamp or observer mutation, even for epoch-zero/running rows. |
| `/retry-failed` | Deprecated, read-only: HTTP 409 for every existing run, with instructions to use coordinated `retry-resume`. Missing run: 404; unavailable database: 503; read error: 500. No callback, node reset or run UPDATE. |
| `/status` with `failed` or `succeeded` | Not a settlement path: HTTP 409 for an existing run, 404 for a missing run. Rejection of a preliminary status mirror does not block an eligible `/completion`. |
| `reconcile-stale-running` | Requires an actually terminal parent whose saved status equals `flow_status` and whose epoch matches `retry_epoch`. A running parent reported as failed, a different terminal outcome or a stale generation returns HTTP 409, not a successful zero count. |
| Public `POST /runs/:flowId/abort` | Captures the original run epoch, CAS-cancels only that active, unsettled generation, then passes the same epoch to reconciliation. A competing settlement or retry returns 409 before node cleanup, flow-control release or audit. Read/write failures remain 500. |

### Snapshot, generation and replay

- **Atomic CAS.** Completion and watchdog writes match the selected run ID,
  raw status, start time, state, result, input, phase, completion time, counts,
  usage, duration and raw retry epoch in the database. Text comparisons are
  byte-exact, independent of column collation. Reading `state_json` for CAS does
  not interpret it as timeout policy. Any changed selected field loses admission.
- **Retry epoch.** Supplied `retry_epoch` must be a nonnegative safe integer and
  equal the saved epoch. Omission means **epoch 0**, never “any epoch”. Stored NULL
  is treated as legacy epoch 0 for admission but remains distinct from zero in
  the SQL fence. On a genuinely older schema without the column, only omitted
  or explicit zero is admissible; positive epochs cannot be verified and conflict.
  Only a missing-column error enables this fallback; other database errors throw.
  An epochless fallback re-probes the schema inside a transaction and holds its
  lock through SELECT/CAS/duplicate confirmation. A concurrent schema upgrade
  cannot insert a new generation between the fallback read and an unguarded write.
  SQLite uses `BEGIN IMMEDIATE`; MySQL holds the transaction's table metadata lock.
  If the column appeared before the lock, the re-probe restores epoch matching.
- **Replay identity.** A replay must match status and every explicitly supplied
  result/input/phase/count/duration/usage field. Result strings are byte-exact;
  sharing a `timeout`/`flow_timeout` category, duration-field shape or saved
  timeout phase is not enough. A direct watchdog reason and a different wrapped
  controller result conflict. Omitted fields preserve the stored value and make
  no new assertion; explicit NULL asserts/clears NULL. A regenerated
  `completed_at` is not replay identity: the winning timestamp is preserved.
  Readback after a lost CAS must additionally retain the originally selected
  start, state and raw epoch; it cannot reclassify a new attempt as a duplicate.
- **No terminal supplements.** Neither failure nor success completion may rewrite
  a terminal row, even if its timestamp is missing. Historical `completed` is also
  protected as a saved success spelling, not added as a new wire settlement status. Noncanonical saved statuses
  (uppercase, surrounding whitespace) are rejected, not normalized into eligibility.
  Routes require nonempty canonical lowercase statuses. Cancellation remains
  protected from completion and reopening.
- **Reopening.** `/status` is only a nonterminal state mirror. It cannot reopen
  any terminal row, including saved epoch NULL/0 or a schema without an epoch
  column. Explicit/manual retries use `retry-resume` to advance the generation;
  they must not sequence `/status running` then an epoch-zero completion. Mirrors
  of a nonterminal row require the matching epoch. Any rejected status write to
  an existing run is HTTP 409, not a false 404.
- **Terminal observers.** Only the winning failure settlement schedules the
  existing failure observer. Identical acknowledgements, lost claims and conflicts
  do not schedule it. This is not exactly-once delivery for external callbacks.
- **Final counts.** Late node increments cannot change a settled row: the SQL
  requires `completed_at IS NULL` and excludes terminal status variants. The
  winning completion owns final counts; a duplicate does not recount nodes after
  reconciliation.

### Public manual abort carries the original generation

The public web-abort handler uses a strict lightweight control snapshot that
actually selects `retry_epoch`; ordinary list rows do not contain that column.
It captures `run.retry_epoch` once and passes that original generation to both
`abort(flowId, reason?, retryEpoch?)` and the fourth reconcile argument. It does
not re-read a newer attempt and borrow its epoch. The public request body remains
`{ reason? }`; this is a server-side propagation fix, not a new client field.

Abort admission requires an active saved status and NULL `completed_at`. A single
UPDATE matches byte-exact run ID/status and the raw epoch (including distinct
NULL); terminal parents cannot be overwritten. If completion or retry wins after
the route read or the repository admission read, abort returns false and the
route responds 409 without changing nodes, releasing flow-control or writing an
abort event. Database failures throw and return 500, not false/conflict. Missing
parents remain 404 and unavailable repositories 503.

Direct repository omission still means legacy epoch zero only; positive attempts
must supply their captured epoch. Truly absent-column schemas retain zero-only
compatibility under the same locked re-probe used by other fenced writes. An
admitted positive-epoch abort now reconciles nodes, releases existing slots/queue
entries and records its normal audit event rather than committing cancellation
and then failing because reconciliation defaulted to epoch zero.

### Manual retry is explicit generation admission

`retry-resume` requires a positive safe-integer `retry_epoch` frozen as the original
attempt's epoch plus one. The single SQL UPDATE matches the preceding epoch, advances
it once, sets running and clears the old completion timestamp, preserving original
`started_at` and node history. A current-running same-epoch replay is acknowledged
without a second UPDATE. Stale or skipped generations conflict; the server never reads
a newer epoch and substitutes it as authorization. An epochless schema cannot safely
advance generations and fails closed rather than reopening epoch zero.

The actual Mind/TaskGuard `handleRetry` implementations select failed/blocked/waiting
nodes by default, but allow an explicit node ID to rerun a succeeded segment. An
explicit, locally authorized retry can therefore reopen success **in a new generation**.
It cannot undo user cancellation (`cancelled`/`canceled`, including ordinary case/space
variants). This policy is shared with Mind direct SQL; it does not grant ordinary
status or completion writers permission to reopen success. `retry-failed` has no
verified generation-bearing client contract and is deliberately deprecated instead
of reading the latest epoch and inventing a new retry intent.

### Deprecated result and start mutations cannot bypass settlement

`PUT /runs/:flowId/result-json` and `PUT /runs/:flowId/started-at` are no longer
mutation paths, independently of any supplied epoch. Valid requests receive 409
for an existing run with explicit deprecation guidance, 404 for a missing run,
503 without a configured repository, and 500 on read errors. Existing malformed
request validation remains 400. Their repositories `updateResultJson` and
`resetStartedAt` also perform only a lookup and return false; database errors
throw rather than becoming a harmless false. Direct SQL repository callers do
not retain a bypass around the routes. Neither path issues an UPDATE or schedules
an observer, and neither can invalidate an already accepted completion replay.

Only fenced completion owns `result_json`; node-level output remains in
`node_executions`. The current Mind and TaskGuard controllers already removed the
old node-result hook. `retry-resume` owns generation admission and preserves the
original `started_at`; it is not paired with a start reset. The earlier audited Mind
`handleRetry` had a `!runtime` reset call; its author removed it in the coordinated
follow-up. The latest Mind and TaskGuard controllers have no active reset call.
Engines must not restore that dependency or re-enable a destructive legacy
endpoint. A run's online/wall-clock policy belongs to its authorized
executor and must not be inferred from a successful start-time rewrite.

### Retry checkpoint is part of admission, not a later mirror

`PUT /runs/:flowId/retry-resume` accepts an optional `state_json` string together
with the captured next `retry_epoch`. When supplied, it must decode to an object
with explicit `retryEpoch` (TaskGuard) or `recoveryEpoch` (Mind) equal to the
wire epoch. At least one is required; when both are present they must be safe
integers with the same value. A bad/null/string-valued alias cannot fall back
to a valid sibling, and the server does not invent or rename either field. A supplied
`workflowId` must match the saved run exactly. Malformed JSON, null, arrays, other
non-object values or contradictory caller identity/epoch return **400** before
any write; the epoch cannot advance without the checkpoint.

The server locks the parent, validates its original epoch and checkpoints, and
performs **one UPDATE** in that transaction to advance the epoch, set running,
clear `completed_at`, and persist canonical `state_json` and any existing
`params_json._clawmind_state` together. The embedded representation retains its
string-versus-object form; other params fields are preserved. If no embedded
checkpoint exists, the server does not create one. Existing canonical/embedded
checkpoints must identify the original generation using the same alias rules
(absent checkpoint epoch is legacy zero), agree semantically when both exist,
and not contradict the saved workflow identity. Malformed or contradictory saved state conflicts with **409**;
no fields are partially updated. Database errors propagate as **500** and roll
back the entire operation. Missing run is **404**, unavailable database **503**.

Retry checkpoints retain the existing fanout boundary. They cannot replace or
create managed-child `_fanoutIdentity`/`_fanoutRevision` checkpoints; those require
their revision-CAS API. Saved `fanoutOrigin` is immutable and is preserved when
omitted. Existing authoritative `subworkflowFanouts` are retained; untrusted
incoming group maps cannot create, delete, rewind or replace groups. The same
trusted merged checkpoint is persisted to canonical and existing embedded state;
therefore stored bytes can differ from the raw supplied string when authority
fields must be restored or untrusted groups removed.

An exact running generation replay is a read-only admission acknowledgement,
not permission to restore a previously supplied checkpoint over later progress.
Supplied JSON shape, epoch and workflow identity are still validated. Callers
omitting `state_json` retain the epoch-only compatibility admission contract:
the saved canonical and embedded checkpoints remain untouched. Current state-bearing
Mind and TaskGuard clients should include their checkpoint; optionality is not a
promise that a cross-writer scanner can recover an unsynchronized old checkpoint. A state-bearing client
must not downgrade to an unfenced `/state` write after any rejection or old-server
response; absence of checkpoint support is a coordinated-upgrade failure, not
permission to reopen without a fence.

### State and phase mirrors are fenced too

The legacy mirror shapes now carry the original execution generation:

- `PUT /runs/:flowId/state`: `{ state_json: string, retry_epoch?: number }`.
- `PUT /runs/:flowId/phase`: `{ phase: string, retry_epoch?: number }`.

Omission remains compatible **only with epoch 0**, including a genuinely absent
column under a transaction-held schema lock. A supplied epoch must be a nonnegative
safe integer, and must equal the parent row; malformed epochs return 400. Positive
state mirrors must explicitly carry matching `retryEpoch` or `recoveryEpoch`
checkpoint fields; contradictory or missing positive-attempt identity returns 409.
Callers freeze this value from their execution state, not a read of the latest
shared epoch. These are mirrors, not retry admission or a fallback for rejected
`retry-resume`.

A parent lock covers admission and the mirror writes. Existing terminal rows
(including historical `completed`) or rows with `completed_at` set cannot be
changed. Only a matching-generation, exact same stored state string or phase can
receive a read-only 200. Existing generation/settlement conflicts return 409,
missing runs 404, and database failures 500 rather than a false 404. The `/phase`
mirror still requires a nonempty string; the separate completion/watchdog phase
contract below supports explicit null.

State mirrors use the existing trusted fanout merge. When params already contain
`_clawmind_state`, the mirror synchronizes that embedded checkpoint in the **same
transaction** using the trusted merged canonical state, preserving string/object
representation and unrelated params. Failure of either write rolls back both.
No embedded checkpoint is invented. This matters for a legacy dual-store run:
`retry1 → progress mirror1 → failed completion1 → retry2` must not be rejected
merely because a legitimate mirror updated only canonical state. It does not
permit rewriting contradictory original state during retry admission itself.

### Phase wire contract (completion and watchdog)

Both `/completion` and `/fail-if-running` map `current_phase` to repository `currentPhase` without coercion:

| Wire value | Write effect |
| --- | --- |
| Field omitted (TypeScript `undefined`) | Preserve the saved phase. |
| `null` | Explicitly clear the phase to SQL NULL. |
| String | Store that exact phase. |
| Other JSON type | HTTP 400, with no settlement. |

This prevents successful persistence from clearing a phase that the executor
subsequently checks during closeout. The watchdog no longer invents a `timeout`
phase for omitted or null input; engines may explicitly send that string. A preserved/explicit phase is fenced like
other settlement data; a replay cannot silently replace it.

### Node reconciliation and errors

`reconcileStaleRunning(flowId, flowStatus, customErrorText?, retryEpoch?)` keeps
its third custom-message argument, carried from optional wire `custom_error_text`.
Omission uses the default text, an empty string remains empty, and any supplied
non-string (including null) is HTTP 400 before repository admission. The fourth
argument is carried from wire `retry_epoch`. The repository locks and validates the parent inside a transaction
(`BEGIN IMMEDIATE` on SQLite, parent `FOR UPDATE` on MySQL/ZDAS), and the child
UPDATE also includes an exact parent-status/epoch EXISTS predicate. It cannot
reconcile a running parent as failed or mutate a newer retry's nodes.

Success returns a nonnegative safe-integer count. HTTP 200 with `reconciled: 0`
means the matching terminal parent was admitted and no running nodes changed.
Parent conflicts throw a typed conflict mapped to HTTP 409. Database read/write
or transaction failures and invalid counts are errors (HTTP 500), never count 0.
No changes are committed when the transaction fails. A missing parent also fails
admission with HTTP 409.

## Release gate and rollback

**This server version removes the former 4505/business-timeout guards as well
as admitting the epoch/checkpoint contract. Do not deploy the check-removal
version independently while a faulty old writer can still reach it.** In
particular, an old **120-minute** scanner can report a timeout against a run
whose frozen executor budget is longer or disabled. Once those guards are gone,
that wrong report can win the CAS, really persist `failed`, and schedule the
terminal observer. CAS prevents a second winner; it cannot tell that the first
winner was wrong. A shared signing key or a `botid` is not proof of run ownership.
Native-store writes and direct client callbacks do not pass through these routes.

The required coordinated release order is:

1. **Pause or isolate faulty old writers first.** Inventory every executor,
   scanner, old replica, recovery process and native-store writer able to reach
   the affected runs. Stop or isolate their write paths and drain or block queued,
   in-flight and retrying reports. Prove the isolation before serving new code.
   **If isolation cannot be established, do not independently deploy this
   check-removal server version.** Merely planning a later Bot upgrade is not safe.
2. **Deploy the matching server and schema contract while isolation remains in
   place.** Verify the actual writer-facing instances and existing migration 140
   epoch column, not only a compiled package. This change does not add an epoch
   migration or a runtime feature flag that can retain the removed budget checks.
3. **Upgrade and validate both Mind and legacy TaskGuard before restoring writes.**
   Use disposable runs and the exact release candidates. Verify frozen disabled/
   long budgets do not produce early reports, an expired run settles once, and
   manual retry advances epoch and checkpoint atomically. Exercise mismatched
   timing evidence, stale/omitted epochs, terminal rows, cancellation and concurrent
   replay. Inspect persisted rows and observer counts, not just HTTP success.
   Include all scanners and recovery paths, not only the interactive Bot process.
4. **Restore writes only after those checks pass.** Nginx emergency filtering is
   **off by default**; it is not automatically enabled by this patch and is not a
   substitute for verified writer isolation. Any ingress isolation used by operators
   must be separately provisioned and tested, including bypass/direct-write paths.
   There is no claimed built-in feature flag for this release sequence.
5. If a faulty writer is discovered afterward, isolate it and stop the rollout.
   Reverting the server can restore previous checks **for future writes only**.
   It cannot reopen an already failed run, recall a notification, or undo a
   native-store write or direct callback. Reconcile affected runs and downstream
   effects separately; do not replay completions or re-enable an old scanner as
   the rollback procedure.

[Rolling-release safety](rolling-release-safety.md) covers static assets and
shutdown; it does not establish writer isolation or certify this release gate.

## Source and local verification

Trace the write from [internal run routes](../public/modules/workflow/server/routes/internal/runs.ts)
through [the repository](../public/modules/workflow/server/repositories/flow-run-repository.ts),
[completion dispatch](../public/modules/workflow/server/repositories/flow-run-completion.ts)
and [timeout claim](../public/modules/workflow/server/repositories/flow-run-timeout-write.ts),
[shared settlement fence](../public/modules/workflow/server/repositories/flow-run-write-fence.ts),
[atomic retry checkpoint](../public/modules/workflow/server/repositories/flow-run-retry-checkpoint.ts)
[state/phase mirror fence](../public/modules/workflow/server/repositories/flow-run-mirror.ts)
and [node reconciliation](../public/modules/workflow/server/repositories/node-reconcile.ts).
Use the repository and HTTP suites at the candidate revision. A suite that still
expects a budget mismatch or early server clock to veto a write exercises the
superseded policy, not the release contract above.

From the repository root, install the app's public dependencies, then create an
isolated node-only test config. Its aliases resolve to the current checkout's
source, not another workspace's compiled packages. The config is temporary and
does not modify package symlinks or the default Vitest config:

```sh
cd apps/evolverun/clawweb
npm ci --no-audit --no-fund
cd public/modules/workflow
config_dir="$(mktemp -d)"
trap 'rm -rf "$config_dir"' EXIT
cat > "$config_dir/server-vitest.config.mjs" <<'JS'
import path from 'node:path';
const root = process.cwd(); // public/modules/workflow
const publicRoot = path.resolve(root, '../..');
export default {
  root,
  resolve: { alias: {
    '@avernet/clawweb-shared': `${publicRoot}/shared`,
    '@avernet/workflow': `${publicRoot}/modules/workflow`,
    '@avernet/clawevolve': `${publicRoot}/modules/clawevolve`,
    '@avernet/clawinsight': `${publicRoot}/modules/clawinsight`,
  } },
  test: {
    environment: 'node', globals: true, testTimeout: 15000, maxWorkers: 4,
    include: ['server/**/*.test.ts'], exclude: ['node_modules/**', 'dist/**'],
  },
};
JS
node ../../../node_modules/vitest/vitest.mjs run \
  --config "$config_dir/server-vitest.config.mjs" \
  server/repositories/__tests__ server/routes/internal/__tests__
```

For the focused regressions, replace the final two directory arguments with:

```sh
server/repositories/__tests__/timeout-write-policy.test.ts \
server/routes/internal/__tests__/timeout-write-policy.test.ts \
server/routes/internal/__tests__/legacy-timeout-bypass.test.ts \
server/repositories/__tests__/terminal-admission.test.ts \
server/routes/internal/__tests__/terminal-admission.test.ts \
server/repositories/__tests__/retry-admission.test.ts \
server/routes/internal/__tests__/retry-deprecation.test.ts \
server/repositories/__tests__/retry-checkpoint.test.ts \
server/routes/internal/__tests__/retry-checkpoint.test.ts \
server/repositories/__tests__/mirror-fence.test.ts
```

## Candidate verification — November 28, 2026

The new terminal-admission tests first reproduced 35 failures out of 52 cases
before the shared fence implementation. The epoch-zero terminal status-reopen
follow-up independently reproduced two failures before removing status reopening.
Before the bounded retry-deprecation/watchdog-phase follow-up, the source-alias
regression run passed **1,483 tests in 67 files**, with
no skipped tests: repository suites, internal-route suites and the valid
200/500 workflow-route logging paths. The two new terminal-admission suites
contribute **102** of those tests. A separate logging/redaction run passes
**191 tests in five files**; it overlaps with that route-logging suite and is not
an additional 191 unique tests.

The bounded follow-up first reproduced **54 failures out of 168 focused cases**,
then passed all **168** after the retry-deprecation, cancellation-validation and
watchdog phase changes. These focused cases overlap with the earlier total; final
regression evidence is recorded below after source stabilization. A further
reviewer-driven dual-connection schema-upgrade and historical `completed` regression
first reproduced **9 failures out of 121 cases**, then passed all **121** after the
transactional fallback and saved-terminal protections. The completed counter follow-up
separately reproduced two failures before adding the same terminal counter guard.

After those source changes, the full repository/internal-route plus workflow-route
logging run passes **1,560 tests in 70 files**, with no skipped tests. The workflow
package `tsc --noEmit` check also passes. The latest separate five-file logging run
is **191/191**; it overlaps the regression run. Independent review rechecked all six
dual-connection schema-race cases and the historical-completed protections.

The final reviewer found two older mutation paths still bypassing the new
settlement invariant: result-json could overwrite a terminal/new-generation result,
and started-at could rewrite start time and clear a terminal timestamp. Both route
and direct repository entry points are now deprecated read-only. Their new real
repository/HTTP suites first reproduced **46 failures out of 50 cases** before the
closure; final post-closure verification is recorded with the frozen candidate.

Every non-engine reconciliation caller, including web abort, must propagate the
exact generation admitted by its authoritative terminal CAS. Passing an omitted
(epoch-zero) argument for a positive-epoch parent is rejected, not a valid cleanup.
The repository/internal-route verification above is not proof that those external
callers, flow-control cleanup or audit-event sequences are migrated correctly.

The final bounded checkpoint/mirror/custom-error follow-up adds optional atomic
checkpoint admission (including explicit Mind/TaskGuard epoch aliases), fanout
authority preservation, generation-fenced mirrors and dual-store progress sync.
The first checkpoint suites reproduced **38 failures out of 48 cases**; alias and
fanout follow-ups reproduced **21 failures out of 86 cases**. The custom-error
wire regressions reproduced **8 failures out of 53 route cases** before fixing
third-argument forwarding. These are overlapping development-stage counts, not
extra unique passing tests.

After the final two deprecated-mutation closures, author verification passes
**1,743 tests in 75 files**:
all repository and internal-route suites plus `workflow-route-logging.test.ts`,
with no skipped/todo tests. This includes **50** new real repository/HTTP
read-only-deprecation cases (28 repository, 22 HTTP); the original accepted
completion still replays without UPDATE or observer after a rejected legacy write.
The standalone mirror suite contributes **39** cases, including epoch-zero/
missing-column compatibility, terminal read-only acknowledgement, late generation
rejection, canonical/embedded rollback, and a second retry after progress. A
separate five-file logging run is **191/191** (overlapping), and workflow
`tsc --noEmit` passes. The unchanged independently authored crossrepo runner also
passes **86/86**, including nine final deprecated-hook integration regressions.
These runs do **not** cover the entire `server/**/*.test.ts` tree or full OCB;
those are independent mainline gates, not implicitly green. Independent checkpoint
review exercised additional concurrent/replay/fanout cases. These 1,743 cases
preceded the final public web-abort compatibility follow-up, not its acceptance.

That final bounded web-abort follow-up first reproduced **30 failures out of 48
new repository/real-HTTP cases**, then passed **48/48**. The final aggregate passes
**1,832 tests in 79 files**, no skips/todos: repository/internal routes, the logging
fixture, and the relevant public abort/fanout/console suites. It covers positive
epoch cleanup and audit, original-generation propagation, competing completion
and retry rejection, raw-NULL CAS, true missing-column zero compatibility and DB
errors. Workflow `tsc --noEmit` also passes; the unchanged crossrepo runner remains
**86/86**. These are overlapping results, not an additive count. This is not a
rerun of the entire server tree or full OCB, nor production rollout approval.

These checks use the candidate checkout's TypeScript sources, local SQLite and
loopback HTTP. They do not establish a full app build, live MySQL/ZDAS behavior,
production readiness or writer isolation. Historical verification below remains
separate and does not describe this candidate's semantics.

## Historical audit — superseded server-side policy

Earlier deployment audits described a controller that could send a best-effort
`/status failed` followed by a wrapped timeout `/completion`. The former server
policy checked the saved budget, the scanner's timing evidence and server wall
time, including rounded deadline checks on duplicates. Those observations and
the local test results below describe the earlier version only. They neither
establish the current writer inventory nor satisfy the release gate above.

Local test-first evidence on November 26, 2026: the initial policy suites had
68 failures before implementation. Later regressions independently reproduced
six failures for noncanonical status bypass and missing first-settlement
observation. After the fixes, all **1,027 repository/internal-route tests** passed
(63 files), including **110 focused timeout/bypass tests**. Those observer assertions
waited for `setImmediate`, including rejected-write checks.

The bounded persisted-status/fractional-deadline follow-up reproduced **93
failures** before its implementation; all **178 repository timeout-policy tests**
then passed, as did the source-alias server typecheck. Those tests included real SQLite
NOCASE/BINARY status cases, byte-only status races, rounded duplicate timestamps,
and premature-write rejection. MySQL/ZDAS binary predicate generation was asserted;
those SQLite runs did not certify a live MySQL deployment.

Those focused source tests did not replace the app's
[canonical CI build/check/test sequence](../scripts/ci/README.md). Final local
verification on November 26, 2026 used Node.js 20.19.0 and fresh public
`npm ci` dependencies in an isolated checkout of the earlier policy change:

- `npm run ci:build` and `npm run ci:check` passed for all five packages after
  the final counter guard; runner unit tests passed **25/25**.
- The final source-alias repository/internal-route regression run, also including
  the changed console-format safety suite, passed **1,386 tests in 65 files**
  with no skips. It included the **42 SQL/HTTP counter cases**. Independent
  review also passed the original eight counter-whitespace repros, 60 helper
  probes, 374 repository policy cases, 39 HTTP policy/bypass cases and the
  source-alias server typecheck. Those executions overlapped and were not a sum of
  unique tests.
- The preceding full `test:ci` aggregate was **not green**: workflow reached its
  unchanged 600-second package budget, was killed, and produced no final package
  JSON/coverage; no full-workflow coverage was verified. The other packages
  recorded 1,466 clawevolve and 1,874 clawinsight passes, while shared had five
  failures and app had one temporary-directory cleanup failure.
- Four shared failures were caused by the initial isolated archive omitting
  adjacent tracked SQL fixtures, not by product baseline behavior. After
  restoring those tracked fixtures, a separate shared run passed **659 tests**
  with seven existing opt-in MySQL-lock skips. Its one earlier Git child-PID
  timing failure did not recur. A separate app run passed **497 tests in 28
  files** with no skips; its earlier `ENOTEMPTY` cleanup failure did not recur.
  Those reruns did not rewrite the failed aggregate or prove a clean-baseline
  failure. The audit did not verify live MySQL/ZDAS behavior.

The earlier identical 33 type diagnostics on the inherited patch and clean HEAD
were specific to a stale shared-installed dependency environment; the fresh
public dependency build/check above resolved that verification gap without a
production-code workaround. Local verification does not imply a merge,
deployment, or external native-store validation.
