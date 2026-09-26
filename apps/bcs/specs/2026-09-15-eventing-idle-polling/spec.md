# Eventing idle polling

Owner: BCS Event Store / Eventing. Status: implemented; validation recorded below.

## Problem

An idle fanout claim executes two SQL statements; an idle delivery claim executes
six. The workers repeat these transactions after the configured polling interval
(default 200 ms), regardless of whether the preceding claim found work.

## Boundaries and contracts

- [Architecture constitution](../../../../docs/arch/arch.rules.md), especially
  Rules 16, 24 and 25, governs the additive Plugin API change.
- [DB Plugin API](../../crates/plugin-api/bcs-db-api/CONTEXT.md) owns transaction
  execution controls. Event Store owns SQL and claim eligibility.
- [Eventing](../../crates/services/bcs-eventing/CONTEXT.md) owns worker scheduling;
  configuration and bootstrap own policy validation and injection.
- No accepted ADR is superseded. Event repository and public HTTP/Event payload
  contracts retain their existing semantics. No database migration is needed.

### DB Plugin API v1 extension

`DbStatement::with_transaction_stop_on_no_rows()` opts a plain transaction
`Execute` into early success. Plugins validate step options before opening a
transaction. The option is rejected for standalone operations, `Query`, and
`ExecuteChecked`; their existing semantics stay intact.

When the flagged Execute affects zero rows, commit the executed prefix and return
only that prefix's results, including the zero-row result. Do not run subsequent
SQL or resolve its parameter bindings. Earlier writes, if any, are committed.
When rows are affected, continue in the same transaction. SQL/binding failures
roll back earlier writes; commit failures must propagate as errors. Use changing
writes so SQLite and MySQL agree on affected rows.

The option lives alongside existing transaction-only statement bindings; the step
and result enums and `DbPlugin` method signatures remain source compatible.
MySQL and SQLite implementations must be upgraded with the consumer and pass the
shared conformance harness. A third-party plugin must implement the option before
using the updated Event Store. Existing unflagged calls retain full execution.

### Event Store

Only the initial fanout/delivery claim UPDATE opts in. A zero count returns an
empty collection before accessing later transaction results. A nonempty delivery
claim still runs all six steps atomically, preserving lease fencing, attempt
records, recovery, strict ordering and causal eligibility. A nonempty fanout
claim retains both steps.

### Idle scheduling

Two optional configuration fields independently bound the idle polling intervals:

- `eventing.fanout_idle_poll_max_interval_ms`
- `eventing.delivery_idle_poll_max_interval_ms`

Omission uses the corresponding base interval, preserving legacy polling cadence
and config serialization. Explicit ceilings must be between that base and 60,000
ms. A ceiling equal to the base disables backoff.

After an empty successful iteration, double the delay window up to the ceiling.
Jitter within the top quarter of the window without going below the base or above
the ceiling. After work or an error, reset to the base interval; errors are logged
and are not classified as empty success. Existing lease and delivery retry
policies are independent. Cancellation interrupts waits; claimed work completes
before shutdown as before. Retention keeps its fixed interval.

## Compatibility and rollout

Apply the transaction optimization first with existing polling configuration.
Enable larger idle ceilings only after measuring cold-event latency. Setting both
ceilings to 1000 ms can contribute up to two seconds of polling wait before
processing time. Setting ceilings to their bases restores fixed polling.
Rollback the BCS binary for correctness regressions; schema rollback is unnecessary.

Existing oversized Event Store and config source files were split by
responsibility. DB statement types and config types keep their root re-exports.
Every added or modified Rust source file must remain within 1000 lines.

## Validation

See [validation.md](validation.md) for completed checks and outstanding deployment
measurements. Required observations are executed SQL count, idle-claim ratio,
transaction duration, event delivery P95/P99, backlog, retries and lease recovery,
using identical environment, replica count and measurement windows.
