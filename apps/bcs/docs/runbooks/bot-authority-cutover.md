# Runbook: 一次性 Authority Cutover（旧数据回填与治理）

Plan Task 17 of the bot owner/manager permissions release (spec
`docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md` §16).
This runbook is the ONLY sanctioned release procedure for migrating the
historical `created_by` library onto the new ownership authority and for
switching every Human authorization path over in one compatible version.

Maintenance binary: `bcs-ownership-migrate` (registered on the `bcs` crate,
built by the normal workspace build). It validates at compile-free level
that:

- `--maintenance` is required for anything to run;
- the datasource comes from the SAME config the BCS server loads
  (`--config-dir` / `BCS_CONFIG_DIR`, `bcs-config-{env}.toml`);
- `inspect` is a dry-run (zero writes) and `apply` is the only write path;
- `apply` consumes an explicit out-of-band confirmation file of candidate
  Bot ids (never owners) and re-derives every decision from current facts;
- exit codes: `0` success; `1` storage failure / per-Bot `failed` entries
  (re-run the SAME `--batch-id`); `2` usage errors (missing
  `--maintenance`, over-limit page, unreadable/invalid confirm file).

`bcs-cli` gains NO new leaf for this operation — the governed binary is the
only entry.

## Release order (execute as written; steps do not commute)

```text
备份 -> 阻断不兼容旧role/runtime写实例 -> 新schema
-> dry-run候选/冲突治理 -> 有界回填 -> 完整性核对
-> 同一兼容版本切换全部Human授权/注册/WS入口 -> 联机验收
```

### 1. 备份（快照恢复的基线）

Take a full point-in-time snapshot of the datasource (MySQL: consistent
logical dump of `bcs_bots`, `edge_grants`, `bot_ownership_transfers`,
`bot_ownership_initializations`, `bot_manager_changes`,
`bcs_bot_action_audits`, `bcs_actor_relations`, `permission_profiles`,
`bcs_provider_bot_bindings`; SQLite: file-level backup + WAL checkpoint).
Record the exact binary/config version in the change ticket. This snapshot
is the LAST state in which `created_by` is the authorization fact;
**after the cutover config switch (step 7) there is no legitimate path back
to it** (see “回滚纪律”).

### 2. 阻断不兼容旧 role/runtime 写实例

Stop or fence every instance that still runs the pre-authority build
(including task workers and run-channel consumers). New-instance writes
against the new schema from old binaries are structurally rejected by the
frozen checks, but fencing keeps the library single-writer for the whole
procedure. Do not run two versions against one datasource: role writes and
runtime admission must not be mixed across versions (spec §16.2). Freeze
permission/transfer writes during the whole batch phase (see step 5).

### 3. 新 schema（冻结链）

Apply the frozen schema chain the way the server expects it (SQLite chain
applies automatically through the `bcs` binary; MySQL/OceanBase applies
externally, ops-run, 001..031 — historical migrations are never rewritten,
dispatch applies only NEW numbers). Verify: `edge_grants` carries the
NOT NULL source columns and the unique owner slot, `bcs_bots.ownership_version`
exists (0 default), and `bot_ownership_initializations.batch_id` exists
(`governed_repair`/`registration` provenance + batch-tagged migration rows).
The binary deliberately does NOT auto-migrate: a schema failure must make it
exit nonzero, not self-heal.

### 4. dry-run 候选/冲突治理

```bash
# No writes: the complete candidate + conflict list, machine-readable.
bcs-ownership-migrate --maintenance --config-dir configs inspect --limit 100
# Large libraries resume the same ordered walk:
bcs-ownership-migrate --maintenance inspect --limit 100 --after-bot-id <cursor>
```

Govern the reported machine reasons (they are fixed vocabulary, never prose):

| reason | meaning | required governance |
| --- | --- | --- |
| `ready` | version-0, live, physical, `created_by` corroborated by the legacy creator relation, creator has a live Human row | confirm into a batch |
| `missing_creator` | absent/blank `created_by` (bare runtime rows) | intentional exclusion: stays version 0, keeps runtime identity and Agent self-discovery; never auto-claimed by suffix |
| `conflicting_creators` | multiple distinct legacy creator claims, or a single claim contradicting `created_by` | human decides the owner; fix by explicit transfer/registration evidence |
| `missing_human` | `created_by` names a User with no live Human actor row | verify the User is real (never fabricate); recreate/restore the Human row first, re-dry-run |
| `authority_inconsistent` | approved owner edge at version 0, or initialized version without owner edge | data-repair case by case; the binary never “fixes” it |

