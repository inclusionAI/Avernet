# OpenAPI Application Coding 实现计划

- **日期**：2026-08-24
- **关联方案**：`../specs/2026-08-24-openapi-application-coding-decision.md`
- **状态**：部分实施
- **实施进度**：Task 1–6、Task 8 已实施并通过当前回归测试；Task 7（删除 hosted workspace）**Blocked**，等待 corp-only DIMA delete contract。
- **Task 7 解阻最小契约**：HTTP/RPC 类型、endpoint/RPC 方法、workspace_id 参数位置、鉴权、成功/不存在/超时语义、幂等删除语义、request_id 透传方式。
- **Task 7 当前验收**：保留“非服务 applicationCoding 删除后无 hosted workspace 残留”未勾选；未拿到契约前不实现猜测性的 client/service 删除调用。

## 0. 两条硬约束（贯穿每个 Task）

1. **不影响原有常见逻辑**：普通 Bot、`claude_code`、`aicoding`、服务化的创建路径行为逐字节不变；`template_type` 未传时零变化。
2. **与原 `create_bot` 链路一致**：新增字段只做并列扩展，不重排、不改 `entity_id`/`engine_type`/`space_id` 的既有解析。

落地口诀：**可选字段并列扩展 + 校验前置 fail-closed + Application Coding 失败显式返回 + 复用现有 auth-status echo**。

---

## Task 1：契约定义 + 错误码

**目标**：`BotCreate` 可接收 `template_type` / `template_config`，未知字段仍被拒。

