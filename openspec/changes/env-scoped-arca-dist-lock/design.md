## Context

The ARCA device TTL renew scheduler (`DeviceTtlTimerTask`) guards its cron-run
full-drain scan with a distributed lock named `device_ttl_timer_lock`. This lock
name is hardcoded as a default in `DeviceTtlTimerTaskConfig` and explicitly
configured in `configs/application.yaml` and `singlebox-configs/application.yaml`.

PRE and PROD deployments against the same distributed lock store therefore
compete for the same lock key. When one environment holds the lock, the other
environment's cron run skips (`Lock not acquired`). Since lock keys are global in
the shared lock store, this couples the two environments' renewal scheduling.

The codebase already has an environment utility, `get_current_env()` in
`core/utils/env_utils.py`, which returns `"dev"`, `"pre"`, or `"prod"` by
inspecting deploy env vars (`SERVER_ENV` / `REAL_SERVER_ENV` / the
`ConfigPath.DEPLOY_ENV_VAR` variable), mapping `prepub→pre`, `gray→prod`.

## Goals / Non-Goals

**Goals:**
- Pre and prod instances use distinct distributed lock names so the ARCA TTL
  renew cron is scoped per environment.
- Keep the lock name configurable via config with a safe environment-scoped
  default; no requirement for operators to change deploy config.
- Preserve testability of the scheduler task.

**Non-Goals:**
- Changing the distributed lock service itself.
- Switching the lock store or lock semantics.
- Scoping any other lock/task beyond the ARCA device TTL renew lock.
- Migrating existing in-progress lock leases (they expire naturally via TTL).

## Decisions

### Decision 1: Scope the lock name with the environment obtained from `get_current_env()`

When constructing the effective lock name, derive the environment tag via the
existing `get_current_env()` helper and compose it into the lock name as
`device_ttl_timer_lock_<env>` (e.g. `device_ttl_timer_lock_dev`,
`device_ttl_timer_lock_pre`, `device_ttl_timer_lock_prod`).

- **Why**: `get_current_env()` is the canonical, already-tested env source; it
  normalizes `prepub`/`gray` correctly. Reusing it avoids a new env-resolution
  path and keeps behavior consistent with the rest of the codebase.
- **Alternative considered**: deriving env from raw `SERVER_ENV` directly — rejected
  because it bypasses the normalization (e.g. `prepub`, `gray`) and duplicates
  existing logic.
- **Alternative considered**: requiring operators to set `lock_name` per env in
  config — rejected because it couples correctness to manual deploy config and is
  error-prone.

### Decision 2: Default `lock_name` to a sentinel meaning "scope by env"; resolve lazily at runtime

`DeviceTtlTimerTaskConfig.lock_name` keeps an explicit default that signals
"auto-scope by environment". At lock acquisition time, the effective lock name is
`<configured_base_or_default><env_suffix>`. If an operator explicitly sets a
`lock_name`, it is still suffixed with the env to guarantee isolation.

- **Why**: An explicit config value of `device_ttl_timer_lock` set by operators
  (as today) must not silently disable env isolation. Resolving at runtime and
  always applying the suffix guarantees pre/prod isolation regardless of whether
  `lock_name` comes from the default or from config.
- **Alternative considered**: storing the final suffixed name in config — rejected
  because config is static per deployment and would require per-env config values,
  defeating the "safe default" goal.

### Decision 3: Expose the resolved lock name via a helper/property

Add a helper (e.g. `resolved_lock_name()`) on the config or task that returns the
env-scoped lock name, so tests and callers observe the exact string that is used
for acquisition.

- **Why**: Keeps the suffix logic in one place, testable, and readable.

### Decision 4: Ensure `lock_expire_seconds < cron_interval_seconds` so another box can acquire the lock

Today both default to `1800`. With equal values (and auto-renew keeping the lease
alive through a long scan), the same machine that held the lock is typically the
one to re-acquire it at the next cron tick — the lease has not yet expired by the
time the next interval fires, so competitors are crowded out. Making the lease
expire strictly before the next interval lets the lock become free between rounds
so a different instance can acquire it (improving scheduling balance and avoiding
single-box monopolization).

- **Why**: `lock_expire_seconds` bounds how long a holder may actually keep the
  lease (it is the "safe" ceiling even if renew is missed); the cron interval is
  the earliest a competitor can contend. For fairness the ceiling must be below
  the contention point.
- **Concrete change**: set `lock_expire_seconds` default to `1750` (clearly below
  `cron_interval_seconds` `1800`), and document in config that
  `lock_expire_seconds < cron_interval_seconds` is required for cross-machine
  takeover between rounds.
- **Alternative considered**: leaving values equal so a single fast box always
  wins — rejected because it contradicts the env-isolation distribution goal and
  reduces resilience/load balancing.

### Decision 5: Remove the dead `lock_renew_interval_seconds` YAML key

`lock_renew_interval_seconds` under `device_ttl_timer` in the YAML configs is
**never read by any Python code**. The auto-renew interval is configured at the
`DistributedLockService` level (wired from `bot_run_queue.session_lock_renew_seconds`)
and per-task this is not overridable. Keeping the key in `device_ttl_timer`
suggests per-task renew control that does not exist.

- **Why**: Removing dead/misleading config prevents operators from believing they
  can tune renew interval per task and avoids silent no-op configuration. Only the
  `device_ttl_timer` occurrences are removed; the `bot_run_queue` occurrences are
  left untouched.
- **Note — related defect (out of scope to fix here unless confirmed):** the renew
  thread (`distributed_lock/_service.py`) refreshes the lease to
  `self._default_expire_seconds` (wired to `session_lock_expire_seconds=300`),
  not to the task's per-acquisition `expire_seconds`. If we keep task-level
  renew, this must be reconciled; the immediate decision is to drop the unused
  task-level key.
- **Alternative considered**: keep the key and wire it through
  `DeviceTtlTimerTaskConfig` → per-acquisition renew. Rejected for this change to
  keep scope small; the env-isolation goal does not require per-task renew tuning.

## Risks / Trade-offs

- **Lock store accumulates stale keys** → Old `device_ttl_timer_lock` leases
  (and dev-scoped variants) linger until their TTL expires after a deploy; this is
  benign because the scheduler only ever acquires the env-scoped name. No cleanup
  required.
- **Gray/pre differentiation** → `get_current_env()` maps `gray→prod`, so gray
  shares prod's lock. `get_current_env_with_gray()` could be used instead if gray
  must be separated. Trade-off between sharing (fewer distinct keys) and strict
  isolation; defaulting to prod-sharing is consistent with existing env semantics.
- **Behavior change to lock key name** → Cross-check: any runbook that manually
  inspects or clears `device_ttl_timer_lock` must now look at the env-suffixed
  key. Addressed by documenting the new name scheme in the design and config
  comment.

## Migration Plan

- No schema or contract changes. `lock_expire_seconds` default changes from 1800
  to `1750`; the unused `device_ttl_timer.lock_renew_interval_seconds` key is
  removed from the YAML configs.
- Deploy: new builds acquire env-scoped lock keys. Existing older builds still use
  the shared key until they are decommissioned — brief co-existence can cause the
  old key to be held while the new key is free, so during rolling upgrade both
  environments may scan independently; this is the intended new behavior.
- Rollback: reverting the code restores the shared name; no data migration.

## Open Questions

- Should gray be isolated from prod? Initially no (consistent with existing env
  semantics); can revisit via `get_current_env_with_gray()` if required.