An absent legacy relation claim is legal (claims only corroborate
`created_by`); a claim that disagrees with it is a conflict. Operator
decisions are recorded out-of-band (change ticket); the binary re-derives
every classification from current facts, so a conflict resolved by hand
re-runs as `ready` on the next dry-run.

### 5. 有界回填（bounded apply）

One batch = at most 100 confirmed Bot ids (over-limit requests are
rejected, never clamped). Build the confirmation file from the dry-run
output — a JSON array of Bot ids ONLY:

```json
["bot-alpha", "bot-beta"]
```

```bash
bcs-ownership-migrate --maintenance apply \
  --batch-id cutover-2026-10-08-01 --confirm-file confirmed-cutover-01.json
```

Contract per batch:

- each confirmed Bot is re-verified from CURRENT facts before the write:
  version 0 verified via the Task 5 CAS inside the write transaction, and
  the owner only ever comes from the trusted `created_by` — the confirm
  file cannot smuggle an owner;
- `created_by` is NEVER modified;
- each initialization commits atomically (owner edge + version 1 + Human
  ensure + default profile + `bot_ownership_initializations` row tagged with
  the `batch_id`, source `governed_repair`, System actor
  `ownership-migration`);
- already-initialized/transferred Bots are only verified and skipped
  (`already_initialized` / `ownership_transferred`) — owner and version are
  never reset by a rerun;
- conflicts and exclusions (deleted bots, Human self rows) are report
  entries, never writes.

Idempotence & recovery: replaying the SAME `--batch-id` returns the
original report counts — previously committed Bots are recovered from the
ledger (`bot_ownership_initializations.batch_id`) and only re-verified;
Bots whose attempt failed are retried. On a partial failure the command
exits nonzero (`1`) with the `failed` entries; NO mid-library rollback
exists and no global “undo the migration” command exists — re-run the same
batch until the report carries no `failed` entry, then continue with the
next bounded batch.

### 6. 完整性核对

Keep authority/transfer writes frozen until every batch is applied and the
following checks pass (SQL, both live and v0 governance):

1. every live Human-owned Bot has EXACTLY ONE approved `owner` edge and
   `ownership_version = 1` (and vice versa: no approved owner edge on a
   version-0 row);
2. no `bot_ownership_initializations` row references a Human self row or a
   deleted Bot (`human_rows_migrated = 0`, `deleted_bots_migrated = 0`);
3. `created_by` unchanged for every migrated Bot (compare against the
   step-1 backup);
4. every batch's report is archived with its machine JSON; every non-`ready`
   outcome of the first dry-run is either migrated or has a governance
   decision in the ticket — the v0 governance checklist: bare runtime rows
   and unresolved conflicts STAY version 0 by design and are listed as the
   residual backlog (they keep runtime identity and Agent self-discovery
   but become non-transferable until governed);
5. `inspect` now returns an empty walk for the protected env (or only the
   documented residual conflicts).

### 7. 同一兼容版本切换全部 Human 授权/注册/WS 入口

Deploy ONE compatible version that switches every consumer to the new
authority in the same release: Bot mine/PATCH/candidates, Group/Session
view-actor/detail/launch, add-member/workspace, message/files/workbench,
invitation/friend acting-actor, legacy downstream re-authorization, the
registration owner-initialization lane, and the WS protected-writer
authorization. Old `created_by` authorization fallbacks are removed in that
release — no open-ended dual-read. The version cut is allowed ONLY after
step 6 passes; skipped migrations are not a blocker for the switch, only a
backlog with governance decisions.

### 8. 联机验收

Release-drill the fresh deployment: manager grant/revoke/re-grant,
ownership transfer (accepted transfer flips roles; the original owner keeps
manager), service restarts and dual-instance reads, WS dequeue/redispatch
still re-authorizing per frame, provider tombstone deletion leaves no
orphan owner edge (the same-commit retirement lane). Post-switch, the
state is the NEW fact: authority comes from owner/manager edges.

