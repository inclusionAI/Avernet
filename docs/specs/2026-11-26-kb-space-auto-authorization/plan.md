# Plan: TC 每库开关配置（含前后端，不执行授权）

## Approach
在现有知识管理边界内增加专用 TC 配置服务与 GET/PUT 接口，复用 `ac_common_config` 保存每库开关。工作台“已授权 Bot”页签增加配置开关；ECB 只供既有元数据/权限读取，不改代码、不保存开关、不执行 grant/撤权。

2026-11-27 按已确认的最小需求收敛，沿用开发分支及本目录；此前完整自动授权方案仅保留在 Git 历史，不是本次实施清单。所有代码片段均为待实施草图，功能未实现。本计划不建立任务、监听器或 Bot 名册。

## Affected Components
公共路径相对 Avernet；证据基于当前分支代码。工作台位于宿主闭源 `apps/frontend-nextgen/src/internal/knowledge/`，不应把其实现复制到公开仓库。

| Source | Evidence / change |
| --- | --- |
| `apps/backend/src/agentclaw/community/plugin_api/models.py:428` | `CommonConfig` 已有表，business_code/param_code 各 64 字符，唯一键为业务码＋参数码＋env。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/config/common_config.py:98` | 按完整业务键读取；现有 upsert 是覆盖写，不等同于乐观锁。 |
| `apps/backend/src/agentclaw/community/core/common_config/service.py:62` | 已有保留配置写保护，可扩展为本功能整个 business_code 保留空间。 |
| `apps/backend/src/agentclaw/community/core/knowledge_management/services/service.py:126` | 详情复用 Space 校验及 provider 元数据。新增配置路径不可调用成员同步激活或 grant mutation。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/catalog.py:221` | catalog-detail 提供 can_manage/status，但不能假定它提供创建者。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/space_knowledge.py:141` | 现有 KB state 解析包含 created_by 与 owner_staff_no；后者不等于创建者。 |
| `apps/backend/src/agentclaw/community/adapters/http/knowledge_management/router.py:145` | 复用已有认证、路由前缀与错误包装，增加配置子资源。 |
| `apps/backend/src/agentclaw/community/di/modules/knowledge_management_module.py:1` | 现有公开装配及宿主 identity/config override；在此注入配置服务。 |

读取边界：[知识管理](../../../apps/backend/src/agentclaw/community/core/knowledge_management/README.md)、[通用配置](../../../apps/backend/src/agentclaw/community/core/common_config/README.md)、[Backend](../../../apps/backend/README.md)、[架构规则](../../arch/arch.rules.md)。现有 common-config 通用读接口、人工授权和 Bot 启用接口语义保持。

## Data Model Changes
无新增表或 DDL。为本功能保留一个 business_code，param_code 为规范序列化的隔离键 SHA-256 完整十六进制值，长度 64。隔离元组含配置的 space_tenant、platform、environment、space_id 和原始 opaque kb_id；不截断 ID，不把浏览器传入的环境或平台作为可信隔离来源。

```python
# core/knowledge_management/toggle_models.py (new; under community/)
BUSINESS_CODE = "knowledge_bot_auto_authorization"
identity = [space_tenant, platform, environment, str(space_id), kb_id]
param_code = sha256(canonical_json(identity).encode("utf-8")).hexdigest()
# common-config enable is storage availability, NEVER the business switch.
record_enable = "1"
param_value = {"schema_version": 1, "identity": identity,
               "enabled": False, "revision": 1}
```

缺行返回 enabled=false/revision=0，读取不写库、不初始化。已有行必须校验 identity、schema、boolean 与 revision；损坏、身份不匹配、异常 disable 或数据库故障明确报错，不被 only_enabled 过滤成“缺失”。业务 OFF 仍保存记录且 enable=1；不会影响其他 common-config。

写入采用 expected_revision。复用已有唯一键保障首次创建；同一缺行并发写只有一方成功，唯一键冲突返回 409。已有行以读取的完整 param_value 做条件 UPDATE，新值 revision+1；影响行数为 0 返回 409。不使用无条件 upsert，不用时间戳充当 revision，不持锁等待远端 HTTP。

```python
# core/repository/implementations/config/knowledge_toggle.py (new)
# Same CommonConfig model; dedicated narrow repository, no generic policy bypass.
read(scope: ToggleScope) -> ToggleState
compare_and_set(scope: ToggleScope, *, expected_revision: int,
                enabled: bool) -> ToggleState
