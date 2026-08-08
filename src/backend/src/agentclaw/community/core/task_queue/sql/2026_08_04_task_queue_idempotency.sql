-- Opt-in, active-only enqueue idempotency for ac_task_queue (issue #569).
--
-- APPLY THIS BEFORE DEPLOYING THE BACKEND RELEASE THAT CONTAINS IT — not merely
-- before some later call site starts passing a key. The ORM maps both columns
-- unconditionally, so every SELECT projects them and every INSERT writes them
-- even when the caller supplies no key. Against a table without the columns the
-- whole task queue fails with "unknown column", un-keyed enqueues included.
-- The prod (OceanBase) table is created manually and must mirror
-- core/task_queue/repository/models.py; there is no fallback path in the code
-- for a missing column, so the ordering is mandatory rather than best-effort.
--
-- COLLATION IS LOAD-BEARING. Dedup keys are compared byte-for-byte, so both
-- columns pin utf8mb4_bin. Under the usual utf8mb4_*_ci default the unique
-- index would treat 'publish:Bot-A:poll' and 'publish:bot-a:poll' as the same
-- key (and non-_0900 ci collations PAD SPACE, so 'k1' == 'k1 '), letting one
-- caller's enqueue silently join a different caller's task. SQLite compares
-- BINARY already, so the test suite cannot observe the difference.
--
-- NO BACKFILL IS REQUIRED. Both columns are new and nullable, so every existing
-- row takes NULL, and NULLs are distinct in a unique index on both MySQL/
-- OceanBase and SQLite. No two existing rows can collide regardless of how many
-- duplicate enqueues the table already holds, so the index can be created in
-- the same statement as the columns with no duplicate-scrub step.
--
-- Two columns on purpose:
--   idempotency_key        durable audit; written once, never cleared.
--   active_idempotency_key enforcement; NULLed on every terminal transition so
--                          the key is released and may be legitimately reused.
-- MySQL/OceanBase have no partial indexes (no UNIQUE ... WHERE status NOT IN),
-- so nulling a plain column is the portable way to scope uniqueness to live
-- rows only.

ALTER TABLE `ac_task_queue`
  ADD COLUMN `idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL
    COMMENT 'caller-supplied enqueue dedup key; NULL = opted out (audit only)',
  ADD COLUMN `active_idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL
    COMMENT 'enforcement copy of idempotency_key; NULLed on terminal to release it',
  ADD UNIQUE KEY `uk_env_task_type_active_idem`
    (`env`, `task_type`, `active_idempotency_key`) GLOBAL;
