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
-- key, letting one caller's enqueue silently join a different caller's task.
-- SQLite compares BINARY already, so the test suite cannot observe the
-- difference.
--
-- utf8mb4_bin closes case folding but NOT space padding: it is itself a PAD
-- SPACE collation, so 'k1' and 'k1 ' would still be one index entry here while
-- staying distinct on SQLite. That half is closed in Python instead — enqueue
-- rejects any key with leading or trailing whitespace, which makes the
-- collision unreachable without depending on utf8mb4_0900_bin (NO PAD) being
-- available on every OceanBase version. Do not relax that validation on the
-- assumption that this collation covers it.
--
-- THE SAME APPLIES TO task_type, WHICH IS WHY STATEMENT 1 EXISTS. A unique
-- index is only as precise as its least precise column: with task_type left on
-- the default ci collation, 'Job' and 'job' are one entry, so a keyed enqueue
-- for one handler joins the other's live task. The application also refuses to
-- register two task types that fold together, but that check is process-local
-- and cannot see a row written by another version mid-rolling-deploy, so the
-- scope is enforced here rather than only in code.
--
-- env is deliberately NOT modified. Unlike task_type it is compared by the
-- claim/reclaim eligibility filter and carries idx_env_status_run_at and
-- idx_env_lease_expires_at, so changing its collation would alter pre-existing
-- behaviour and rebuild those indexes — much wider than the risk, given env
-- comes from deployment config rather than per-call input. task_type is
-- compared in SQL only by the dedup lookup and is in no other index.
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

-- STATEMENT 1 — run this FIRST, before statement 2, so the unique index is
-- built against the binary collation instead of being built and then rebuilt.
--
-- OPERATOR NOTE: this one differs in kind from statement 2. task_type already
-- exists and holds data, and a collation change rewrites the column, so expect
-- a table rebuild rather than an instant metadata-only change — schedule it
-- accordingly. MODIFY COLUMN also restates the whole definition, so confirm the
-- live column matches varchar(100) NOT NULL with this comment before running;
-- anything omitted here would be silently dropped. All 14 shipped task types
-- are lowercase dotted names, so no existing row changes value or collides.
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
    (`env`, `task_type`, `active_idempotency_key`) GLOBAL;