## 回滚纪律（read before the first batch）

- **已转交后禁止回滚到 created_by 授权版本.** Once ANY accepted transfer
  exists, “delete the owner edges and restore `created_by` authorization”
  is forbidden: it hands control back to the historical creator without a
  transfer decision. The preferred rollback after ANY successful migration
  is a compatible version that keeps reading the NEW authority and disables
  new/accepted transfers (freeze-first), never an authority rewrite.
- **不存在迁移中途的删库回滚语义.** There is no command that unwinds
  committed initializations; the recovery unit is the `batch_id` replay,
  which is add-safe and never rewrites owner facts. “回滚 migration 就行”
  is not a procedure.
- **前向修复 vs 快照恢复**
  - Forward fix (default): fix with governed writes — a wrongly initialized
    owner is corrected by a `delete_human_actor`-guarded
    retirement/transfer of the specific Bot, recorded and audited; the
    report/ledger rows for the batch are audit history and are never
    rewritten.
  - Snapshot restore (emergency only): requires the step-1 backup, the same
    fencing as step 2, an independent reviewed data-import/mapping plan,
    and — importantly — it restores BOTH the schema-consistent state and
    the step-7 config. After any accepted transfer, a snapshot restore
    resurrects the pre-cutover authorization-fact split; the restore plan
    must re-run this whole runbook from step 4 against the restored
    library. Restoring only tables (partial), or dropping role edges while
    “re-enabling” `created_by` reads in code, is forbidden.
- Road-block cases: pending transfers accepted during the batch phase
  (writes were frozen at step 5, so any pending-accept corpus is itself an
  incident to reconcile under governance, not a code path).

## 附: exit codes & semantics summary

| code | meaning |
| --- | --- |
| 0 | inspect printed; apply committed with an empty `failed` lane (conflicted/skipped lanes are governance entries, not failures) |
| 1 | storage failure, a missing/unloadable `--config-dir`/`BCS_CONFIG_DIR` chain, or ≥1 `failed` entry — fix the config/operator inputs, then re-run the same `--batch-id` to recover the batch from its committed prefix |
| 2 | usage error: missing `--maintenance`, over-limit page, blank batch id, unreadable/invalid confirmation file |

## 附: 2026-10-09 验收演练记录（Task 20 全链路验收）

本节是 release 前对上述 runbook 的实测记录（环境：无 MySQL，SQLite/memory 路径；
凭证不落日志）。逐条真实输出，未执行的项不在本节声称。

- `bcs-ownership-migrate inspect`（无 `--maintenance`）：真实子进程退出码 **2**
  （usage 语义，与上表一致）。
- `bcs-ownership-migrate --maintenance --help`：退出码 **0**。
- `--maintenance inspect` 缺 `--config-dir`/`BCS_CONFIG_DIR`：验收时实测曾为
  config-loader panic（退出码 101，见下条历史记录）。**终审修复（fix commit）
  已闭合**：binary 改走 fallible 的 `BcsConfig::try_load_with_env` 并映射为
  `RunFailure::Execution` → **退出码 1**，与上方 exit-code 表的 config 行一致，
  由真实子进程测试
  `binary_missing_config_is_a_typed_execution_failure_not_a_panic`
  （`crates/bootstrap/bcs/tests/ownership_migration.rs`）固定：exit 1、报错
  命名 config 问题、断言无 `panicked at`；`--maintenance` 缺失仍独立为退出码
  2。启动前失败、零写、不产生半迁移状态的语义不变。
- dry-run 与回退断言由 `crates/bootstrap/bcs/tests/ownership_migration.rs`
  以真实二进制子进程覆盖：inspect 前后库指纹断言**零写**；同 `--batch-id` 重放
  返回原报告（add-safe、不重置已转交 owner/version）；conflict/skip 车道是治理
  条目、不进 failed 车道；created_by 全程不变（§4 of 验收文档）。
- live MySQL 双实例/锁时序合同仍是 CI-pending（`#[ignore]` +
  `BCS_TEST_MYSQL_URL`，6 个套件的具体清单与未验证原因见验收文档 §2.1）；
  本环境已按“未验证”如实记录，不冒充通过。
