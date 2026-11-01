# ClawWeb Pack operation journal (migration 149)

## Scope and compatibility

`clawweb_pack_operations` is private to ClawWeb. It is not a deployment-history
reservation table and does not change ClawMind, TaskGuard, Git consumption, or
`workflow_deploy_history.action`. Existing workflow/history rows are untouched.
Formal history is written only by the service's business callback after the
exact Git outcome has been confirmed.

The repository is `server/repositories/pack-operation-repository.ts`; its database
boundary is `IDatabase`. New schema is defined in shared `schema-pack-operations.ts`
and mounted through the normal shared `db.ts` migration runner as **149**. It does
not reuse versions 145, 147, or 148 or restore the retired write coordinator.

## Repository contract

- `begin({requestId, workflowId, actorId, kind, inputHash})` returns
  `{operation, created}`; identity is all five fields, not just the request ID.
  Reusing an ID with different input returns 409. `requestId` should be a UUID.
- `get(requestId)` returns the stored operation or null. This is an audit read:
  the route must check `actorId` and `workflowId` before exposing a receipt.
- `prepare(requestId, payloadJson, expectedAttempt)` freezes exact JSON bytes. `payloadJson` can
  contain `{git: {commit, ...}, specJson, title, packId, remoteHash, ...}`, including
  recovery bundles and candidate YAML. Different prepared bytes return 409.
- `markPushing(requestId, expectedAttempt)` returns `{operation, claimed}`. Only `claimed: true`
  authorizes a new push; duplicate callers must not push. `begin.created` alone
  is not a push claim.
- `preflightRecovery(requestId, expectedAttempt)` locks workflow/operation and checks the full
  baseline for `prepared/pushing/unknown` before the service touches Git.
  `completed` returns its receipt; `preparing/failed` return 409. It is not a new
  push claim. The service must recheck the returned phase.
- `restartFailed(input, expectedAttempt)` accepts only a `failed` operation with the same five
  identity fields, the unchanged complete baseline, and no other workflow lock.
  It returns `{operation, created: true}`, resets the phase to `preparing`, and
  increments the persisted `attempt` and clears payload/result/error. Non-failed phases and concurrent restart losers
  return 409; it never resets an uncertain push or refreshes a stale baseline.
- `complete(requestId, resultJson, businessCallback, expectedAttempt)` checks the frozen workflow
  and active-record baselines, invokes `businessCallback(tx, operation)`, writes
  the result, and releases the persistent lock in **one transaction**. Use `tx`
  for all callback DB writes, propagate errors/zero-row failures, and do no Git
  or other network I/O in the callback. Exact-result replay never reruns it.
- `failBeforePush(requestId, errorJson, expectedAttempt)` releases only `preparing/prepared` work.
- `markUnknown(requestId, errorJson, expectedAttempt)` retains the lock on `pushing/unknown` work.
- `findSavedCandidate(workflowId, commit)` accepts only completed saves whose
  parsed `payloadJson.git.commit` equals the full commit and whose recorded
  workflow row ID equals the current `workflow_specs.id`. Substring matches,
  deployment records and records of deleted/recreated workflows are not proof.

All methods are async. `prepare/complete/failBeforePush/markUnknown` return the
operation. The public fields are `attempt`, `phase`, `payloadJson`, `resultJson`, `errorJson`,
`baseline.workflow`, `baseline.active`, the five identity fields, and
`createdAtMs/updatedAtMs`. JSON fields are strings or null, not decoded objects.
`PackOperationError` exposes `status` (400/404/409), `code`, and a message;
unexpected DB failures are not converted to success.

Workflow baseline fields: `workflow_id/id/spec_json/pack_id/version/owner_id`.
The stored workflow ID must exactly match the requested ID, even on databases
with case-insensitive collation; a case-only mismatch is 404, not a different Git
ref pointing at the same database row. The active
baseline contains **every** active row, ordered by ID, including ID, workflow,
pack, deployment/version/tag, action/from-deployment, spec, note, bot, owner and
active flag. Timestamps are not content identity. `begin` replay also checks the
workflow row ID, so an old completed request cannot succeed for a new instance.

Every mutation and recovery preflight requires the positive integer
`expectedAttempt` captured with its operation/payload. There is no default or
"use current" fallback. Missing, malformed or mismatched tokens return 409
`ATTEMPT_STALE`, including otherwise-idempotent calls. `begin` starts at attempt 1;
`restartFailed` increments it atomically. A cached attempt-1 P1 recovery can never
claim, fail, mark unknown or complete restarted attempt-2 P2. `complete` provides
the transaction-current operation to its callback; derive business writes from
that operation's payload, not from a previously cached object.

## Locks and recovery

`preparing → prepared → pushing → completed`; a pre-push operation may become
`failed`, and a started push may become `unknown → completed` after reconciliation.
Only `completed/failed` clear `lock_workflow_id`. No phase has a TTL; an old
`updated_at_ms` never permits lock stealing. Prepared payloads and pushing/unknown
states survive process/database connection restarts. Do not delete uncertain
journal rows or clear their locks to unblock a new operation.

