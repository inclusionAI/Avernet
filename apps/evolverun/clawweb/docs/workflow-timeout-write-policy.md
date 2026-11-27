# Workflow timeout write policy

## Ownership and scope

The workflow server owns this write policy at the internal run routes and
`FlowRunRepository`. It protects persisted `flow_runs` from legacy watchdog
writes; it does not change the engine generation protocol or establish a trusted
owner identity. The signature uses a global key; a `botid` in the body/header is
not trustworthy proof of run ownership. This fence is policy/evidence validation,
not authorization against a malicious writer that can also change saved state.

## Contract

- Failed generic `/completion` with parsed `result_json.reason` equal to
  `flow_timeout` or `timeout` is a watchdog write. The deployed controller's
  `buildFailedResultJson` wrapper, whose `message` is the original JSON string,
  is recognized as well. `/fail-if-running` is always a watchdog write, even
  when its reason is unclassified.
- `state_json.effectiveFlowTimeoutMinutes` is the saved run budget. A present
  zero (disabled) or invalid value rejects the timeout and **never** falls back.
  Only a missing saved field may use provable
  `state.workflowSnapshot.flowTimeoutMinutes`, or its legacy `timeoutMinutes`.
  An absent/unprovable legacy budget fails closed. No global default is guessed.
- Server wall elapsed (`Date.now() / 1000 - started_at`) must meet the budget.
  Evidence must contain numeric `timeoutSecs` equal to saved minutes times 60,
  and finite `ranForSecs` at least that budget but no greater than wall elapsed.
  A recovered active elapsed may be lower than wall elapsed. Optional
  `total_duration_ms` must be finite, nonnegative, and no greater than wall time.
  Caller `completed_at` cannot prove elapsed time: accepted timeout completion
  timestamps come from the server, floored to whole seconds. First-write wall
  validation remains unrounded, including fractional-minute budgets.
- The policy read is followed by one atomic conditional SQL update matching
  status, started time, byte-exact state, result, completion time and the raw
  retry epoch (including NULL versus zero). Concurrent changes lose the fence.
  SQLite and MySQL/ZDAS use their own binary text comparison syntax. A legacy
  schema without a retry epoch retains no-epoch operation; no epoch is invented.
- Only one eligible nonterminal row transitions to failed. Generic duplicate
  timeout completion acknowledges a proven already-settled timeout without
  changing the row or notifying observers again; a dedicated duplicate watchdog
  claim returns `claimed: false`. A concurrent timeout winner may be acknowledged
  only if start, state and raw epoch remain the same. A duplicate's saved
  completion must reach the precise deadline or equal its exact floored second;
  arbitrary earlier fractional timestamps, earlier seconds and future timestamps
  are rejected. Current wall time and both incoming and saved timeout evidence
  must still prove the full budget. Whole-second storage cannot distinguish an
  earlier instant within that one deadline second; this is acknowledgement-only
  precision tolerance, not permission for an early write or another notification.
  Rejections do not notify.
- Watchdogs reject any noncanonical saved status (not exactly nonempty
  `status.trim().toLowerCase()`), including legacy terminal and running variants;
  they never normalize a saved status into eligibility. Raw snapshot CAS is unchanged.
- Succeeded, cancelled and canceled rows cannot be overwritten by watchdog or
  plain failed completions, including saved case/whitespace variants. Plain failed
  completion classifies saved status with JavaScript `trim().toLowerCase()` so
  tabs, newlines and Unicode trim whitespace have the same meaning on SQLite and
  MySQL/ZDAS; it does not depend on SQL `TRIM`, `LOWER` or column collation. Both
  its settlement and supplement updates atomically match the raw selected status
  (`status COLLATE BINARY = ?` on SQLite, `BINARY status = BINARY ?` on MySQL/ZDAS),
  rejecting a concurrent spelling/status change rather than relying on a racy
  preflight check alone. Plain non-timeout failure completion may settle a
  nonterminal run or supplement failed details, retaining the existing optional
  epoch guards. Normalized saved `failed` variants use the same first-settlement,
  supplement and epoch rules: an explicit positive epoch cannot supplement an
  already-settled failed row, regardless of its saved spelling. A failed row with
  no completion timestamp receives its first
  settlement and observer once; later failed-detail supplements are not another
  terminal transition. Other status/retry behavior is unchanged.
- Node-count increments require `completed_at IS NULL` and reject canonical
  succeeded/failed/cancelled/canceled statuses (also case and ordinary-space
  variants) in the same SQL update. Settled
  counts cannot be changed by late node events, even if a legacy saved status
  contains tabs, newlines or Unicode whitespace, or still says running. An
  explicit supported reopen to running clears the old completion timestamp;
  cancellation remains protected. This keeps accepted final counts stable for
  strict closeout readback, rather than relaxing completion confirmation.
