# ADR-0014: Background job model — scheduler tasks with per-bot concurrency

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Much of BaaS is time-driven rather than request-driven: keep device TTLs
renewed so long bot runs are not evicted, sweep expiring sandbox pods (destroy
Pod + stop bot), advance publish device attempts that timed out, recover
stuck bot runs, poll file-transfer completions. Jobs must be schedulable at
different periods, protected against multi-replica double execution
(ADR-0009), and bounded so one chatty bot cannot starve the fleet.

## Decision

Run background work as **declared scheduled tasks under one `AppScheduler`**,
plus a bounded per-bot execution model, rather than ad-hoc cron scripts or
unbounded worker pools:

- `core/service/scheduler/` exposes `AppScheduler` + `ScheduledTask`
  (protocol) and the registered task set:
  `DeviceTtlTimerTask` (renew active sandbox TTLs), `ExpireSandboxTimerTask`
  (destroy expired Pod, stop the bot), `PublishRetrySweepTask` (advance
  timed-out device attempts — partner of ADR-0005), `BotRunRecoveryTask`,
  `DeadlineRenewalScheduler` (deadline-extension bookkeeping), and
  `FileTransferPoller`;
- every task is a config-carrying class with `…TaskConfig` (period, lock
  name, budgets), declares its `DistributedLockService` lease
  (`device_ttl_timer_lock`, ADR-0009), and is started by bootstrap;
- periods and budgets live in `user_config` (`device_ttl_timer`,
  `expire_sandbox_timer`, `publish_retry_sweep`) so operators tune scheduling
  without redeploying;
- bot execution concurrency is per-bot bounded (`_bot_concurrency.py`,
  `user_config.bot_runner`), and queued runs persist as `bot_run_queue` /
  `bot_run_queue_chunk` repositories rather than in-memory queues, so
  restarts do not drop work;
- Arca TTL bookkeeping persists via the `arca_ttl` repository so renewal
  history is auditable.

## Consequences

**Positive:** one mental model for all time-driven work; adding a job follows
a copyable template (task class + config + lock + bootstrap registration);
persisted queues and TTL records survive restarts; noisy tenants are fenced
by per-bot limits.

**Negative:** the scheduler is a crash-restart design (missed periods are not
compensated beyond task-level sweeps); every job needs explicit config
field threading.

**Risks:** a task that grows beyond its lock expiry (see ADR-0009 tuning) or
blocks the shared scheduler loop; mitigated by per-task budgets and isolated
sweep tasks reviewed against their configs.

## Evidence

- community: `src/secbaas/community/core/service/scheduler/__init__.py` — `AppScheduler`, `ScheduledTask`, task classes
- community: `src/secbaas/community/core/service/scheduler/_tasks/{_device_ttl_timer_task,_expire_sandbox_timer_task,_publish_retry_sweep_task,_bot_run_recovery_task,_deadline_renewal_task,_file_transfer_poller}.py`
- community: `src/secbaas/community/core/service/bot_run/_bot_concurrency.py` — per-bot limits
- community: `src/secbaas/community/core/repository/{bot_run_queue,bot_run_queue_chunk,arca_ttl}/` — persisted queue/TTL state
- community: `configs/application.yaml` — `user_config.{device_ttl_timer,expire_sandbox_timer,publish_retry_sweep,bot_runner}` blocks
- community: `README.md` — timer/publish-retry configuration reference