Each changing transaction first locks the real `workflow_specs` row (`FOR UPDATE`
on MySQL/ZDAS; the SQLite adapter uses `BEGIN IMMEDIATE`). A full-column unique
nullable `lock_workflow_id` stores the actual numeric workflow row ID and holds
the durable lock between transactions. The busy check also matches unfinished
operations by full workflow name, preventing deletion/recreation from evading an
old unresolved operation. Workflow IDs up to the existing 255-character limit
are preserved without a prefix unique index.
`request_id` must also have full-column uniqueness. **All active-version writers
must take the same workflow row lock** before changing membership of the active
set; locking only currently active history rows cannot fence a writer that does
not cooperate under READ COMMITTED. This repository does not change unrelated
activation writers or impose a global coordination framework.

After a crash, the service reads the frozen bundle/objects, checks the exact Git
outcome, and calls `complete` only for a confirmed outcome. It must not create new
objects, allocate a new version, or reinterpret unknown as a pre-push failure.
`get` may still return an old completed audit record after workflow deletion;
it is not permission to reuse that result as a new save.

## Managed MySQL / ZDAS: manual apply before rollout

A restricted runtime account **does not apply or record** v149 when CREATE is
denied. Startup validates column types, capacities, nullability, and full-column unique indexes; an
absent/incomplete/unreadable schema raises an explicit migration error. The
existing initializer may expose its normal unavailable/NoOp (503) mode, not a
successful migration. Do not grant runtime DDL just to bypass this check.

An authorized operator must apply the DDL below to the intended database after
checking the release's earlier migrations. MySQL uses InnoDB: the normal runner
explicitly appends `ENGINE=InnoDB` and validates an existing table's engine. ZDAS
uses its native transactional storage without forcing a MySQL engine option. All
indexes are inline for ZDAS. The common DDL below is the current canonical
`packOperationMigrations[0].sql`; **append `ENGINE=InnoDB` before the semicolon
when applying on MySQL**.

**Do not apply `packOperationDdl` to MySQL/ZDAS:** that export is pre-rendered
SQLite SQL for test fixtures (`raw.exec(ddl)`). To generate deployment SQL in a
built shared package, use `renderPackOperationDdl(db, sql)` for each migration SQL
statement. That helper includes the MySQL engine requirement.

```sql
CREATE TABLE IF NOT EXISTS clawweb_pack_operations (
  request_id VARCHAR(190) PRIMARY KEY COMMENT 'ClawWeb操作请求ID，建议UUID',
  workflow_id VARCHAR(256) NOT NULL COMMENT '操作所属工作流',
  actor_id VARCHAR(256) NOT NULL COMMENT '发起操作的用户',
  kind VARCHAR(16) NOT NULL COMMENT '操作类型save或deploy',
  input_hash VARCHAR(128) NOT NULL COMMENT '完整请求内容摘要',
  attempt BIGINT NOT NULL DEFAULT 1 COMMENT '操作尝试代数',
  phase VARCHAR(16) NOT NULL COMMENT 'preparing|prepared|pushing|unknown|completed|failed',
  lock_workflow_id BIGINT DEFAULT NULL COMMENT '未完成操作锁定的workflow_specs行ID，不自动过期',
  baseline_json MEDIUMTEXT NOT NULL COMMENT 'workflow_specs和全部active部署核心字段基线',
  payload_json LONGTEXT COMMENT '精确Git对象、恢复bundle及候选YAML的JSON',
  result_json LONGTEXT COMMENT '完成响应JSON',
  error_json LONGTEXT COMMENT '失败或结果未知的恢复信息JSON',
  created_at_ms BIGINT NOT NULL COMMENT '创建时间毫秒',
  updated_at_ms BIGINT NOT NULL COMMENT '最后修改时间毫秒，不作为锁过期时间',
  UNIQUE INDEX uk_clawweb_pack_operation_lock (lock_workflow_id)
) COMMENT='ClawWeb专用Pack操作与恢复日志';
```

Before updating the migration ledger, verify the columns and indexes:

```sql
SHOW CREATE TABLE clawweb_pack_operations;
SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SUB_PART
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clawweb_pack_operations'
ORDER BY INDEX_NAME, SEQ_IN_INDEX;
```

Require primary/full unique `request_id`, full unique `lock_workflow_id`, and no
prefix (`SUB_PART IS NULL`) on either. SQLite validation additionally rejects
partial unique indexes; MySQL validation requires InnoDB. `CREATE TABLE IF NOT EXISTS` cannot repair
an incorrectly precreated table: inspect and repair it under an approved migration
instead of stamping the ledger. Once the verified DDL is applied, the authorized
operator may record v149:

```sql
INSERT INTO schema_version (version, description)
SELECT 149, 'ClawWeb-only durable pack operation journal and workflow operation lock'
WHERE NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 149);
```

Restart ClawWeb and verify the schema check and normal DB connectivity. Do not
rollback by dropping the journal while unresolved operations exist: doing so
loses recovery evidence and releases locks without knowing the Git outcome.

## Validation scope

Repository tests exercise real SQLite transactions, independent worker-thread
connections, restart reads, immutable payloads, identity conflicts, baseline
changes, business/journal failure rollback, persisted retry-generation ABA fencing,
and absence of pending history writes.
Migration tests exercise the normal runner, real additive SQLite SQL, managed DDL
denials, required unique constraints, and MySQL/ZDAS SQL rendering/locking
contracts. The latter are **not** live MySQL/ZDAS execution tests; run the same
concurrency scenarios on the intended managed deployment before rollout.
