# Enqueue idempotency migration — which file to run

One migration, three files, because the supported stores do not share a dialect.
Run exactly one; they are alternatives, not steps.

| Your store | File |
| --- | --- |
| OceanBase (prod) | `2026_08_04_task_queue_idempotency.sql` |
| MySQL (stock) | `2026_08_04_task_queue_idempotency.mysql.sql` |
| SQLite (persistent file) | `2026_08_04_task_queue_idempotency.sqlite.sql` |

## PostgreSQL is not supported by this component

`CommunityDatabase` will happily connect to PostgreSQL, but **the task queue does
not run on it**, with or without this migration. `TaskQueueRepository._now_plus`
branches on SQLite and treats every other dialect as MySQL, emitting
`date_add(now(), INTERVAL n SECOND)` — a function PostgreSQL does not have, so
the first enqueue with a non-zero delay fails. All the repository's timing is
DB-side, so this is not a corner case.

No PostgreSQL DDL is shipped deliberately: applying one would produce a table
that still cannot be used, which is worse than an honest gap because it looks
like a supported path. Making the component work on PostgreSQL means teaching
`_now_plus` (and a test matrix) about a third dialect — a change to the
component, not to this migration.

## Do I need to run anything?

**Yes, if you have provisioned `ac_task_queue`** — regardless of whether any
call site passes an idempotency key yet, and regardless of whether the worker is
enabled. Apply it **before deploying the release that contains it**. The ORM maps
both new columns unconditionally, so every `SELECT` projects them and every
`INSERT` writes them even for an un-keyed enqueue; against a table without the
columns the whole queue fails with a missing-column error.

**No, if you have never provisioned `ac_task_queue`.** `CommunityDatabase` is a
pure connection provider and never runs `create_all`, so the table only exists if
an operator created it. Without it, nothing reads the table and the worker is
disabled by default (`task_queue_worker.enabled` defaults to `false`).

**No, for the local/test profile.** Its SQLite schema is rebuilt from the ORM
metadata on every process start, so it already has the columns. Note that
`create_all(checkfirst=True)` adds missing *tables*, not missing *columns* — a
long-lived local database file predating this change needs recreating or needs
the SQLite file above.

## Why the dialects differ

The engines disagree about string comparison, and the dedup key is compared
byte-for-byte by contract.

- **MySQL / OceanBase** default to a `utf8mb4_*_ci` collation, under which
  `publish:Bot-A:poll` and `publish:bot-a:poll` are the *same* entry in the
  unique index — one caller's enqueue would silently join another's task. Both
  key columns therefore pin `utf8mb4_bin`, and `task_type` (the index's other
  scope column) is rewritten to match, since an index is only as precise as its
  least precise column. That rewrite is why those two files have a separate,
  ordered first statement.
- **SQLite** compares `TEXT` as `BINARY` natively.

So only the MySQL-family files carry collation clauses; SQLite already has the
required semantics and needs no column rewrite.

`utf8mb4_bin` is itself **PAD SPACE**, so it settles case but not trailing
spaces. That half is closed in Python, for every engine: `enqueue` rejects keys
with leading or trailing whitespace, and `HandlerRegistry.register` rejects any
task type carrying it.

## Backfill

None, on any engine. Both columns are new and nullable, so every existing row
takes `NULL`, and all three engines treat `NULL`s as distinct in a unique index.
No two existing rows can collide however many duplicate enqueues the table
already holds, so the index can be created alongside the columns with no
duplicate-scrub step.

## Rollback

`DROP INDEX` + `DROP COLUMN` on both columns. No data migration to unwind. On the
MySQL-family files, reverting `task_type` to its previous collation is optional —
it is a strictly narrower comparison and nothing depends on the looser one.