**文件**：
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py`
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/errors.py`
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/contracts.py`（如需新增公共错误响应模型）

**改动**：
- `BotCreate` 增加两个**可选**字段（保留 `extra="forbid"`）：
  ```python
  template_type: str | None = Field(default=None, description=...)
  template_config: dict[str, Any] | None = Field(default=None, description=...)
  ```
- `template_config` 使用**受控薄透传**：公共契约声明为 `dict[str, Any] | None`，不猜测或重塑内部字段形状。原因是历史 Application Coding 配置与新模板/引擎配置并非同一套结构：
  - 历史真实 fixture 使用扁平业务字段，例如 `devflow_workflow`、`yuque_kb_repos`、`code_repos`、`token`；
  - 当前引擎侧还消费 `bot_template_config.preset_capabilities`、`bot_template_config.ext_config.thetaKey` 等嵌套字段；
  - 因此不得引入 `model/runtime/repos/config` 这组未经历史契约证明的顶层字段。
- `template_config` 只做边界控制，不做业务字段重映射：
  - 必须是 JSON object；保留嵌套对象、数组、标量和未知业务字段；
  - `to_internal_template_config()` 只做深拷贝/JSON 归一化，不改变 key 层级，不把字段搬到 `bot_template_config`；
  - 客户端不得写入服务端管理字段：顶层 `dima_space_id`、`template_uid`、Bot ID、workspace 状态字段；这些由 Workspace Hosting 或 BaaS template resolver 生成/补充；
  - `template_key` 不在本期单独声明为公共字段；若历史校验器或模板 resolver 后续需要，必须另行补充契约，而不是借薄透传静默赋予公共语义；
  - 具体字段合法性由既有 Application Coding/模板校验器负责，校验失败统一映射为 `OPENAPI_BOT_TEMPLATE_INVALID`；公共层不得静默丢弃未知字段。

**已核历史 fixture**（`src/backend/tests/community/acceptance/bot_management/test_bot_live_lifecycle.py`）：
```python
old_template = {
    "devflow_workflow": "old-flow",
    "yuque_kb_repos": [],
    "code_repos": [],
    "token": "old-token",
}
```
该 fixture 用于既有 Application Coding Bot 的模板更新/回读，证明 legacy 配置不是 `model/runtime/repos/config` 形状。嵌套 `bot_template_config` 结构来自引擎/模板消费路径，公共 API 必须原样保留，不能擅自扁平化或包装。
- 定义稳定错误码常量（放错误/响应层，不与内部异常名耦合）：
  ```text
  OPENAPI_BOT_TEMPLATE_INVALID          # 模板参数非法 / template_config 单传
  OPENAPI_BOT_COMBINATION_UNSUPPORTED   # 引擎/部署/空间/服务化组合不支持
  OPENAPI_APPLICATION_CODING_UNAVAILABLE # Workspace Hosting 未绑定或不可用
  ```

**测试**：
- `template_type`/`template_config` 正常解析；
- 外层未知字段仍被 `extra="forbid"` 拒绝；
- 历史扁平字段与嵌套 `bot_template_config` 字段均原样保留；服务端字段（如 `dima_space_id`、`template_uid`）被拒；
- 只传 `template_config` 不传 `template_type` 被拒；

**不破坏常见逻辑的依据**：字段 `default=None`，不传不影响既有请求。

---

## Task 2：组合策略 + Workspace Hosting 绑定检查

**目标**：applicationCoding 的组合校验收敛成一个纯函数；复用现有 Workspace Hosting 绑定检查。

**文件**：
- `src/backend/src/agentclaw/community/core/bot_inventory/policies/combo_policy.py`（组合校验）
- `bot_management` 下复用 Workspace Hosting Service 的绑定检查

**改动**：
- 新增纯策略函数：
  ```python
  assert_application_coding_create(
      *,
      engine: str,
      bot_type: str,
      space_kind: SpaceKind,
      deployment_mode: DeploymentMode,
  ) -> ComboDecision
  ```
  规则：
  - `engine == "claude_code"`；Application Coding 不是 `engine=aicoding` 的别名，后者作为既有 AI Coding 引擎单独使用；
  - `bot_type == "personal"`；
  - `space_kind == PERSONAL`；
  - `deployment_mode == CLOUD`；
  - 服务端模板校验器按历史 Application Coding 配置规则校验必要字段，不用“至少其一”作为通用兜底。
  `space_kind` 必须来自 `BusinessSpaceContextProtocol.resolve_current()`，不能用 `bot_type` 推断个人空间；`deployment_mode` 必须来自现有后端部署/创建能力解析，不能由客户端新增字段绕过。
- 复用现有 Workspace Hosting Service 的绑定检查，不新增独立的 capability probe protocol、探测任务或 Application Coding 专属运维 flag。
  - 未绑定真实 hosting 实现时直接返回 503 `OPENAPI_APPLICATION_CODING_UNAVAILABLE`。
  - 已绑定但创建 workspace 失败时，按创建失败处理并透出错误；不能通过吞异常或访问私有地址伪装成可用。
  - hosting 的具体 client/URL 仍留在 core 的既有 service/adapter 边界内，不泄漏到 HTTP 契约。

**测试**：§13.2 组合矩阵（通过/拒绝两向）+ `space_kind`/`deployment_mode` 真实值校验 + 未绑定 hosting 返回 503。

---

## Task 3：路由层 preflight（副作用之前 fail-closed）

**目标**：所有校验发生在 passport 申请、insert、设备、workspace **之前**。

**文件**：`src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`

**改动**：
- 在现有 `if body.engine not in _get_engine_types()` / `validate_engine_cluster` / `_require_service_capable_engine` 之后，先调用 `resolve_current` 获取 `space_kind`，再执行 Application Coding preflight；所有副作用（Passport、insert、设备、workspace）之前完成：
  ```python
  if body.template_type == "applicationCoding":
      # 组合校验（engine/bot_type/space_kind/deployment_mode）
      # + 按历史配置校验器校验 template_config
      # 组合不支持 → 409(error_code=OPENAPI_BOT_COMBINATION_UNSUPPORTED)
      # hosting 未绑定/不可用 → 503(OPENAPI_APPLICATION_CODING_UNAVAILABLE)
  elif body.template_type not in (None,):
      # 不支持模板类型 → 422(error_code=OPENAPI_BOT_TEMPLATE_INVALID)（模板类型非法，对齐 §7.3，与组合 409 区分）
  elif body.template_config is not None:
      # 有 config 无 type → 422(OPENAPI_BOT_TEMPLATE_INVALID)
  ```
- `BotCreateSpec(...)` 并列增加 `template_type=body.template_type, template_config=to_internal_template_config(body.template_config)`，**不动** `entity_id`/`engine_type`/`space_id`。
- `_complete_auth_status` 使用同一套 preflight；不能只在 POST 校验，不能让 echo 参数绕过空间/部署/模板策略。

**测试**：端到端拒绝矩阵（§7.2）+ 普通 Bot `template_type=None` 回归。

---

## Task 4：202 异步授权参数透传（复用现有 echo 契约）

**目标**：授权完成后模板参数不丢；不新增 create-intent 表，不改变现有授权状态契约。

**关键事实（已核）**：当前 `auth-status` 轮询通过 query echo 重建 `BotCreateSpec`（`engine`/`bot_type`/`bot_name`/`bot_desc`/`space_id`），且 `BotAuthStatusPoll` 使用 `extra="forbid"`。当前没有 pending spec 持久化结构，本期继续沿用现有 echo 机制。

**文件**：
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/schemas.py`
- `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`
- `src/backend/src/agentclaw/community/core/bot_management/create_flow.py`
- 相关 OpenAPI 授权流程测试

