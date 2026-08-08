-- PostgreSQL counterpart to 2026_08_04_task_queue_idempotency.sql.
--
-- That file is the prod (OceanBase) change request and uses syntax PostgreSQL
-- rejects: backticks, inline COMMENT, MODIFY COLUMN, and the GLOBAL index
-- keyword. This file is the same migration, executable as-is. See
-- 2026_08_04_task_queue_idempotency.README.md for who needs which file.
--
-- No collation clause: PostgreSQL varchar equality under a deterministic
-- collation (the default) is exact and does not pad, which is already the
-- byte-for-byte comparison the key contract requires. That is also why
-- task_type needs no rewrite here — only MySQL/OceanBase default to a collation
-- that would merge 'Job' and 'job' in the dedup index. If this database was
-- created with a NONDETERMINISTIC collation, that assumption does not hold and
-- the two key columns need an explicit deterministic COLLATE.
--
-- No backfill: both columns are new and nullable, every existing row takes
-- NULL, and UNIQUE is NULLS DISTINCT unless declared otherwise, so no two
-- existing rows can collide however many duplicate enqueues the table holds.

ALTER TABLE ac_task_queue
  ADD COLUMN idempotency_key varchar(190) DEFAULT NULL,
  ADD COLUMN active_idempotency_key varchar(190) DEFAULT NULL;

COMMENT ON COLUMN ac_task_queue.idempotency_key IS
  'caller-supplied enqueue dedup key; NULL = opted out (audit only)';

COMMENT ON COLUMN ac_task_queue.active_idempotency_key IS
  'enforcement copy of idempotency_key; NULLed on terminal to release it';

CREATE UNIQUE INDEX uk_env_task_type_active_idem
  ON ac_task_queue (env, task_type, active_idempotency_key);
