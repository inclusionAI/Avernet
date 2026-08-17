## 1. Env-scoped lock name resolution

- [x] 1.1 Add a `resolved_lock_name()` helper on `DeviceTtlTimerTaskConfig` (or task) that combines the configured `lock_name` base with an environment suffix from `get_current_env()` (e.g. `device_ttl_timer_lock_<env>`).
- [x] 1.2 Update `DeviceTtlTimerTask.run()` to use the resolved lock name when acquiring the distributed lock (and when logging acquisition/skip messages).

## 2. Config and comments

- [x] 2.1 Update the `lock_name` comment in `src/baas/configs/application.yaml` and `src/baas/singlebox-configs/application.yaml` to document the env-suffixed key scheme.
- [x] 2.2 Set `lock_expire_seconds` in the YAML configs and `DeviceTtlTimerTaskConfig` default to `1750` (strictly less than `cron_interval_seconds` `1800`), and add a config comment requiring `lock_expire_seconds < cron_interval_seconds`.
- [x] 2.3 Remove the unused `lock_renew_interval_seconds` key from the `device_ttl_timer` sections of both YAML configs. (The `bot_run_queue` occurrences are left intact.)
- [x] 2.4 Verify no other code references the raw `device_ttl_timer_lock` name for acquisition.

## 3. Tests

- [x] 3.1 Add/update unit tests in `test_device_ttl_timer_task.py` asserting the effective lock name carries the correct environment suffix for dev, pre, and prod.
- [x] 3.2 Add a test that an explicit `lock_name` config value still yields an env-suffixed effective lock name.
- [x] 3.3 Update `DeviceTtlTimerTaskConfig` default assertions for `lock_expire_seconds == 1750` (strictly less than `cron_interval_seconds` `1800`).
- [x] 3.4 Run the scheduler unit tests and confirm all pass.

## 4. Architecture and CI gates

- [x] 4.1 Run the module SAST/lint and architecture boundary checks for `src/baas/` and confirm no regressions.
- [x] 4.2 Document the new lock key naming in the design/migration notes so runbooks referencing `device_ttl_timer_lock` are updated.