- `/status` and `/completion` require nonempty, lowercase status strings without
  surrounding whitespace. Noncanonical variants such as `FAILED` or `failed `
  are rejected rather than bypassing policy or relying on database collation.

## Compatibility and residual limits

`/status failed` is rejected for every existing row (HTTP 409; missing row 404).
Old 0.5 watchdogs may call `failFlowInTaskFlow` →
`DatabaseTaskFlowAdapter.fail` → `apiRepo.updateStatus('failed')` before generic
completion, so allowing nonterminal-to-failed status writes would bypass the
fence. The adapter's status call is best-effort; normal controller completion
settlement remains supported, as do failed-detail supplements and phase writes.

A plain `/completion` failure with no recognizable timeout evidence cannot be
classified as a watchdog. Such failures remain compatible on nonterminal/failed
rows. This server cannot stop an external native store's `fail` call or an old
client's direct HTTP callbacks; it only fences writes and observer notifications
at these server entry points. It cannot undo earlier external effects.

[Rolling-release safety](rolling-release-safety.md) describes delivery of static
assets and shutdown only, not this timeout policy. Deployment requires upgrading
the actual write-serving instances; local tests do not certify a deployed fence
or force old clients to stop external side effects.

## Source and verification

Implementation: `public/modules/workflow/server/repositories/flow-run-timeout-write.ts`,
`flow-run-completion.ts`, `flow-run-repository.ts`, and `routes/internal/runs.ts`.
Real SQLite and HTTP suites cover saved/legacy budgets, wrapped watchdog evidence,
exact CAS races, terminal protection, and asynchronous observer idempotence.

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
server/routes/internal/__tests__/legacy-timeout-bypass.test.ts
```

Local test-first evidence on November 26, 2026: the initial policy suites had
68 failures before implementation. Later regressions independently reproduced
six failures for noncanonical status bypass and missing first-settlement
observation. After the fixes, all **1,027 repository/internal-route tests** passed
(63 files), including **110 focused timeout/bypass tests**. Observer assertions
wait for `setImmediate`, including rejected-write checks.

The bounded persisted-status/fractional-deadline follow-up reproduced **93
failures** before its implementation; all **178 repository timeout-policy tests**
then passed, as did the source-alias server typecheck. These include real SQLite
NOCASE/BINARY status cases, byte-only status races, rounded duplicate timestamps,
and premature-write rejection. MySQL/ZDAS binary predicate generation is asserted;
these SQLite runs do not certify a live MySQL deployment.

These focused source tests do not replace the app's
[canonical CI build/check/test sequence](../scripts/ci/README.md). Final local
verification on November 26, 2026 used Node.js 20.19.0 and fresh public
`npm ci` dependencies in an isolated checkout containing the target branch plus
this patch:

- `npm run ci:build` and `npm run ci:check` passed for all five packages after
  the final counter guard; runner unit tests passed **25/25**.
- The final source-alias repository/internal-route regression run, also including
  the changed console-format safety suite, passed **1,386 tests in 65 files**
  with no skips. It includes the **42 SQL/HTTP counter cases**. Independent
  review also passed the original eight counter-whitespace repros, 60 helper
  probes, 374 repository policy cases, 39 HTTP policy/bypass cases and the
  source-alias server typecheck. These executions overlap and are not a sum of
  unique tests.
- The preceding full `test:ci` aggregate was **not green**: workflow reached its
  unchanged 600-second package budget, was killed, and produced no final package
  JSON/coverage; no full-workflow coverage claim is made. The other packages
  recorded 1,466 clawevolve and 1,874 clawinsight passes, while shared had five
  failures and app had one temporary-directory cleanup failure.
- Four shared failures were caused by the initial isolated archive omitting
  adjacent tracked SQL fixtures, not by product baseline behavior. After
  restoring those tracked fixtures, a separate shared run passed **659 tests**
  with seven existing opt-in MySQL-lock skips. Its one earlier Git child-PID
  timing failure did not recur. A separate app run passed **497 tests in 28
  files** with no skips; its earlier `ENOTEMPTY` cleanup failure did not recur.
  Those reruns do not rewrite the failed aggregate or prove a clean-baseline
  failure. Live MySQL/ZDAS behavior remains unverified.

The earlier identical 33 type diagnostics on the inherited patch and clean HEAD
were specific to a stale shared-installed dependency environment; the fresh
public dependency build/check above resolves that verification gap without a
production-code workaround. Local verification does not imply a merge,
deployment, or external native-store validation.