**改动**：
- `BotAuthStatusPoll` 增加可选 `template_type` / `template_config`，保持 `extra="forbid"`；
- `auth-status` 使用与 POST 相同的模板 DTO 校验和 Application Coding 组合 preflight；
- 将轮询请求中的模板字段转换为 `BotCreateSpec`，不改既有 `entity_id` / `engine_type` / `space_id` 解析；
- 保留已有 `bot_id`、`space_id` 等 echo 字段，兼容现有客户端；
- 本期不新增 `Idempotency-Key` 公共请求头，不把 response `request_id` 当作业务幂等键；
- 不新增 create-intent 表，不新增 migration；
- 授权失败或参数非法时不创建 Bot；普通 Bot 的现有授权行为保持不变。

**已知边界（过渡态）**：本 Task 复用 `auth-status` echo（与 decision §8.2 一致），标注为**过渡态**——客户端丢失参数、服务重启后的 pending 意图恢复、并发 poll 的严格单写 claim 不在本期范围，后续若有要求再单独建设持久化 intent。

**测试**：
- POST 返回 202 后，auth-status 回传模板参数能够完成创建；
- 模板配置较大或包含嵌套字段时不会在请求解析和 spec 转换中丢失；
- `to_internal_template_config` 不改变历史扁平字段和 `bot_template_config` 嵌套层级；
- 普通 Bot 老客户端仍可带 `space_id` 完成授权；
- 重复 poll 不改变现有行为；
- 授权失败不产生 Bot；
- 模板字段非法或组合不支持时，在创建副作用前拒绝。

---

## Task 5：Application Coding 创建失败处理（复用现有存储）

**目标**：不新增 workspace 资源表和通用创建 Saga；Application Coding 创建失败时不留下可用性错误的 Bot，沿用现有 Bot/template 存储完成必要清理；普通 Bot 路径不变。

**文件**：
- `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`
- `src/backend/src/agentclaw/community/core/bot_management/create_flow.py`
- `src/backend/src/agentclaw/community/core/bot_management/services/aicoding/workspace_hosting_service.py`
- 相关 workspace / create_flow / bot_service 测试

**改动**：
- Application Coding 的 workspace 创建失败必须作为创建失败返回，不能继续创建一个没有 workspace 的 Bot；
- 在现有 `create_bot` 失败路径中，按已有能力清理已插入的 Bot、模板记录、设备和 Passport；不改普通 Bot 的创建顺序和异常语义；不承诺为本期新增全量原子补偿；
- workspace 创建成功后，继续将 `dima_space_id` 写入现有 `ac_templates.ext`；
- workspace 创建失败但远端结果不确定时，本期记录 request_id 和错误日志，由调用方重试或人工补救；不新增 UNKNOWN 资源表和后台 reconcile；
- 现有 `ensure_hosted_workspace()` 继续作为历史 Bot 的手动补救入口；新建链路不调用该补救 API。

**明确不做**：
- 不新增 `create_intent` 表；
- 不新增 `hosted_workspace_resource` 表；
- 不新增 CreationCompensationService；
- 不新增 cleanup pending 状态机和 reconcile 任务；
- 不引入 Application Coding 专属 `Idempotency-Key` 必填契约。

