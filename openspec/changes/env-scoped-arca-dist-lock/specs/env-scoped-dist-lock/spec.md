## ADDED Requirements

### Requirement: Env-scoped distributed lock name
The ARCA device TTL renew scheduler SHALL derive its distributed lock name from the
deployment environment so that pre-production and production use distinct lock
names and do not share a lock holder.

The effective lock name is composed of the configured lock name base and an
environment suffix derived from the current environment identifier. The suffix MUST
reflect the normalized environment (`dev`, `pre`, or `prod`) so that distinct
deployments to different environments never acquire the same lock key.

#### Scenario: Production uses a prod-scoped lock name
- **WHEN** a deployment's current environment resolves to `prod`
- **THEN** the scheduler acquires a distributed lock whose name carries a `prod`
  environment suffix and is distinct from the pre scoped name

#### Scenario: Pre-production uses a pre-scoped lock name
- **WHEN** a deployment's current environment resolves to `pre`
- **THEN** the scheduler acquires a distributed lock whose name carries a `pre`
  environment suffix and is distinct from the prod scoped name

#### Scenario: Environment isolation is guaranteed with an explicit lock_name
- **WHEN** a configuration specifies an explicit `lock_name` value
- **THEN** the effective lock name still carries the environment suffix so that
  pre and prod never share the same lock key

#### Scenario: Dev uses a dev-scoped lock name
- **WHEN** a deployment's current environment resolves to `dev`
- **THEN** the scheduler acquires a distributed lock whose name carries a `dev`
  environment suffix

### Requirement: Lock lease shorter than cron interval
The scheduler's distributed lock lease (`lock_expire_seconds`) SHALL be strictly
less than the cron interval (`cron_interval_seconds`), so that between cron rounds
the lock is released and a different instance can acquire it rather than the same
machine re-acquiring it every tick.

#### Scenario: Another machine acquires after lease expiry
- **WHEN** a cron interval elapses with the lock no longer held because its lease
  (less than the interval) has expired
- **THEN** the lock is acquirable by a different scheduler instance that contends
  at the tick

#### Scenario: Default values enforce the ordering
- **WHEN** the task is constructed with default configuration
- **THEN** `lock_expire_seconds` is strictly less than `cron_interval_seconds`

### Requirement: No dead renew-interval config
The `device_ttl_timer` configuration SHALL NOT expose a `lock_renew_interval_seconds`
key, because no code reads it and it misleadingly implies per-task auto-renew control
that does not exist.