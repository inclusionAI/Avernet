# Runtime schema error handling — 2026-10-27

## Approved follow-up

Remove sync-table schema preflight entirely, not merely when capture is disabled.
The capture lifecycle opens no DB session and probes no table existence, columns,
primary key or history during startup or first library use. As of the 2026-10-27
startup decoupling below, it performs no configuration-format or capture/delivery
validation at startup either. Producers still require enabled capture at runtime.
Constructor parameters remain compatible with existing callers.

Actual enabled-capture SQL is unchanged: database errors propagate in the original
caller transaction, or are handled by existing task retry boundaries. No fallback,
automatic table creation, successful acknowledgement or history rewriting is
introduced. Ordinary capture-disabled source writes do not touch the outbox.
This supersedes the intermediate schema-check behavior recorded in
`history-availability-verification.md` without changing its historical results.

## 2026-10-27 startup decoupling

Configuration-format and capture/delivery consistency checks no longer block boot
either. A malformed `space_knowledge_capture` block fails closed to disabled
capture with a warning instead of raising at composition time; capture-disabled
deployments with an enabled sink start normally, and the full FastAPI lifespan
serves traffic while the background membership scan logs its real runtime errors.
Producer operations still reject disabled capture at runtime, and delivery without
recording never fabricates snapshots. No SQL error is swallowed, no failed
transaction is force-committed, and no disabled sink is treated as successful
synchronization.

Evidence for this round, same environment as below:

- Focused lifecycle/configuration/HTTP run: **46 passed**, covering malformed
  configuration fail-closed boot, valid explicit enablement, disabled-capture
  startup with an enabled sink, and the full lifespan with delivery enabled and
  capture disabled.
- Full space_knowledge directory: **143 passed**. Architecture, boundary,
  protocol and Space persistence isolation: **389 passed**. Adjacent Space, Work
  Order and HTTP regression: **618 passed**. Counts overlap; do not add.
- Ruff check and format on all five changed Python files, plus
  `git diff --check`: **passed**.

## Evidence

Baseline: local intermediate fix `563b0936396a1e664e06e7db1c3b188c6a14d5fb`,
on release Avernet `1131e4af8c30e35cfa3b28effcdaba7fb2d2174c`.

- Before the follow-up implementation, the startup tests produced **7 failed,
  1 passed**: enabled missing-table, both modes' missing-column and wrong-PK,
  and both modes' forbidden startup session cases failed at preflight.
- After removing preflight, lifecycle tests initially produced **25 passed**.
- Final lifecycle + full FastAPI lifespan/HTTP run: **37 passed**. It covers
  enabled/disabled startup with missing/broken schema, normal HTTP reads, disabled
  ordinary writes, actual sync SQL errors, and member-write rollback after SQL
  failure. A dependency spy verifies capture startup opens no ORM session.
- Adjacent knowledge, Space, Work Order, HTTP and provider-contract run:
  **408 passed**. This includes the focused cases above; counts overlap.
- Architecture compliance, context boundaries, protocol mapping and Space
  persistence isolation: **23 passed**.
- Ruff check and format check on all three changed Python files, plus
  `git diff --check`: **passed**.

Tests ran under `DEPLOY_PROFILE=test`, with the checkout's source/package paths
on `PYTHONPATH`, using the existing Python 3.12 dependency environment. The
runtime failure cases use real temporary SQLite, not simulated SQL exceptions.
No test gates or failure thresholds were weakened.

## Explicit limits and trade-offs

Startup success no longer establishes knowledge-schema compatibility. A schema
problem that does not cause a SQL error (for example a different uniqueness
constraint that still accepts a query) is not proactively detected. SQLite
missing-table/column behavior is not proof of every MySQL/OceanBase failure mode.

An enabled instance may fail an affected Space/member transaction or retry a
background sync task when it actually uses bad schema. Configuration errors and
unrelated infrastructure bootstrap can still fail application startup. Existing
mixed-writer revocation gaps and task ownership are not changed.

These are local mechanism and adjacent-regression results, not remote-provider,
SSO/gateway, full repository, deployed fleet or unified Singlebox acceptance.
No push, PR, merge, deployment, DDL or target-database access was performed.
