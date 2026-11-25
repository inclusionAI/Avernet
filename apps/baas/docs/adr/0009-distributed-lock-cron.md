# ADR-0009: Distributed locks for singleton cron execution

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS runs as multiple replicas (mesh deployment in enterprise, several boxes
in self-hosted community). Background jobs — device TTL renewal, expiry
sweeps, publish retry sweeps — must execute exactly once per period cluster-
wide, but there is no leader-election infrastructure; the only shared state
is the database plus a shared cache in enterprise deployments.

## Decision

Wrap every scheduled task's body in an acquired distributed lock (via the
`distributed_lock` domain: `DistributedLockService` over
`core/repository/distributed_lock`), rather than electing a leader or running
per-replica and deduplicating downstream:

- a task instance acquires a **named lock with expiry** before doing work;
  e.g. the TTL timer defaults to `lock_name = "device_ttl_timer_lock"`,
  `lock_expire_seconds = 1750` — longer than the run period, shorter than
  two periods — and the lock name gets an environment suffix (`_pre`,
  `_prod`) so shared databases never cross-lease between environments;
- locks are renewed-or-released by the task runtime, so a crashed holder
  cannot block the next period forever;
- the enterprise lock backend rides the `cache` plugin (ZCache);
  community uses its storage-backed implementation — the task code is
  backend-agnostic because the lock goes through the service abstraction.

Trade-off vs leader election: per-lease locking cannot guarantee strict
once-per-period during clock skew or lease overlap — accepted, because these
sweeps are idempotent and per-attempt budgets bound any double-run.

## Consequences

**Positive:** no new infrastructure; exactly-one-executor behavior emerges
from one service call; environment isolation is encoded in lock names; works
identically in both editions through the plugin seam.

**Negative:** lock expiry tuning per task; a fast task finishing before lease
expiry still holds the lease (wasted exclusivity window); strict "at most
once" is unprovable under partition.

**Risks:** forgetting to acquire a lock in a new scheduled task would double-run
it cluster-wide — the `AppScheduler` task pattern (ADR-0014) is the guardrail
to copy.

## Evidence

- community: `src/secbaas/community/core/service/distributed_lock/` — `DistributedLockService`
- community: `src/secbaas/community/core/repository/distributed_lock/` — lock persistence
- community: `src/secbaas/community/core/service/scheduler/_tasks/_device_ttl_timer_task.py` — lock name/expiry/env-suffix pattern
- enterprise: `docs/distributed_lock_usage.md` — legacy usage notes (history)
- enterprise: `src/secbaas/enterprise/plugins/cache/real/` — ZCache-backed lock backend