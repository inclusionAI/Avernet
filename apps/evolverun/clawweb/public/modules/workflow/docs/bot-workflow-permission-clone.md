# Bot workflow permission Clone: record deduplication

`POST /api/internal/bot-workflow-permissions/clone` keeps its existing method,
request fields, headers, validation, response fields and outcome values. Callers
do not need any new parameter. The change only makes each permission merge an
atomic database write; it does not introduce request replay or a new table.

## Persistence invariant

An exact target Bot has at most one permission row per
`(bot_id, bot_owner_id, workflow_id, env)`. Clone uses the dedicated
`BotWorkflowPermissionRepository.mergeGrants()` method to insert or atomically
OR the three permission bits with the database's **current** row. It does not
replace a concurrent grant with the result of an earlier JavaScript read.
Regular administrative `upsert()` keeps its replace semantics, including
permission downgrade.

Source selection, owner resolution, `scope`, cross-environment reads,
current-environment writes, empty-source success and `dry_run` stay unchanged.
The route still skips a workflow when the target already covers it. Permission
counts remain per-call diagnostics, not an exact cross-request concurrency
ledger. A retry can return `already_covered` after an earlier `granted` result.

This guarantees row uniqueness only when the complete unique index exists.
It does not make a multi-workflow batch a single transaction or serialize Clone
against administrative revocation. A new invocation can grant permissions again
after revocation. Owner-level rows whose `bot_id` is NULL are outside this exact
Bot uniqueness invariant; this change does not normalize or deduplicate them.

## OceanBase MySQL-mode deployment

The shared v18 schema declares `uk_bot_wf_perm`, but the ZDAS dialect skips
standalone `CREATE INDEX`. Migration version alone is not proof that the index
exists. Inspect the actual database and deploy the index **before** the new
Clone writer. Never run production DDL as part of a unit test or request.

### 1. Inspect the table and existing indexes

```sql
SHOW CREATE TABLE `bot_workflow_permissions`;
SHOW INDEX FROM `bot_workflow_permissions`;
```

Check the tenant is in MySQL mode, the table definition, partitioning, column
lengths/character sets, and the index names. A valid equivalent unique index
covers all four full columns with `Non_unique = 0` and `Sub_part IS NULL` for
every column. Reuse it if present; do not add a redundant index. If the existing
name is non-unique, incomplete or prefix-only, stop and plan its replacement
rather than dropping it automatically.

### 2. Check conflicting rows

```sql
SELECT `bot_id`, `bot_owner_id`, `workflow_id`, `env`, COUNT(*) AS duplicate_count
FROM `bot_workflow_permissions`
WHERE `bot_id` IS NOT NULL
GROUP BY `bot_id`, `bot_owner_id`, `workflow_id`, `env`
HAVING COUNT(*) > 1;
```

The result must be empty before adding the index. If duplicates exist, pause
relevant writers, back up the rows, review ID references, merge their permission
bits without downgrade, and retain one stable row per tuple through an approved
data migration. This runbook intentionally supplies no automatic deletion SQL.
Prevent concurrent writers from recreating duplicates during remediation and
index creation. Do not shrink identity columns or substitute prefix indexes to
work around a key-length error: confirm the deployed OceanBase version, tenant
limits and schema before choosing any additional migration. Partitioned tables
also require a deployment-specific local/global-index review.

### 3. Add and verify the index

The executable artifact is
[`bot-workflow-permission-clone-ob-index.sql`](bot-workflow-permission-clone-ob-index.sql).
Run it once only after the preflight succeeds:

```sql
ALTER TABLE `bot_workflow_permissions`
  ADD UNIQUE INDEX `uk_bot_wf_perm`
  (`bot_id`, `bot_owner_id`, `workflow_id`, `env`);
```

Inspect `SHOW INDEX` again and confirm the complete unique index is available.
The DDL does not change the HTTP interface or remove `env` from the identity.
Keep the uniqueness constraint on application rollback; dropping it would reopen
the duplicate-row window. Existing pre-fix writers may report a unique-conflict
error under contention, so coordinate writer rollout rather than masking errors.

## Verification scope

The regression suites cover the route contract, real SQLite exact-row uniqueness,
repeat merges, concurrent complementary merges, stronger existing permissions,
administrative downgrade and MySQL/ZDAS SQL generation. SQL-generation tests are
not evidence that a production OceanBase tenant has the correct index or accepts
its deployed DDL; that deployment verification remains required.
