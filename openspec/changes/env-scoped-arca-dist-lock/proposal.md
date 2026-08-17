## Why

The ARCA TTL renew scheduler uses a fixed distributed lock name
`device_ttl_timer_lock` for the cron path. Pre-production and production
deployments share the same lock store, so when both environments run the
scheduler the lock is shared across environments. This lets one environment's
renewal run block the other's, and can cause cross-environment interference and
misleading lock-acquisition logic. Each environment needs its own lock so the
lock is isolated by env.

## What Changes

- Scope the distributed lock name by environment so pre and prod use distinct
  lock names instead of the shared `device_ttl_timer_lock`.
- Make `lock_expire_seconds` strictly less than `cron_interval_seconds` so the
  lease frees between rounds and a different machine can acquire the lock,
  instead of the same box re-acquiring every tick.
- Remove the unused `lock_renew_interval_seconds` key from the `device_ttl_timer`
  YAML config: it is never read by any code and misleadingly implies per-task
  renew control that does not exist.
- Keep the existing `lock_name` configurable with a sane default, but default it
  to an env-qualified name derived from the environment identifier.
- Update the packaged config files (`configs/application.yaml`,
  `singlebox-configs/application.yaml`) and adjust unit tests that assert the
  lock name.

## Capabilities

### New Capabilities
- `env-scoped-dist-lock`: the distributed lock name used by the ARCA TTL renew
  cron path is namespaced by environment, isolating PRE and PROD lock holders.

### Modified Capabilities
<!-- None: no existing specs yet. -->

## Impact

- `src/baas/src/secbaas/community/core/service/scheduler/_tasks/_device_ttl_timer_task.py`
  — `DeviceTtlTimerTaskConfig` and lock acquisition.
- `src/baas/configs/application.yaml` and
  `src/baas/singlebox-configs/application.yaml` — lock name configuration, lock
  expire default, and removal of the dead device_ttl_timer renew-interval key
  (`bot_run_queue` occurrences are left intact).
- `src/baas/tests/unit/core/service/scheduler/test_device_ttl_timer_task.py` —
  lock name assertions.
- No change to persisted contract/schema; lock keys in the distributed lock store
  will change on deploy (old `device_ttl_timer_lock` leases may linger until their
  TTL expires).