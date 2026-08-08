-- Stock-MySQL counterpart to 2026_08_04_task_queue_idempotency.sql.
--
-- That file is the prod (OceanBase) change request; it is identical to this one
-- except for the GLOBAL index keyword, which is OceanBase-only and which stock
-- MySQL rejects. See 2026_08_04_task_queue_idempotency.README.md for who needs
-- which file. Everything the prod file says about ordering, collation, and the
-- absence of a backfill applies here unchanged — this header only covers what
-- differs.
--
-- STATEMENT 1 — run FIRST, so the unique index is built against the binary
-- collation instead of being built and then rebuilt.
--
-- OPERATOR NOTE: this one differs in kind from statement 2. task_type already
-- exists and holds data, and a collation change rewrites the column, so expect
-- a table rebuild rather than an instant metadata-only change. MODIFY COLUMN
-- also restates the whole definition, so confirm the live column matches
-- varchar(100) NOT NULL with this comment before running; anything omitted here
-- would be silently dropped. All 14 shipped task types are lowercase dotted
-- names, so no existing row changes value or collides.
ALTER TABLE `ac_task_queue`
  MODIFY COLUMN `task_type` varchar(100) COLLATE utf8mb4_bin NOT NULL
    COMMENT 'handler registry key';

-- STATEMENT 2 — the new columns and the dedup index.
ALTER TABLE `ac_task_queue`
  ADD COLUMN `idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL
    COMMENT 'caller-supplied enqueue dedup key; NULL = opted out (audit only)',
  ADD COLUMN `active_idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL
    COMMENT 'enforcement copy of idempotency_key; NULLed on terminal to release it',
  ADD UNIQUE KEY `uk_env_task_type_active_idem`
    (`env`, `task_type`, `active_idempotency_key`);
