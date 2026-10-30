# Plan: 服务 Bot 知识库开关的分版本尽力记录

## Approach
沿用当前草稿选择与 `AGENTS.md` 投影，新增按发布版本保存、读取最近成功记录的普通 Module。只在既有异步构建任务开始处尝试一次独立保存，不修改原生发布事务、状态推进或上线成功回调；预发和生产复用相同版本记录。缺失、损坏或依赖故障均不补读草稿，且记录只表示采集时的选择，不是部署或文件一致性的证据。

文档基线为 `REL20261029@2508dcabaa5f533321325e0549b724cd916650d9`，新分支不含 PR #3177。用户已批准 SDD 与历史兼容策略，现按任务分组实现。

## Affected Components
- `core/bot_knowledge` — 草稿行为保留，增加采集和版本记录读取。
- `core/service_bot/services/publish_flow/tasks.py:303-350` — 唯一运行期集成位置，原生任务继续负责构建、部署及轮询。
- `di/modules/bot_knowledge_module.py:67-94`、`di/modules/service_bot_module.py:734-757` — 普通 DI；不增加 Plugin、事件总线或新任务种类。
- `adapters/http/bot_knowledge/router.py:38-84` — 保持原 GET/PUT，增加独立的版本记录只读端点。

以上路径相对 `apps/backend/src/agentclaw/community/`。后文新建路径为计划路径，并非已实现。

## Data Model Changes
None. 不增加表、字段、索引或迁移，不使用 `online_enabled`，不写 `ac_bots.ext`、`ac_bot_publish.ext`。

复用 `plugin_api/models.py:403-418` 的 `BotCommonConfig` 现有唯一键；该表本身没有租户列，命名空间必须显式纳入租户，不依赖隐式租户过滤。

```python
# core/bot_knowledge/publication_selection.py (new, storage coordinates sketch)
bot_id = f"knowledge:{source_bot_pk}"
entity_id = "tenant:" + sha256(avernet_tenant.encode("utf-8")).hexdigest()
config_key = f"publication:{publication_id}"
# Exact lookup: (bot_id, entity_id, env, config_key), is_delete=0.
# Hashing tenant avoids case/accent-insensitive collation collisions.
# Business Bot ID / current Owner are validated separately, not used as a
# mutable storage identity. Do not expose these internal coordinates to clients.
```

```jsonc
// ac_bot_common_config.config_value (new payload contract)
{
  "schema_version": 1,
  "avernet_tenant": "tenant-a", "env": "dev", "source_bot_pk": 42,
  "space_id": 7, "publication_id": 101, "version": 2,
  "kb_ids": ["kb-a"],
  "captured_at": "2026-10-29T04:00:00Z"
}
```

启用集合排序去重、最多 100 个 ID，沿用现有 ID 校验；序列化后 UTF-8 不超过 65535 字节。空数组是有效记录；不能把查询失败或权限不足转换为空数组后保存。保存复用 common-config Repository 的 `put`（`implementations/config/bot_common_config.py:143-182`），读取在本 Module 的独立会话按同一唯一键执行严格查询；原有 `get` 会将缺表降级为 None，不能用于区分本功能的 unavailable。均不修改通用 Repository 的兼容语义；写入异常由 Repository 正常抛出，由显式的附属记录方法处理并返回结果。

## API / Interface Changes
现有草稿端点、分页、CAS、个人 Bot 行为不变。不在原 GET 中混入历史选择，也不新增阶段开关写入口。

```python
# core/bot_knowledge/protocols.py / api/bot_knowledge_service.py
# New ordinary Service API, exported beside existing protocol; not a Plugin.
class BotKnowledgePublicationSelectionProtocol(Protocol):
    def record_build_selection(self, *, publication_id: int) -> bool: ...
    def get_publication_selection(
        self, *, scope: BotKnowledgeScope, actor_id: str, publication_id: int,
    ) -> dict: ...
# record: True means independently persisted; False means logged / not recorded.
# get: access errors propagate; auxiliary-storage failures return unavailable.
```

