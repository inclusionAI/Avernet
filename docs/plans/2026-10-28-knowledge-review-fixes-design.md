# Plan: Knowledge publication review fixes

## Approach
按批准的五项复审建议逐项做最小修复。复用现有短事务、原生 CAS 和 artifact offloader，不新增发布子系统。测试先复现再修复。

## Changes / Propagation
```python
# di/container.py: build_injector -- pure configuration resolution on every profile
_app_injector.get(KnowledgePublicationPolicy)
# publishing/bot_publish.py -- applies only when integration is enabled
if online_success and observed_workflow_id is None:
    raise KnowledgeError("KNOWLEDGE_OBSERVATION_REQUIRED")
```
```python
# config_artifact_offload.py -- choose offload from the complete inline ext budget
# prepare remains pure and immutable/content-addressed; preserve old oversized rule.
# publishing/knowledge_selection.py -- separate preparing a frozen candidate from committing it
# publishing/bot_publish.py -- fast path commits in one transaction; if a new OSS PUT
# is necessary, release locks, upload, reacquire and recapture the same-generation
# candidate; commit only when status/ext/selection/scope still match. Bounded retry.
# Existing markers normalize via GET outside locks. Supplied ext with a changed
# baseline is a CAS miss, not retried against newer metadata. Completion preflight
# reserves 2 KiB for its compact result; final pending keys must match staged keys.
```
```python
# knowledge_selection.py -- use existing source PK and preserve shared writer lock
# restore -- enabled-index seek (limit 101) + exact selected-bindings seek (<=100)
```
Service/Repository protocols and README describe observed completion; internal callers and fixtures forward observed workflow explicitly. No HTTP body field exposes that authority. Native disabled writes retain behavior. Existing retry endpoints remain, but may not synthesize success while opted in.

## Data / Dependencies
None: no package, ORM, DDL, table or index change.

## Alternatives Considered
- Only filter the HTTP adapter: rejected; leaves other shared write callers inconsistent.
- Lower artifact threshold globally / always offload: rejected; unnecessary I/O and canonicalization changes to old records.
- Upload while freezing under locks: rejected; breaks the short local-only transaction contract.
- Add indexes: unnecessary when source PK and existing binding indexes suffice.

## Risks / Mitigations
- Admission/upload race: bounded recapture with native exact ext and selection checks; cleanup immutable orphan if record was deleted.
- Generic success consumers: explicit intentional enabled-mode refusal; real observer already supplies workflow; fix test fixtures to exercise that contract.
- Alternate SQL dialects: compile checks plus SQLite bounded-work witnesses; MySQL/OceanBase plans remain rollout evidence, not local claims.

## Test Strategy
Focused config/DI, real handler/service/repository status tests, below-threshold artifact with maximum multibyte selections, OSS failure/CAS/delete races, same-name/disabled-history VM tests, all affected publishing/knowledge/services/config/architecture suites and lint.

## Approved default enablement follow-up (2026-10-28)
用户明确要求通过 ODC CLI 只读核对指定数据库，结构就绪后在本 PR 开启发布集成，不处理历史。已用 SHOW CREATE TABLE 核对知识绑定、通用配置、发布表，并用 SHOW INDEX 核对源 Bot 主键/唯一键；知识字段和所需索引均存在。公开文档不记录内部数据库 ARN、域名或调用者信息。

选择：随包 application.yaml 显式 enabled: true；仍允许部署显式设 false，缺失配置/直接构造策略对象的兼容 fallback 保留 false。拒绝仅开启本机配置（不能随 PR 交付）或修改全局策略 fallback（无必要扩大直接构造调用者影响）。不修改数据库、历史 UNKNOWN 语义或消费端。验证现有配置合并/DI 生命周期/发布 HTTP 服务边界及相关完整回归。

## Approved failed-online-restart retry repair (2026-10-28)
The user approved fixing native FAILED(source_status=success) -> SUCCESS retry recovery and updating this PR. Use a dedicated internal Service/Repository entry, accepting only publish_id and expected_ext. Validate FAILED, exact ext CAS, source_status, environment and the latest failed online restart ledger operation in one transaction. Recreated targets follow the native observer's RESTART-without-workflow / ext.restart.online / FIRST_RELEASE rule. Only add retry and restore native status; do not apply knowledge effects. Keep disabled compatibility.
Rejected: a generic bypass boolean or loosening all SUCCESS transitions (would reopen legacy status synthesis); a fabricated observed workflow (recovery is not observed completion). Test the real Flow/Service/Repository/ledger with SQLite; double only external observation/new restart issuance. Include empty knowledge, recreation, stale ext/ledger/status, rollback and generic-gate regressions. No schema or live operations.

## Approved legacy-Owner draft-restore repair (2026-10-29)
Native source resolution treats a live source_bot_pk plus bot_id as authoritative even when old publication owner_id does not equal the current immutable entity_id. Knowledge integration must not turn that supported native restore into failure after device success. Split native source locking from knowledge identity trust: keep PK/bot_id/env/tenant/service/nondeleted scope, writer reservation and refresh; keep strict entity equality for build/online completion. During restore, an entity mismatch on either draft or previous publication produces knowledge UNKNOWN, preserves working/online flags and revisions, and atomically completes the native ledger. Latest draft/source version/operation/attempt/deadline and replay guards remain mandatory. No fallback, history backfill, schema, startup, config or deployment change.
Rejected: swallowing every source mismatch (loses isolation), or globally removing entity checks (would trust foreign snapshots). Verification uses the already approved native Flow/Service/Repository/operation seam, real PK Bot lookup and external device boundary doubles, plus negative identity/ledger cases.
