# agentcompute Schema Migration Validation — `ac_agent_compute_*`

- **Spec:** `openspec/changes/migrate-agentcompute-swarm-into-backend/` —
  schema decisions D38 (migration & lifecycle), D34/D28 (resumable slices and
  fencing), task 12.3; retention wiring D13/D31 for the artifact columns.
- **Migration files:** dated files in this directory, applied **ascending by
  file name**. Current:
  - `2026_10_08_agent_compute.sql` — v1 tables: `run`, `agent`, `message`,
    `result_shard`, `round`, `guard_event`, `job` (durable slice queue, D34).
  - Later files evolve these additively (see
    `tests/community/core/agentcompute/test_agentcompute_schema_contract.py`
    for the enforced policy).
- **ORM parity:** `core/agentcompute/repository/registry.py` is the source of
  truth; `Base.metadata.create_all` (community SQLite bootstrap,
  `create_schema: true`) is the canonical fresh-install shape. The dated
  files are the operator-provision shape and must be regenerated in the same
  change whenever the models move — the contract test fails any drift in
  columns, unique-index names, or index column order.

## Deployment steps (dev → pre → prod)

1. **Apply dated sql files ascending, BEFORE the application release that
   uses them.** A file's `YYYY_MM_DD_` prefix is its position in the sequence;
   operators apply the earliest date first. Additive changes are safe to run
   ahead of any code change: old pods keep inserting the old row shape and
   simply leave new columns at their defaults — that is the property the
   contract test enforces (no `DROP`/`MODIFY`, additive `ALTER ... ADD` only).
2. **Audit duplicates before applying a file that widens or adds a unique
   index.** Fresh v1 tables make this a no-op today; from the first
   post-provision migration on, run the duplicate audit for each new/widened
   unique key before applying (same procedure as
   `2026_08_24_oceanbase_validation.md` step 1).
3. **Validate, then ship the application change.** Run the verification
   queries below against pre-release; the local SQLite suite can only verify
   what the two storage engines share (the contract tests), not the
   OceanBase-specific behavior.
4. **Rollback: re-deploy the previous image. Never DROP back.** Old
   application code must keep running against post-migration rows — that is
   the forward-only contract (D38). A schema reversal is itself a new
   additive migration (add anew, ship anew), never a destructive rewrite of
   rows a resumed slice may still read. Durable-slice state (`job.claim_epoch`,
   `job.lease_expires_at`, `result_shard`) is exactly the state that makes
   dropping unrecoverable.

**Deploy-lead rule (cross-reference, task 12.2 drain — do not widen here):**
a rolling deploy must lead the old fleet's shutdown by at least
`orchestration.slice_max_seconds + orchestration.lease_seconds` (defaults
300s + 60s) so every in-flight slice either finishes within the drain grace
(`drain.grace_seconds`) or releases via lease expiry with the job rescheduled
— old pods stopping mid-slice is safe because `claim_epoch` fencing rejects
their late writes. The authoritative procedure lives with the graceful-drain
runbook shipped for task 12.2.

## Verification queries (run per release, per table)

Rows are (env, tenant)-scoped; every count is grouped by `env, tenant` — an
ungrouped count hides cross-tenant drift the code can never produce.

```sql
-- 1. Row counts per scope (one query per table, same shape):
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_run
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_agent
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_message
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_result_shard
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_round
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_guard_event
  WHERE is_deleted = 0 GROUP BY env, tenant;
SELECT env, tenant, COUNT(*) FROM ac_agent_compute_job
  WHERE is_deleted = 0 GROUP BY env, tenant;

-- 2. Exactly one job per run (uk_job_run): any row here blocks the release.
SELECT env, tenant, run_id FROM ac_agent_compute_job
  WHERE is_deleted = 0 GROUP BY env, tenant, run_id HAVING COUNT(*) > 1;

-- 3. Shard XOR contract (D31): exactly one of payload / artifact_key.
SELECT env, tenant, shard_id FROM ac_agent_compute_result_shard
  WHERE is_deleted = 0
    AND ((payload IS NULL) = (artifact_key IS NULL));

-- 4. Orphan lineage: agents/rounds/jobs/shards whose run row is missing.
SELECT a.env, a.tenant, a.run_id FROM ac_agent_compute_agent a
  LEFT JOIN ac_agent_compute_run r
    ON r.env = a.env AND r.tenant = a.tenant AND r.run_id = a.run_id
  WHERE a.is_deleted = 0 AND r.id IS NULL;

-- 5. Job queue health (admission counters read these shapes):
SELECT env, status, COUNT(*) FROM ac_agent_compute_job
  WHERE is_deleted = 0 GROUP BY env, status;
```

## OceanBase (MySQL-mode) behavior to verify in pre-release

These cannot be reproduced by the SQLite suite (single in-process writer, no
partitioning, no distributed row lock) — the same gap
`2026_08_24_oceanbase_validation.md` documents.

- **V1 — unique-index partition modifiers.** `uk_job_run` is
  `BLOCK_SIZE 16384 GLOBAL`: `ac_agent_compute_job` dedups one job per
  `(env, tenant, run_id)` and is the claim source; a partition-LOCAL unique
  key would allow the same `run_id` once per partition, double-claiming
  slices. Other unique keys are `LOCAL` (or bare) by current provisioning —
  do not "align" them to GLOBAL without a dedup-scoping reason.
- **V2 — `BOOL`/`tinyint(1)` and `bigint` epoch-ms column handling** on the
  ships-`DateTime` vs `bigint` split (D33): coordination columns
  (`run_at`, `lease_expires_at`, `deadline_at`) are DB-clock `timestamp`;
  domain audit columns (`started_at`, `ended_at`, `routed_at`) are epoch-ms
  `bigint` — verify the claim/reclaim comparisons resolve on the DB clock,
  not client time.
- **V3 — TEXT size.** `goal`, `task_spec`, `payload`, `content`, `decision`
  are `TEXT`; verify the largest serialized TaskEnvelope/decision/result fits
  the tenant's TEXT limit; results above `artifact_inline_threshold` spill to
  the artifact store (D31), so over-limit results must never land in-row.
- **V4 — index key length (utf8mb4).** The widest unique keys are
  `(env, tenant, run_id, idempotency_key)` with `varchar(190)` keys —
  verify they create within the tenant's utf8mb4 key-length budget;
  otherwise shorten via prefix length, never by widening a key.
- **V5 — sqlite-equivalence baseline (CI).** Column/index parity, mandatory
  columns (`env`/`tenant`/`is_deleted`/`gmt_*`), env/tenant-scoped unique
  keys, and the doc-file policy (dated names, no destructive statements,
  additive ALTERs) are enforced in CI by
  `tests/community/core/agentcompute/test_agentcompute_schema_contract.py`.

## Pre-release validation status

| Check | Owner | Status |
| --- | --- | --- |
| Per-table (env, tenant) counts + orphan queries | DBA + backend | TODO (pre) |
| One-job-per-run audit (uk_job_run) | backend | TODO (pre) |
| V1 GLOBAL dedup on job queue | backend | TODO (pre) |
| V2 DB-clock coordination columns | backend | TODO (pre) |
| V3 TEXT size | DBA | TODO (pre) |
| V4 utf8mb4 key length | DBA | TODO (pre) |
| V5 schema-equivalence parity | CI | ✅ automated |