```python
# adapters/http/bot_knowledge/router.py (new endpoint sketch)
@router.get("/{bot_id}/publications/{publication_id}/selection")
def publication_selection(
    bot_id: str, publication_id: int, owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotKnowledgePublicationSelectionProtocol = Injected(
        BotKnowledgePublicationSelectionProtocol),
): ...
# Additive route; GET /{bot_id} and PUT /{bot_id}/{kb_id} unchanged.
```

```jsonc
// GET /api/bot-knowledge/{bot_id}/publications/{publication_id}/selection
{
  "publication_id": 101, "version": 2,
  "observation_status": "last_recorded",
  "kb_ids": ["kb-a"], "captured_at": "2026-10-29T04:00:00Z"
}
// Explicit all-off: last_recorded + kb_ids: [].
// Missing / invalid: unrecorded + kb_ids: null + captured_at: null.
// Auxiliary store failure: unavailable + kb_ids: null + captured_at: null.
// 403/404: access / resource ownership failure; never disguised as unrecorded.
```

调用方先沿用原生发布卡片的 `publication_id` 与状态，再查询上述只读端点。原生卡片已提供版本与发布状态（`service_publication_facade.py:306-337`）；本功能不调用全历史列表推导“当前生产”，不在读取中修复或触发发布。前端接入不在本次实现，不能仅凭新增后端端点声称页面已支持。

## Key Files & Functions
### K1. Recording and read contracts

```diff
# core/bot_knowledge/protocols.py:1-30
+# BotKnowledgePublicationSelectionProtocol, additive recording / read methods.
# api/bot_knowledge_service.py:3-5
+from agentclaw.community.core.bot_knowledge.protocols import (
+    BotKnowledgePublicationSelectionProtocol,
+)
+# Export the new Service API alongside the existing protocol.
```

### K2. One small implementation

```python
# core/bot_knowledge/publication_selection.py (new)
class BotKnowledgePublicationSelection:
    def __init__(self, db: DatabasePlugin,
                 configs: BotCommonConfigRepositoryProtocol) -> None: ...
    def record_build_selection(self, *, publication_id: int) -> bool: ...
    def get_publication_selection(self, *, scope: BotKnowledgeScope,
                                  actor_id: str, publication_id: int) -> dict: ...
# Private: resolve exact source + space, validate/encode bounded payload,
# form internal coordinates, map optional observation-store failures.
```

采集路径按发布单主键读取源 Bot；确认当前租户、环境、源 PK、业务 Bot ID、空间和 `BUILDING` 状态，然后读取当前空间最多 101 个 enabled ID。复用 `repository/store.py:32-114` 的 Bot/空间规则，但不调用 `list_enabled_current`：该方法在权限/空间失败时返回空集合（`:250-273`），不能用来记录“明确全部关闭”。不把历史发布 Owner 当作当前 Owner 权限，不跨源 PK 猜测继承。

读取路径先按 `scope` 解析当前 Bot 并执行原有编辑者可读检查，再精确读取发布单并核对源 PK、业务 ID、环境；规则参考原生 `_resolve_publication`（`service_publication_facade.py:188-213`）。无可信源映射的 legacy lineage 返回不存在/未记录，不扩大遗留资源恢复范围。DRAFT 视图始终使用原 current 接口；新端点只展示版本最近记录，不能覆盖草稿选择。只有访问校验完成后，辅助配置读取/解析异常才能降级。内容必须与当前 tenant/env/source PK/space/publication/version 全部匹配。

### K3. The only execution hook

```diff
# core/service_bot/services/publish_flow/tasks.py:303-350
+# Constructor: receive a lazy ordinary Service API provider, through lifecycle.
 if status == PublishStatus.BUILDING:
+    try:
+        self._selection_provider().record_build_selection(publication_id=publish_id)
+    except Exception:
+        logger.exception("knowledge selection observation failed: publish_id=%s", publish_id)
     build_result = await self._flow.execute_build_phase(record, operator)
```