**已知边界（明确 defer）**：owner relationship 写失败导致的「Bot 已建但 owner 关系缺失」孤儿，本期**不修**——仅验证其失败显式报错、不误报成功，压后续与 intent 持久化一并处理；workspace「超时但远端已成功」的孤儿同样走记录 request_id/日志 + 重试/人工补救，不引入 reconcile。

**测试**：
- workspace 返回失败和抛异常时，创建失败且不返回成功 Bot；
- device 失败时，已创建的 Bot/template 按现有失败路径清理；
- owner relationship 失败时，验证现有错误处理，不把失败误报为成功；
- 普通 Bot 回归不变。

## Task 6：ready 语义——现状已实现，仅补测试

**目标**：确认 `READY` 经 `status` 暴露、不新增 `auth-status` 状态；**不改 `readiness.py`**。

**关键事实（已核）**：`readiness.py:is_bot_ready(52-84)` **已完整实现** decision §10 语义：
- `needs_repos = template_type == "applicationCoding" and active_engine in ("aicoding","claude_code")`；
- `ext.start_status == "SUCCEEDED"` 才视为成功；
- 历史无 marker 但 binding 已 ACTIVE 的兼容放行（`missing_marker_on_active_binding`）；
- 非 application bot 忽略 start_status（保留既有行为）。

**改动**：**本 Task 无生产代码改动**，仅补测试断言：
- `READY` ≡ `is_ready==true`（`status==ACTIVE && binding==ACTIVE && start_status==SUCCEEDED`）；
- ready 不早于 workspace 初始化完成（`start_status` 尚未 `SUCCEEDED` 时不 ready）；
- 历史无 marker + binding ACTIVE 兼容放行；显式 `PENDING`/`FAILED` 仍阻塞。
- 若 `GET /bots/{id}/status` 尚未复用 `is_bot_ready`，则该处接线为小改动单独核，**不应重写 readiness 逻辑**。

**测试**：ready 语义 + 历史兼容。

---

## Task 7：applicationCoding 删除清理 hosted workspace（补齐现有 hosting 删除能力）

**目标**：非服务 applicationCoding Bot 删除时回收 hosted workspace；复用 `ac_templates.ext.dima_space_id`，不新增 workspace 资源表；普通 Bot 删除路径不变。

**文件**：
- `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`（`delete_bot`）
- `src/backend/src/agentclaw/community/core/bot_management/services/aicoding/workspace_hosting_service.py`
- `src/backend/src/agentclaw/community/core/bot_management/services/aicoding/workspace_hosting_client.py`（如当前 client 尚无删除方法）

**关键事实（已核）**：现状 `delete_bot` 只做 owner 校验、最早 Bot 保护、设备/Passport 清理和 Bot soft-delete，不调用 workspace hosting 清理。workspace ID 当前保存在 `ac_templates.ext.dima_space_id`。

**改动**：在 `delete_bot` 的**非服务**删除分支，对 `template_type == "applicationCoding"`：
1. 校验调用方权限；
2. 读取现有模板配置中的 `dima_space_id`；
3. 先通过 Workspace Hosting Service/Client 调用 workspace 删除能力（如当前 client 尚未提供，则补齐该薄封装；明确“不存在”视为已删除）；
4. workspace 删除失败或结果不确定时返回错误，不继续报告 Bot 删除成功；
5. workspace 删除成功后，沿用现有模板、设备和 Bot 删除逻辑。若后续既有删除步骤失败，允许 Bot 暂时保留并通过重试处理；不新增跨资源补偿状态。

- 普通 Bot（`template_type is None` / 非 applicationCoding）删除路径完全不变；
- 服务 Bot 仍走发布生命周期，普通 DELETE 不得绕过 `RELEASED` / `UPGRADED` 历史检查；
- 本期不新增 `DELETING` / `DELETE_PENDING_CLEANUP` / `DELETED` 资源状态和后台 reconcile；未能确认 workspace 删除结果时由接口返回失败，后续通过重试或现有人工补救处理。