# UPDATE ac_common_config SET param_value=:new_value
# WHERE business_code=:business AND param_code=:key AND env=:env
#   AND param_value=:previous_value AND enable='1'
# INSERT for expected_revision=0; constraint conflict => configuration conflict.
```

repository 使用项目已有数据库事务/错误处理。只把可识别的目标唯一键冲突转成 409，其他写失败向上抛错；测试真实 SQLite 及企业适配，不假定现有通用 upsert 的非 SQLite 分支适用于所有部署。

## Authorization and metadata
GET 先验证当前 TC 空间访问和 KB 的既有读取权限，返回 can_update；PUT 每次重新校验以下条件，不能信任 GET 的旧结果或客户端提交的 creator/status：

```python
# core/knowledge_management/services/toggle.py (new)
context = catalog.toggle_context(space_id=space_id, kb_id=kb_id, actor_id=actor_id)
can_update = (context.actor_is_creator and context.can_manage
              and context.status == "active")
# PUT: require can_update before any config mutation.
# GET: readable but non-editable => stored value + can_update=False.
```

新增的 toggle_context 是 TC outbound port 的元数据组合，不是 ECB 新接口。适配器复用既有 catalog-detail 的 can_manage/status，以及既有 KB get 返回的 created_by；用现有可信身份 resolver 比较 actor 与 created_by。不能以 owner_staff_no、can_delete、空间管理员或“最后修改人”推导创建者。缺字段/身份映射失败/两份元数据空间或 KB 不一致则拒绝写入。两次远程读取不承诺与 ECB 权限变更分布式原子，但每次请求都实时校验，不长期缓存写权限。

实施前用实际部署的既有响应固定合同测试；这是只读适配核验，不授权改 ECB。若部署接口缺少必要信息，明确记录接口证据与无法校验的具体缺口，不能猜创建者放行。

通用配置服务的 create/upsert、按 ID update/delete、按业务键 delete、enable/disable 均拒绝本 business_code；按 ID 路径先读取原记录再检查。新增专用配置服务直接使用窄 repository，不经可任意写的通用服务。检查其他 HTTP/内部通用写入口，避免只保护页面；数据库管理员直接 SQL 不属于产品 API 保护范围。

## API Changes
新增 TC 配置子资源，沿用现有知识管理认证与返回包装。strict boolean、非负整数 revision、未知请求字段拒绝；space_id 和 kb_id 沿用现有格式校验。

```http
# adapters/http/knowledge_management/router.py (under community/)
GET /api/knowledge/spaces/{space_id}/knowledge-bases/{kb_id}/bot-auto-authorization
PUT /api/knowledge/spaces/{space_id}/knowledge-bases/{kb_id}/bot-auto-authorization
```

```jsonc
// adapters/http/knowledge_management/schemas.py — payload sketch
// GET / successful PUT response data
{"enabled": false, "revision": 0, "can_update": true}
// PUT request
{"enabled": true, "expected_revision": 0}
// Success means configuration persisted, never permission execution completed.
```

沿用现有 400/403/404/依赖错误转换；新增配置冲突 409 与配置损坏/存储不可用错误的可识别 code。读取错误不返回 false；保存超时未知时前端重新读取后显示真实结果，不自动重放。新资源没有 pending、批次、授权数量或后台任务状态。

## Frontend
在宿主 AuthorizedBotsPanel 顶部增加独立配置区域，不复用 canManage 作为开关写权限。读取 TC 的 enabled/revision/can_update；无法编辑时只读或禁用，保留原人工授权按钮的既有权限。

```ts
// host: apps/frontend-nextgen/src/internal/knowledge/domain.ts
export interface BotAutoAuthorizationConfig {
  enabled: boolean;
  revision: number;
  can_update: boolean;
}
// New component: BotAutoAuthorizationToggle.tsx
// Label: 自动授权当前空间下所有 Bot
// Always visible help: 本期仅保存配置，暂不执行自动授权
```

复用现有 UI 组件及语义 token、useKnowledgeResource/useKnowledgeMutation。加载/读取失败不把开关渲染成可信 OFF；请求中禁止重复提交，成功采用服务端响应，失败保留上次确认值并显示错误。409 刷新配置，请用户重新操作；切换库/空间取消旧请求，旧响应不能覆盖新 scope。配置错误单独展示，不阻断原授权列表/人工操作。不开全量 Bot 查询，不额外刷新授权计数，不产生轮询或后台进度。

## Key Files
公共源码缩写均相对 `apps/backend/src/agentclaw/community/`；测试相对 `apps/backend/`。

| Unit | Files |
| --- | --- |
| Model/port/service | `core/knowledge_management/toggle_models.py`、`ports.py`、`services/toggle.py`；`api/knowledge_management_service.py`（增加方法或同域专用 Service API 导出） |
| Persistence | `core/repository/implementations/config/knowledge_toggle.py`、同域 repository Protocol；复用 `plugin_api/models.py` 的模型，不改表 |
| Generic write protection | `core/common_config/service.py`；保留 business_code 常量放低层纯数据模块，避免 common-config 导入知识管理实现 |
| Metadata/HTTP/DI | `adapters/knowledge_platform/catalog.py`、`adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`di/modules/knowledge_management_module.py` |
| Frontend | 宿主 `src/internal/knowledge/components/{AuthorizedBotsPanel.tsx,BotAutoAuthorizationToggle.tsx}`、`domain.ts`、`services/knowledgeService.ts`、`api/managementController.ts`；按现有调用链拆分 |
| Documentation | 现有 knowledge_management README、common_config README、必要 Backend 导航；本目录验证记录 |

