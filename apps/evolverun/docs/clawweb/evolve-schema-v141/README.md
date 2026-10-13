# Recovery task context capacity

Migration v141 expands the existing `ce_tasks.config_json` column from TEXT to
MEDIUMTEXT. Recovery tasks keep their timeline and evidence references in this
column; no table, index or separate delivery store is added.

## Managed database deployment

Before deploying the corresponding ClawWeb code, inspect the existing column:

```sql
SHOW FULL COLUMNS FROM `ce_tasks` LIKE 'config_json';
```

For an existing TEXT column, apply this change through the normal database
change process:

```sql
ALTER TABLE `ce_tasks`
  MODIFY COLUMN `config_json` MEDIUMTEXT NOT NULL
  COMMENT '业务任务扩展与运行上下文';
```

Inspect the column again and confirm MEDIUMTEXT, NOT NULL and preservation of
existing task records. A database already using MEDIUMTEXT needs no ALTER.
For fresh managed installations, apply the existing table definitions and this
upgrade before starting the application. SQLite requires no column change;
its canonical migration registry records v141 at startup.

Managed MySQL/ZDAS deployments that deny runtime DDL skip startup migrations.
The registered v141 migration does not replace this external upgrade. Align
the deployment's schema-version ledger only after all intervening migrations
have been verified; do not mark unrelated migrations as applied.

## Rollback

Reverting application code does not require shrinking this column or deleting
task history. Keep MEDIUMTEXT during rollback. Shrinking to TEXT can lose data
or fail once any existing JSON exceeds 65,535 bytes; it is a separate reviewed
database change requiring a size check and backup, rather than a deployment
rollback step.