上面是同步队列 handler 所在线程中的短 DB 操作，不新开异步后台记录任务。Service 内捕获已知附属依赖失败并返回 False；handler 外层隔离还覆盖惰性 DI 构造失败。不得包住原生 `execute_build_phase`：真实构建失败仍沿原有分支返回 Fail，不能被附属容错吞掉。`BUILT/VALIDATE_PUB/VALIDATING/ONLINE_PUB/SUCCESS` 路径不采集；重启和回滚不补写。

```diff
# core/service_bot/services/publish_flow/tasks.py:744-761
+# PublishTaskLifecycle.__init__: selection_provider is a required callable.
+# Only pass it to PublishVerifyFlowHandler; leave other handlers unchanged.
# di/modules/service_bot_module.py:734-757
+selection_provider=lambda: injector.get(BotKnowledgePublicationSelectionProtocol),
```

### K4. Assembly and delivery

```diff
# di/modules/bot_knowledge_module.py:67-94
+# Bind new Service API to ordinary implementation; constructor performs no I/O.
# adapters/http/bot_knowledge/router.py:38-84
+# Add publication_selection GET from the interface sketch above.
```

```diff
# core/bot_knowledge/README.md
-Current application selection only;
+Current selection plus best-effort publication observations;
+# Document last_recorded/unrecorded/unavailable and non-authoritative semantics.
# core/service_bot/README.md (Context Boundary)
+  - agentclaw.community.core.bot_knowledge.protocols
+# Task adapter consumes the ordinary Service API; native transactions unchanged.
```

### K5. Verification files

```python
# tests/community/core/bot_knowledge/test_publication_selection.py (new)
def test_empty_selection_is_not_missing(): ...
def test_source_tenant_space_and_current_owner_are_checked(): ...
def test_rebuild_failure_keeps_only_last_recorded_semantics(): ...
# tests/community/endpoints/test_bot_knowledge_router.py (existing)
def test_publication_selection_uses_real_di_and_does_not_change_current_get(): ...
# tests/community/core/service_bot/services/test_publish_tasks.py (existing)
def test_observer_and_lazy_provider_failure_do_not_fail_native_build(): ...
```

```python
# tests/community/di/test_bot_knowledge_publication_selection.py (new)
def test_real_binding_resolves_without_optional_remote_credentials(): ...
# tests/community/e2e/test_bot_knowledge_publication_selection.py (new)
def test_draft_a_staging_a_new_draft_b_keeps_version_a(): ...
# tests/community/adapters/http/test_two_phase_boot.py (existing)
# Extend the exact expected finalized route set with the new GET.
# tests/community/architecture/test_bot_knowledge_publication_observation.py (new)
def test_observation_is_not_in_native_completion_or_transaction_paths(): ...
```

## Dependencies
None. 不增加第三方包、新 Plugin、全局配置开关、新进程或任务类型。新普通 Module 使用现有 DatabasePlugin 与 common-config Repository；不依赖外部知识平台、身份文件或设备连接。

