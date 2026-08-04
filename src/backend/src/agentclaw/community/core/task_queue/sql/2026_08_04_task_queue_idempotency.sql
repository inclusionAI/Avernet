-- Opt-in, active-only enqueue idempotency for ac_task_queue (issue #569).
--
-- APPLY THIS BEFORE deploying Backend code that writes these columns. The prod
-- (OceanBase) table is created manually and must mirror
-- core/task_queue/repository/models.py; there is no fallback path in the code
-- for a missing column, so the ordering is mandatory rather than best-effort.
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
  ADD COLUMN `idempotency_key` varchar(190) DEFAULT NULL
    COMMENT '调用方提供的入队去重键；NULL 表示不去重（仅审计用）',
  ADD COLUMN `active_idempotency_key` varchar(190) DEFAULT NULL
    COMMENT '去重键的执行副本；进入终态时置 NULL 以释放该键',
  ADD UNIQUE KEY `uk_env_task_type_active_idem`
    (`env`, `task_type`, `active_idempotency_key`) GLOBAL;