## Alternatives Considered
- ECB 保存开关：不符合唯一真源边界，不采用。
- 增加独立策略表：当前 boolean 配置已有存储可用，无证据需要，采用 common-config。
- 使用通用 enable 代表开关：会把关闭和记录不可用混淆，不采用；业务值单独保存。
- 直接复用无条件 upsert：会覆盖并发修改，采用窄 CAS 写操作，不改其他配置业务。
- 将 can_manage/can_delete 当创建者：权限语义不一致，不采用；核对真实 created_by。
- 接入授权任务预留未来功能：超出本期，完全不实现。

## Migration / Rollout
没有开关数据回填或 ECB 部署。先上线 TC 专用接口与通用写保护，再上线宿主 UI；旧库无配置保持 OFF。按实际部署核对 common-config 唯一键已存在，缺失为部署问题，不能自动执行 DDL。

回滚前端可隐藏新区域；保留配置记录及通用写保护，避免后续重新发布丢设置。由于无人消费开关执行授权，回滚不改变 grant/访问状态，也无需撤权任务。宿主集成所需公共 gitlink 在实现后固定，文档阶段不改。

## Test Strategy
覆盖 spec AC01–AC08；重点验证“只保存配置”的正向和零副作用断言。

```python
# apps/backend/tests/community/core/knowledge_management/test_toggle.py (new)
def test_missing_is_off_but_storage_error_is_not_off(): ...
def test_creator_without_manage_and_manager_without_creator_denied(): ...
def test_pending_delete_and_unknown_creator_cannot_write(): ...
def test_toggle_never_calls_grant_queue_members_or_publication(): ...
# apps/backend/tests/community/repository/config/test_knowledge_toggle.py (new)
def test_full_scope_isolation_and_long_opaque_kb_ids(): ...
def test_concurrent_first_insert_and_update_conflict(): ...
def test_malformed_record_and_hash_identity_mismatch_fail(): ...
```

补 common-config 所有写变体保护测试、HTTP 直接越权/严格 schema/错误映射及完整 DI/provider 合同测试。前端在组装树执行组件与类型检查：刷新回显、禁用权限、读取失败不显示 OFF、冲突/超时恢复、切换 scope 丢弃旧响应；人工授权功能仍能操作。

```bash
# apps/backend/ — focused checks after implementation
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/endpoints/test_common_config.py \
  tests/community/endpoints/test_knowledge_management.py \
  tests/community/knowledge_management tests/community/core/bot_knowledge \
  tests/community/architecture
```

新增测试路径创建后加入上述命令；HTTP 变更再执行 Backend README fast gate。宿主按当地 AGENTS 先 assemble，再在组装树运行，不在不完整闭源 src 直接声称 build 通过。组合验证使用真实 TC 保存/刷新，记录 ECB 仅元数据读取，确认 grant、enabled、人员 ACL、MCP 和发布选择无变更。未执行测试单列，设计文档通过不代表功能验收。
