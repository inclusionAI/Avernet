# Complete release audit call timing

## Scope

Record one completion timing log for every `record_created()` and `record_transition()` invocation, including success, failure and skipped/no-op paths. Measure with monotonic time from entry through audit transaction commit/rollback and session exit. Keep public return values, native publication status/transaction boundaries, Plugin selection and audit correlation fences unchanged; the cleanup adjustment below is explicitly included by user steering. Do not include raw ext, credentials or exception messages in these logs.

## Design

The internal observer reports success/failed/skipped only after its transaction exits. Internal transition completion reports whether an audit operation actually applied. Each entry logs its outcome and elapsed_ms in finally; logging failures are best effort and must not affect the caller. Preserve existing diagnostics. Add no new database operations, transactions, retries or schema fields.

## Verification

Use real SQLite audit fixtures and a deterministic monotonic clock advanced during transaction exit to prove commit and commit-failure duration is included. Cover Plugin failures, no-actor and stale/non-terminal/duplicate skips, and timing logger failure isolation. Run adjacent audit/Plugin/DI tests, static checks and Backend CI. Rebase on remote REL20261127 and publish a single-commit PR; no OCB worktree/gitlink changes.

## User steering: audit checks must not suppress native cleanup

Restore the original unconditional native retry cleanup and dict-guarded restarting cleanup. Neither audit ID nor workflow comparison may suppress those business operations. Keep change/workflow checks only on audit marker writes to avoid recording another attempt's result. This intentionally preserves pre-audit cleanup semantics, including their concurrent-retry behavior; broader native concurrency changes are outside this task. Reuse the original ext read, adding no DB calls, transactions or locks. Verify cleanup still runs when IDs/workflows change while newer audit markers remain untouched.
