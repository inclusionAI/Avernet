-- Persistent-SQLite counterpart to 2026_08_04_task_queue_idempotency.sql.
--
-- That file is the prod (OceanBase) change request and uses syntax SQLite
-- rejects: backticks, MODIFY COLUMN, COMMENT, and the GLOBAL index keyword.
-- This file is the same migration, executable as-is. See
-- 2026_08_04_task_queue_idempotency.README.md for who needs which file.
--
-- No collation clause: SQLite compares TEXT as BINARY natively, which is
-- already the byte-for-byte comparison the key contract requires. That is also
-- why task_type needs no rewrite here — only MySQL/OceanBase default to a
-- collation that would merge 'Job' and 'job' in the dedup index.
--
-- No backfill: both columns are new and nullable, every existing row takes
-- NULL, and SQLite treats NULLs as distinct in a unique index, so no two
-- existing rows can collide however many duplicate enqueues the table holds.
--
-- One column per statement — SQLite's ALTER TABLE accepts a single ADD COLUMN.

ALTER TABLE ac_task_queue ADD COLUMN idempotency_key varchar(190) DEFAULT NULL;

ALTER TABLE ac_task_queue ADD COLUMN active_idempotency_key varchar(190) DEFAULT NULL;

CREATE UNIQUE INDEX uk_env_task_type_active_idem
  ON ac_task_queue (env, task_type, active_idempotency_key);