**测试**：
- 非服务 applicationCoding 删除会读取并清理 `dima_space_id`；
- workspace 不存在时删除幂等；删除能力只补齐现有 hosting client/service，不新增资源表；
- workspace 清理失败时不返回删除成功；
- 服务 Bot 普通删除仍被拒绝；
- `RELEASED`、`UPGRADED` 历史不会被普通删除误删；
- 普通 Bot 删除行为不变。

**不破坏常见逻辑的依据**：仅 applicationCoding 且非服务分支增加 workspace 清理，普通 Bot 删除逻辑不动。

## Task 8：回归与收口

**目标**：证明「原有常见逻辑不变」。

- **改前改后语义兼容对比**：`template_type` 未传的创建请求，断言**语义等价**（业务字段/状态/engine 值/副作用一致）；新增可选字段（`template_type=null`/`template_config=null`）的序列化差异**不做逐字节比对**，避免误报。
- **runtime 路由基线**（§13.5）：`claude_code + applicationCoding` 仍命中 `aicoding` adapter、`claude_code + normalCC/空` 仍命中 `claude_code` adapter。
- 全量模块测试：`bots` 端点、`create_flow`、`bot_service`、`readiness`、`combo_policy`、workspace hosting。
- 更新契约文档 + 上下文边界元数据；同步 `errors.py`/`responses.py`/路由 responses 与 OpenAPI schema；若触及架构边界，同步架构测试（不只改 Python schema）。

---

## 实施顺序与依赖

```
Task 1 (契约/错误响应) ─┬─> Task 3 (resolve + preflight)
                         │
Task 2 (策略/hosting 绑定检查) ─┘
Task 4 (授权参数透传) ─> Task 5 (创建失败处理) ─> Task 6 (ready)
Task 5 ─> Task 7 (删除 workspace) ─> Task 8 (回归)
```

- Task 1/2 可并行；Task 3 必须等待 Task 2 明确 `space_kind/deployment_mode` 与现有 hosting service 的绑定检查入口。
- Task 4 必须在 Task 1 确定 auth-status 模板字段兼容规则后实施。
- Task 5 依赖 Task 2 的 hosting 绑定检查；Task 7 复用现有模板配置中的 `dima_space_id`，不依赖新资源表。
- Task 8 必须在 Task 1–7 全部完成后做基线对比。

---

## 风险与回滚

- **Hosting 依赖**：未绑定真实 Workspace Hosting → 创建前 503（`OPENAPI_APPLICATION_CODING_UNAVAILABLE`）；不新增 Application Coding 专属 flag/probe 基础设施。组合不支持仍返回 409。
- **向后兼容**：未传 `template_type`/`template_config` 的请求行为不变；新增字段 `default=None`。
- **异步参数透传**：模板参数依赖 auth-status echo，客户端未回传时拒绝创建，不静默创建普通 Bot。
- **workspace 不确定结果**：本期通过错误返回、request_id 日志和重试处理，不引入后台 reconcile。

---

## 验收清单（对照方案 §17）

- [x] `template_type`/`template_config` 可经 OpenAPI 提交，未知字段仍拒。
- [x] `applicationCoding + 云端 + 个人 + 非服务` 的前置准入链路已实现。
- [x] 本地/服务/团队/非 Coding 引擎的 applicationCoding 全部后端拒绝（非前端隐藏）。
- [x] Workspace Hosting 未绑定 → 创建前 503；前置检查不触发 Passport/Bot/设备/workspace。
- [x] 202 授权完成后模板参数透传；POST auth-status 使用同一套校验。
- [x] Application Coding workspace/template 创建失败不返回成功 Bot；已有清理路径按现状执行，不承诺本期全量原子补偿。
- [x] 不新增 create-intent / hosted-workspace 表，不新增 reconcile。
- [x] `READY` 经 `status` 暴露，不与 `auth-status` 混契约。
- [ ] 非服务 applicationCoding 删除后无 hosted workspace 残留（Task 7 Blocked，等待 DIMA delete contract）。
- [x] 普通 Bot / `claude_code` / `aicoding` / 服务化创建行为已完成相关回归测试。