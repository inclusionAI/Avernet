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

## Enablement Follow-up
结构只读核对完成后，改动如下，不修改业务算法或 schema：
```diff
 # configs/application.yaml / user_config.bot_knowledge_publication
- enabled: false
+ enabled: true
```
同步三种 community 配置快照及当前 README。先以真实默认 DI 回归证明旧配置失败，再验证 true/false 两种生命周期及默认配置下正常服务/仓储接线，最后重跑原相关回归集与推送门禁。

## Failed Online Restart Retry Repair
```python
# Internal Service + Repository contract; no HTTP route/parameter:
restore_failed_online_restart(*, publish_id: int, expected_ext: dict | None)
# Flow.retry uses it only for rollback_status == SUCCESS.
# Stage changed artifacts before locks, validate current FAILED/ext/source/env
# and latest failed ledger; commit retry=True and SUCCESS without KB effects.
```
A bounded restart_retry helper keeps the publish repository below 1000 lines. Queries use publish PK and existing operation key prefixes. A latest RESTART workflow is authoritative; an abandoned RESTART without workflow permits the observer's latest failed FIRST_RELEASE fallback matching ext.restart.online. No marker-only authorization or knowledge replacement. Disabled policy retains native CAS compatibility.

## Approved legacy-Owner draft-restore repair (2026-10-29)
Native source resolution treats a live source_bot_pk plus bot_id as authoritative even when old publication owner_id does not equal the current immutable entity_id. Knowledge integration must not turn that supported native restore into failure after device success. Split native source locking from knowledge identity trust: keep PK/bot_id/env/tenant/service/nondeleted scope, writer reservation and refresh; keep strict entity equality for build/online completion. During restore, an entity mismatch on either draft or previous publication produces knowledge UNKNOWN, preserves working/online flags and revisions, and atomically completes the native ledger. Latest draft/source version/operation/attempt/deadline and replay guards remain mandatory. No fallback, history backfill, schema, startup, config or deployment change.
Rejected: swallowing every source mismatch (loses isolation), or globally removing entity checks (would trust foreign snapshots). Verification uses the already approved native Flow/Service/Repository/operation seam, real PK Bot lookup and external device boundary doubles, plus negative identity/ledger cases.

```python
# knowledge_selection.py (internal helpers only)
def _locked_native_source(session, publish): ...  # PK/bot_id/env/tenant/service/live

def _locked_source(session, publish):
    source = _locked_native_source(session, publish)
    if source.entity_id != publish.owner_id:
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    return source
# restore_draft uses native lock, then checks both publication entity locators
# inside the knowledge-only UNKNOWN boundary, before validate_snapshot/bindings.
```
