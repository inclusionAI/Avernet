-- The generic background task queue (``core/task_queue``).
--
-- WHY THIS FILE EXISTS AT ALL. The table had no DDL in the repository: README's
-- "Status" section says the production table "must be provisioned before
-- enabling the worker", local and test runs build it from the shared ORM
-- metadata, and the OceanBase-only ``GLOBAL`` modifier lived in prose alone.
-- Every manifest apply path now goes through this queue (PR #1791), and W13
-- (#1696) creates bots on it, so a deployment that provisions the six
-- bot_config_manifest tables and not this one has an API that accepts work and
-- never performs it.
--
-- WHAT THIS FILE IS NOT. It is reconstructed from ``repository/models.py`` and
-- README, not dumped from a live table. On a deployment that already HAS
-- ac_task_queue, ``SHOW CREATE TABLE ac_task_queue`` is the truth and this file
-- is not -- reconcile against it before running anything here. Two known ways
-- an existing table differs:
--
--   * THE LEGACY DEDUP INDEX IS DECLARED HERE, not omitted. An earlier draft
--     of this file left it out on the reasoning that a NEW table should never
--     gain an index scoped wider than the code's -- true in isolation, and
--     wrong as a schema decision, because ``repository/models.py`` still
--     declares it and
--     tests/community/repository/platform/test_task_queue_repository.py
--     asserts the behaviour it produces
--     (test_same_key_under_different_app_is_rejected_while_the_legacy_index_lives).
--     Omitting it here would mean a freshly provisioned OceanBase database
--     accepts an enqueue that every local SQLite test run rejects -- the two
--     schemas disagreeing while both claim to be this component's. The ORM's
--     own comment sets the order: dropping it is the last step of the
--     app-scoping migration, "drop it there and here in the same change"
--     (README "Provisioning"). This file is the "here".
--   * TIMESTAMP COLUMNS. The manifest DDL in core/bot_config_manifest/sql/ uses
--     TIMESTAMP to match ac_bots. This file deliberately keeps DATETIME,
--     mirroring the ORM's ``DateTime`` and the table as it was provisioned.
--     Do not "align" it as a consistency edit: every claim, reclaim and
--     deadline comparison here is a DB-clock comparison, and changing the type
--     under a running fleet changes what those comparisons mean.
--
-- DIALECT: OCEANBASE, MYSQL MODE. No ENGINE clause and no BLOCK_SIZE /
-- REPLICA_NUM / COMPRESSION / TABLET_SIZE / PCTFREE / ROW_FORMAT -- OceanBase
-- applies its own defaults and echoes them back from SHOW CREATE TABLE.
--
-- ``AUTO_INCREMENT_MODE = 'ORDER'`` is pinned rather than inherited from the
-- tenant default, because the code states the assumption outright:
-- ``_find_by_key``'s audit lookup orders by ``id DESC``, and its comment reads
-- "ids are monotonic per row and need no tie-break, while two rows enqueued in
-- the same second share a gmt_create". Under NOORDER each observer caches its
-- own auto-increment range, so ids stop being monotonic in insertion order
-- across observers and the query answering "which task handled key X?" returns
-- an older row -- with no timestamp precise enough to fall back to.

CREATE TABLE `ac_task_queue` (
  `id`            bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  -- utf8mb4_bin, and it is not decoration: task_type is the third column of the
  -- dedup unique index, and an index is only as precise as its least precise
  -- column. Under the default ci collation 'Job' and 'job' are two handlers but
  -- one index entry, so a keyed enqueue for one joins the other's live task.
  -- HandlerRegistry.register also refuses types that fold together, but that
  -- check is process-local: it cannot see a row written by another version
  -- during a rolling deploy, which is why the scope is enforced here too.
  `task_type`     varchar(100) COLLATE utf8mb4_bin NOT NULL COMMENT 'handler registry key',
  `payload`       text          NOT NULL COMMENT 'JSON string; deserialised in to_record()',
  `status`        varchar(20)   NOT NULL COMMENT 'PENDING/RUNNING/SUCCEEDED/FAILED/TIMED_OUT',
  `run_at`        datetime      NOT NULL COMMENT 'next-eligible time (DB clock); claim requires run_at <= now()',
  `claimed_by`    varchar(128)  DEFAULT NULL COMMENT 'worker id of the current holder; NULL when not RUNNING',
  `lease_expires_at` datetime   DEFAULT NULL COMMENT 'claim lease deadline (DB clock); expired => reclaimable',
  `attempts`      int(11)       NOT NULL DEFAULT 0 COMMENT 'incremented on each claim; diagnostic only, not the give-up rule',
  `last_error`    text          DEFAULT NULL COMMENT 'last failure / timeout message',
  `deadline_at`   datetime      NOT NULL COMMENT 'give-up time from first enqueue (DB clock); a task always has one',
  -- Two columns on purpose. idempotency_key is the durable audit value, written
  -- once at enqueue and never cleared, so "which task handled key X?" stays
  -- answerable after the task finishes. active_idempotency_key is the
  -- enforcement value: equal to it while the task is live, NULLed on every
  -- terminal transition to release the key. MySQL/OceanBase have no partial
  -- indexes, so nulling a plain column is the portable way to express "unique
  -- among live rows only" -- both engines treat NULLs as distinct in a unique
  -- index, so un-keyed enqueues never collide. That is relied upon, not
  -- incidental.
  `idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'caller-supplied enqueue dedup key; NULL = opted out. Audit only',
  `active_idempotency_key` varchar(190) COLLATE utf8mb4_bin DEFAULT NULL COMMENT 'enforcement copy; NULLed on terminal transitions',
  `env`           varchar(20)   NOT NULL COMMENT '环境标识: prod/pre/dev; all queries filter by env',
  -- Which application owns the row. A second, independently deployed backend
  -- shares this table; without this column both fleets claim each other's
  -- tasks, each running a task_type its registry never heard of. The value
  -- comes from deployment config (TaskQueueConfig.app), never from a per-call
  -- argument, exactly like env.
  `app`           varchar(32)   NOT NULL DEFAULT 'agentclaw' COMMENT 'app who owns the task; every query that selects work filters by app',
  `gmt_create`    datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'first-enqueue audit time',
  `gmt_modified`  datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'audit; set DB-side on every CAS UPDATE',
  PRIMARY KEY (`id`),
  -- Claim and reclaim scans. The app-scoped pair leads with app because every
  -- claim/reclaim query now carries an app term; without it the busiest
  -- statement the component runs degrades to a full partition read.
  KEY `idx_env_status_run_at` (`env`, `status`, `run_at`) GLOBAL,
  KEY `idx_env_lease_expires_at` (`env`, `lease_expires_at`) GLOBAL,
  KEY `idx_env_app_status_run_at` (`env`, `app`, `status`, `run_at`) GLOBAL,
  KEY `idx_env_app_lease_expires_at` (`env`, `app`, `lease_expires_at`) GLOBAL,
  -- Active-only enqueue dedup: at most one LIVE task per key within an
  -- (env, app, task_type). Terminal rows null their active key and drop out.
  --
  -- GLOBAL IS LOAD-BEARING HERE, and it is the requirement README says the ORM
  -- cannot express: a partition-local index would allow the same active key
  -- once per partition and defeat dedup entirely. For W13 that means one
  -- create-with-manifest request running twice -- two Passport applications,
  -- two containers -- which is exactly what the key exists to prevent.
  UNIQUE KEY `uk_env_app_task_type_active_idempotency_key`
    (`env`, `app`, `task_type`, `active_idempotency_key`) GLOBAL,
  -- The pre-``app`` dedup index. Redundant once every app writes its own
  -- ``app``, and scheduled for removal -- but present because the ORM still
  -- declares it, so leaving it out would make a fresh database behave
  -- differently from every test run. It is unique on (env, task_type, key)
  -- regardless of app, so while it exists two deployments cannot use the same
  -- key for the same task_type in one env: the second app's INSERT is rejected
  -- even though the key is free in its own scope. ``enqueue`` never joins that
  -- row -- it belongs to another app and this deployment could not claim it --
  -- so the enqueue fails loudly once its retries are spent, which is the
  -- documented intermediate state rather than a defect in this file.
  --
  -- Drop it from this file, from models.py and from the deployed tables in one
  -- change; dropping it here alone re-opens the divergence described above.
  UNIQUE KEY `uk_env_task_type_active_idempotency_key`
    (`env`, `task_type`, `active_idempotency_key`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = '通用后台任务队列';