## Risks & Mitigations
- **Risk: 复用同一发布单不等于同一次构建。** 原生取消预发会将同一记录退回 DRAFT（`bot_publish_service.py:1252-1265`），回滚也会复用记录（`publish_rollback_mixin.py:159-189`）；ARCA 构建路径按 version 复用（`bot_build_service.py:391-402`）。因此路径 hash 不是构建代次证明。**Mitigation:** 本版只提供“该发布版本最近成功记录”，始终带采集时间；重建覆盖失败可以留下旧记录，不声称本次构建已记录。不新增 generation 或强一致校验。这是 review 时需明确接受的弱一致取舍。
- **Risk: DB 等待仍可能拖慢当前构建任务。** DatabasePlugin 没有逐调用 timeout 参数；公有 CommunityDatabase 也未声明短 SQL 截止时间。**Mitigation:** 常数次精确读取、无显式 FOR UPDATE、无重试，复用既有连接/驱动超时。交付前核实实际配置与故障表现；不把 try/except 描述为时间隔离，不添加无法取消的超时线程。不承诺独立的硬耗时上限；如必须保证硬上限，应重新 review，而不是悄悄改造全局数据库。
- **Risk: 同一底层数据库不可用也会影响原生发布。** **Mitigation:** 故障注入单独作用于辅助读写，证明不传播附属异常；不宣称数据库整体故障时原生仍可成功。
- **Risk: 当前表无 tenant 列、旧 Owner 与新 Owner 不一致。** **Mitigation:** 内部存储键包含 tenant 与不可变源 PK；所有外部读取先检查当前权限及空间；payload 再核对归属；不能按历史 owner 直接授权。
- **Risk: common-config 操作员可修改记录。** **Mitigation:** 沿用 require_operator 的既有管理权限（`adapters/http/bot_common_config/router.py:33-83`）；记录不是防篡改审计或授权真相。非法/错域 payload 降级，不为展示记录重做全局 CRUD 保护体系。
- **Risk: 文件同步落后、记录与运行不同。** **Mitigation:** 明确该记录只是采样选择，继续沿用现有 AGENTS.md 投影，不据此判断检索权限或部署状态。

## Alternatives Considered
- **原 PR 事务联动：** 与用户要求相反，不复用它的 online_enabled、生产摘要、发布结果标记或恢复保护。
- **三阶段各存一份并在状态变更时复制：** 增加复制入口和回滚方向问题；同一发布版本可共用一条记录。
- **发布单 ext 加辅助字段：** 即使 catch 异常也要修改原生 ext/CAS；选择独立配置存储。
- **artifact fingerprint / build generation：** ARCA 路径会复用，teclaw artifact stage 会被重新标记（`publish_flow/ext_state.py:219-224`）；不为非关键记录构建新的代次体系。保存时间与 last_recorded 语义更诚实也更轻。
- **完全零发布接触：** 缺少构建时自动记录触发点；接受一处任务入口的弱集成，不把它描述成零耦合。

## Rollout
新 PR 目标仍为用户已指定的 `REL20261029`，从干净 worktree 开发，不更新/关闭旧 #3177。实现阶段只修改新 worktree、提交本地任务并验证；远程交付、数据库变更和部署不随实现自动执行。个人 Bot 和已有草稿请求无需升级；历史发布版本读取 unrecorded，不回填。删除新增记录调用与读取端点即可撤回功能，不需原生状态恢复或数据库回滚。

```bash
# New worktree only; delivery commands are NOT run in this SDD pass.
git status --short
git diff --stat github/REL20261029...HEAD
# On an explicitly authorized later push:
AVERNET_PRE_PUSH_MERGE_TARGET=github/REL20261029 git push github HEAD
```

## Test Strategy
以真实 Module 的外部 Interface 为主；DI/HTTP 回归从真实组装解析，只替换外部平台边界。fault injection 分别覆盖 record 写入、可选配置读取、惰性 provider 构造；断言原生阶段方法与返回结果均未改变。另注入真实构建失败，证明不会被容错吞掉。

```bash
# Run from apps/backend after implementation and normal dependency setup.
DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/bot_knowledge \
  tests/community/core/service_bot/services/test_publish_tasks.py \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/di/test_bot_knowledge_publication_selection.py \
  tests/community/e2e/test_bot_knowledge_publication_selection.py
DEPLOY_PROFILE=test uv run pytest \
  tests/community/architecture/test_module_boundaries.py \
  tests/community/architecture/test_api_layer_is_protocols_only.py \
  tests/community/architecture/test_bot_knowledge_publication_observation.py
```

最后运行 Backend SAST、完整 Backend CI 与 changed-line gate；对基线已有失败单独复现，不顺手扩大修复。Singlebox 的 bot_knowledge 当前处于 pending（`singlebox/ci/singlebox_coverage_modules.yaml:328-332`），不在本 PR 改阈值或伪造设备验收。保留失败注入、查询数量、状态未变及实际未验收环境的证据；需要完整 Singlebox 时运行既有共享栈门禁，未运行则明确披露。
