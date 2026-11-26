# ClawWeb Shared

This package provides cross-business ClawWeb capabilities and the existing
SQLite/MySQL/ZDAS database abstraction. Business modules consume package exports;
internal adapters and private configuration remain in the host.

## Distributed database leases

`server/services/distributed-lock.ts` provides `DistributedLockService.withLease`
over `server/repositories/distributed-lock-repository.ts`. The callback runs only
after a successful durable claim. Busy returns `{ acquired: false }`; persistence
errors reject and never fall back to unlocked work. The callback receives
`assertHeld()` and the immutable acquisition `version` for log correlation.
This is coordination, not an exactly-once guarantee for external side effects.

### Scope and storage

The scope is `(environment, scene, tenantId, resourceKey)`, encoded as a JSON
array and hashed with SHA-256. `clawweb_distributed_locks.id` is an auto-increment
primary key; `lock_id` has a separate full-column unique index. Original scope
columns remain readable. This avoids oversized or prefix-only unique indexes
on managed databases. The tenant identifier comes from the caller; global
background scans explicitly use `__global__`, never a newly generated tenant.

Each row also stores a fresh `owner_token`, the acquisition's `lease_ms`,
`expires_at_ms`, optional cooldown `next_acquire_at_ms`, increasing `version`,
and database-clock `created_at_ms` / `updated_at_ms`. Rows are retained on
release: clear ownership/expiry but preserve cooldown and version.

### Ownership and time

An initial unique insert creates a free record. Acquisition uses conditional
UPDATE on the observed version, expired/free lease, and elapsed cooldown.
Reads are advisory; successful atomic writes decide ownership. Renewal requires
the exact token/version and an unexpired lease, uses the persisted `lease_ms`,
and advances the version. Release also checks token/version/expiry in SQL.
Every operation uses the existing database facade; no transaction spans work.

Deadline/cooldown decisions use the database clock via `Dialect.nowMsSql`,
not application wall-clock time. The service uses a conservative local monotonic
budget beginning before database I/O, so slow responses cannot extend ownership.
A delayed renewal cannot revive a callback already marked lost. Renewals are
serialized and drained before release. Database errors and release errors
propagate; simultaneous work/release errors are retained in an AggregateError.

`leaseMs` and `renewEveryMs` must be positive integer milliseconds, with renewal
shorter than the lease. `minIntervalMs` defaults to zero; when present, successful
acquisition records the earliest next acquisition from that acquisition's start.
This prevents quick release and staggered replica timers from duplicating a
periodic scan. A callback must call `assertHeld()` at meaningful work boundaries.
Lost ownership cannot cancel a request already sent, so task-level versions,
claims and idempotency remain necessary.

### Provisioning and compatibility

Migration v158 registers through the existing database migration registry.
Provision managed databases using
[`distributed-locks-v158.mysql.sql`](../../scripts/sql/distributed-locks-v158.mysql.sql)
before enabling consumers. The schema guard verifies the auto-increment primary
key, required column definitions and full unique lock key. Request paths only
validate and never create/repair schema. Missing/incompatible schema fails closed.
SQLite, MySQL and ZDAS use the existing dialect and package boundary; there are
no new middleware dependencies, runtime credentials, or environment settings.

Only session recovery adopts this service in this change. Its scope is the
control-plane environment, `session_recovery_maintenance`, `__global__`, `scan`.
The host timer is 60 seconds, the lease is 120 seconds, renewal is every 20 seconds,
and the shared minimum start interval is 60 seconds. Original maintenance SQL,
selection, task protections, reporting and execution-monitor timers are retained.
Workflow planning remains on its existing implementation and journal.

### Verification

Shared tests cover independent SQLite connections, simultaneous first claims,
scope isolation, per-acquisition TTL, start cooldown, database-clock decisions,
stale-holder fencing, renewal/release races and storage failures. The explicit
opt-in MySQL suite uses `CLAWWEB_LOCK_TEST_MYSQL_SOCKET` for a disposable local
server only; it does not load application DB configuration or credentials.
Run the focused suites from `shared` with the workspace Vitest binary.
