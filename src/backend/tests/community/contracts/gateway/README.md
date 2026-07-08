# Gateway 契约测试实现文档

## 概述

基于 `src/mobile/docs/Gateway 透明转发接口文档.md`，对 Gateway 透明转发的全部上游接口实施契约测试。测试直接调用后端路由 Handler（非经由 Gateway 代理），通过 Mock 数据断言响应字段符合文档规范。

**当前状态**: 166 tests, all passing (63 HTTP + 16 Service-backed HTTP + 69 WebSocket + 10 Schema Conformance + 8 BCS Mock Conformance)

## 测试架构

### 目录结构

```
tests/contracts/gateway/
├── conftest.py                    # 共享 fixtures 和断言工具
├── schema_utils.py               # Schema 快照与契约验证工具
├── schema_snapshots/             # Pydantic 模型 JSON Schema 快照
│   ├── DeviceBindingResponse.json
│   ├── SkillSetResponse.json
│   ├── ... (35 个 Pydantic 模型快照)
│   └── bcs/                      # BCS/Engine 权威契约 Schema
│       ├── group_response.json
│       ├── session_response.json
│       ├── bot_detail_response.json
│       └── ... (11 个 BCS/Engine 契约 Schema)
├── test_schema_conformance.py    # Schema 快照与 Mock 数据契约测试
├── test_service_backed_api_contracts.py  # 真实 Service + SQLite Repo API 契约
├── docs/                          # 本文档目录
├── __init__.py
├── test_rule01_bot_management.py  # Rule #1  Bot 管理
├── test_rule02_bot_public.py      # Rule #2  公开 Bot 互动
├── test_rule03_expert_chats.py    # Rule #3  专家对话
├── test_rule05_devices.py         # Rule #5  设备连接
├── test_rule06_token.py           # Rule #6  Token 交换
├── test_rule07_bcs_groups.py      # Rule #7,7b,12,12b,14  BCS 群组/会话/Bot
├── test_rule08_engine_proxy.py    # Rule #8  Engine 代理
├── test_rule10_skills.py          # Rule #10 技能市场
├── test_rule13_cron.py            # Rule #13 定时任务
├── test_rule15_skillsets.py       # Rule #15 能力集管理
├── test_rule16_mcp.py             # Rule #16 MCP 市场
├── test_rule17_mcp_meta_auth.py   # Rule #17 MCP 办公网鉴权
├── test_ws01_engine_chat.py       # WS-1/1a Engine Chat Moltis 协议
├── test_ws01a_grt_chat.py         # WS-1a GRT Expert Chat 协议
└── test_ws02_bcs_group_chat.py    # WS-2   BCS 群聊协议
```

### 测试策略

| 策略 | 适用接口 | 实现方式 | 数量 |
|------|----------|----------|------|
| **A: DI Mock** | AgentClaw 后端路由 (Rules 1,2,3,5,6,10,13,15,16,17 部分) | FastAPI `TestClient` + `bind_mock_service()` 替换 DI 注入的服务 | 37 |
| **B: HTTP Mock** | BCS/Engine 代理路由 (Rules 7,8,17 部分) | `@responses.activate` 拦截上游 HTTP 请求 | 26 |
| **C: WS 协议契约** | WebSocket 协议 (WS-1, WS-1a, WS-2) | 数据类序列化/反序列化 + `MockWebSocket` + `AsyncMock` | 69 |
| **D: Schema 快照** | Pydantic 响应模型 | `model_json_schema()` 导出 → 快照对比，检测字段变更 | 2 |
| **E: Mock 数据契约** | BCS/Engine Mock 数据 | `jsonschema.validate()` 验证 mock 数据符合权威 JSON Schema | 8 |
| **F: Service-backed API 契约** | 真实后端 service 路径 | FastAPI `TestClient` + 真实 Service + SQLite Repo seed + 完整 response 快照 | 16 |

**策略选择依据**:
- 后端自身实现的路由 → 策略 A（直接调用 Handler，Mock 其 DI 依赖）
- Gateway 透传到 BCS (Rust) 或 Engine (Python) 的路由 → 策略 B（Mock 上游 HTTP 响应）

## 核心基础设施

### `conftest.py` — 共享 Fixtures

#### 认证 Override

```python
MOCK_USER = BuserviceUser(id="test-gw-user-id", operatorName="test_operator",
                          outUserNo="448524", nickName="TestUser", realName="测试用户")

MOCK_REQUEST_CTX = RequestContext(user_id="448524", bot_id="default", nick_name="TestUser")
```

`gw_client` fixture 覆盖三个认证依赖:

| FastAPI Depends | Override 函数 | 适用路由 |
|-----------------|--------------|---------|
| `get_current_user` | `_mock_get_current_user` | `/api/mcp/*`, `/api/cron/*`, `/api/v1/bot-public/*` |
| `require_operator` | `_mock_require_operator` | `/api/bots/{id}/detail-by-owner` |
| `get_request_context` | `_mock_get_request_context` | `/api/bots/*`, `/api/skillsets/*`, `/api/skills/*`, `/api/v1/resources/*` |

> **关键发现**: 大多数 `/api/bots/*` 和 `/api/skillsets/*` 路由使用 `Depends(get_request_context)` 而非 `get_current_user`。只 override 前两者会导致 401。

#### DI Mock 绑定

```python
def bind_mock_service(service_cls: type, mock_instance: MagicMock, app):
```

工作原理:
1. 创建 `_OverrideModule` 将 `service_cls` 绑定到 `mock_instance`
2. `injector.binder.install(_OverrideModule())` 覆盖 `Binder._bindings`
3. **清除 `SingletonScope._context` 缓存** — 这是关键步骤，`binder.bind()` 虽然覆盖了绑定，但 `SingletonScope._context` 保留了旧实例会短路新的解析

#### 断言工具

| 函数 | 用途 |
|------|------|
| `assert_has_fields(data, fields, label)` | 检查 dict 包含所有必需字段 + 类型。`fields` 支持 `list[str]`（仅存在性）或 `dict[str, type\|tuple]`（存在性+类型） |
| `assert_response_schema(json, required_top, data_fields, ...)` | 检查完整 API 响应结构，参数同上支持类型断言 |
| `assert_response_data_contract(json, snapshot_name, update)` | 用实际响应 `data` 校验严格 JSON Schema 快照，补足 `ApiResponse`/`dict` 宽泛模型盲区 |
| `assert_api_response_contract(json, snapshot_name, update)` | 用完整 API response 校验严格 JSON Schema 快照，覆盖顶层 envelope + `data` |
| `assert_success(json, label)` | 断言 `success=True` |

**`FieldSpec` 类型定义**:
```python
FieldType = type | tuple[type, ...]   # 如 str, int, (dict, list, type(None))
FieldSpec = dict[str, FieldType]      # 字段名 → 类型（同时检查存在性 + 类型）
```

**用法示例**:
```python
# 检查字段存在 + 类型
assert_has_fields(data, {"id": int, "name": str, "status": str}, label="...")

# 类型允许联合
assert_has_fields(data, {"data": (dict, list, type(None)), "total": int}, label="...")

# 严格模式：同时检查是否有未声明的额外字段
assert_has_fields(data, {"id": int, "name": str}, label="...", strict=True)
```

**类型断言优势**: 当接口响应字段类型不匹配时（如 `id` 从 `int` 变为 `str`），类型断言能立即捕获而非静默通过。

### `assert_no_extra_fields` — 检测未声明字段

```python
def assert_no_extra_fields(data: dict, allowed_fields: set[str] | dict, label: str = "") -> None:
```

当 API 响应新增了前端未预期的字段时，此断言可以立即捕获。通常与 `assert_has_fields` 配合使用：

```python
# 方式1: 分开检查
assert_has_fields(data, {"id": int, "name": str}, label="...")
assert_no_extra_fields(data, {"id", "name"}, label="...")

# 方式2: 使用 strict=True 一步到位
assert_has_fields(data, {"id": int, "name": str}, label="...", strict=True)
```

### `schema_utils.py` — Schema 快照与契约验证工具

| 函数 | 用途 |
|------|------|
| `model_to_json_schema(model_cls)` | 从 Pydantic 模型导出 JSON Schema |
| `save_snapshot(model_cls, name)` | 保存模型 JSON Schema 为快照文件 |
| `load_snapshot(name)` | 加载快照文件 |
| `diff_schemas(current, snapshot)` | 对比两个 JSON Schema，返回差异列表 |
| `validate_model_against_snapshot(model_cls, name, *, update=False)` | 验证当前模型 Schema 与快照匹配 |
| `validate_mock_against_schema(mock_data, schema, label)` | 验证 mock 数据符合 JSON Schema 契约 |
| `load_contract_schema(name)` | 加载 `schema_snapshots/bcs/` 下的 BCS/Engine 契约 Schema |
| `infer_json_schema(data)` | 从实际响应 `data` 推导严格 JSON Schema，递归设置 `additionalProperties: false` |
| `validate_data_against_snapshot(name, data, update=False)` | 校验宽泛接口真实 `data` 是否与 `schema_snapshots/response_data/` 快照一致 |
| `validate_api_response_against_snapshot(name, response, update=False)` | 校验完整 API response 是否与 `schema_snapshots/api_response/` 快照一致 |

**Schema 快照工作流**:
1. 首次运行 → 自动为所有注册的 Pydantic 模型生成快照
2. 模型变更 → 测试失败，显示字段增删/类型变更
3. 有意变更 → `--snapshot-update` 标志刷新快照

**实际 `data` 契约快照工作流**:
1. 对 `response_model=ApiResponse`、`ApiResponse[dict]`、`dict[str, Any]` 等宽泛接口，在接口测试中调用 `assert_response_data_contract(body, "snapshot_name", update=contract_snapshot_update)`
2. `--snapshot-update` 时从当前实际 `body["data"]` 推导严格 JSON Schema，写入 `schema_snapshots/response_data/`
3. 普通测试运行时用 `jsonschema` 校验实际 `data`；字段新增、删除、类型变化、嵌套对象额外字段都会失败
4. 空数组无法推导 item 结构，应优先使用非空 mock 数据作为契约样本

**Service-backed API 契约工作流**:
1. 不替换被测 service（例如 `BotService`），通过 `world.get(Repo)` 向 per-test SQLite 注入代表性数据
2. 仍通过 `gw_client` 调用真实 FastAPI handler，让真实 service 参与响应拼装
3. 使用 `assert_api_response_contract()` 校验完整 response envelope；service 返回字段增删、类型变化、顶层 envelope 漂移都会失败
4. 只在必要时 mock 外部系统/插件，避免把 service 本身替换成 `MagicMock`
5. 当前覆盖 BotService、DeviceService、SkillSetService/SkillService、BotPublicService 的前端强依赖接口，共 40 个完整 API response 快照

## 测试文件详细说明

### Rule #1 — Bot 管理 (`test_rule01_bot_management.py`)

**接口**: GET `/api/bots/by-owner`, GET/PUT/DELETE `/api/bots/{id}`, POST `/api/bots`, GET `/api/bots/check/name`, POST `/api/bots/{id}/restart`, GET `/api/bots/{id}/passport`

**Mock 策略**:
- `_bind_bot(app)`: `bind_mock_service(BotService, mock_svc, app)` — 基本读写操作
- `_bind_bot_repo(app)`: `bind_mock_service(BotRepository, mock_repo, app)` — `generate_bot_id` 等仓库调用
- `_bind_create_bot_deps(app)`: 完整 5 依赖绑定 — `BotService` + `BotRepository` + `PassportPlugin` + `AuthRelationshipPlugin` + `SkillSetServiceFactory`
- `_bind_passport(app)`: `BotService` + `PassportPlugin` — Passport 查询

关键: Handler 调用 `bot_service.get_engine_paths()` 为每个 bot 计算路径，必须 mock 此方法返回非空 dict，否则 `list({}.values())[0]` 会 IndexError。

```python
svc.get_engine_paths.return_value = {"openclaw": "/tmp/test/openclaw"}
```

**`create_bot` 依赖**: Handler 注入 5 个 DI 服务（`BotService`, `BotRepository`, `PassportPlugin`, `AuthRelationshipPlugin`, `SkillSetServiceFactory`），必须全部 mock 否则会因 DI 解析失败返回 500。

### Rule #2 — 公开 Bot 互动 (`test_rule02_bot_public.py`)

**接口**: GET `/api/v1/bot-public/my-friend-bots`, GET `friend-record`, GET `search`, POST `friend-request-approval`

**Mock 策略**: `bind_mock_service(BotPublicService, mock_svc, app)`

使用 `get_current_user` 认证，Mock 返回值需匹配 Handler 期望的原始数据结构。

### Rule #3 — 专家对话 (`test_rule03_expert_chats.py`)

**接口**: GET/POST/DELETE `/api/v1/expert-chats/*`

**Mock 策略**: `bind_mock_service(ExpertChatService, mock_svc, app)`

### Rule #5 — 设备连接 (`test_rule05_devices.py`)

**接口**: GET `/api/v1/devices/{id}/connection`, GET `/api/v1/devices/connectable`

**Mock 策略**:
- `bind_mock_service(DeviceService, mock_svc, app)`
- `get_device_connection` → 返回 `DeviceConnectionInfo` 数据类（handler 经 `connection_info_to_response` 转为 Response）
- `list_connectable_devices` → 返回 `(total, [record])` 元组，record 为 MagicMock 模拟 `DeviceBindingRecord` 属性

### Rule #6 — Token 交换 (`test_rule06_token.py`)

**接口**: POST `/api/v1/token/exchange`

**Mock 策略**: Token handler mock。

### Rules #7,7b,12,12b,14 — BCS 群组/会话/Bot (`test_rule07_bcs_groups.py`)

**接口**: 18 个 BCS 相关端点

**Mock 策略**: `@responses.activate` + `responses.add(responses.GET/POST, BCS_URL, json=..., status=200)`

这些路由在 Gateway 中直接代理到 BCS 服务，后端不实现 Handler，因此 Mock HTTP 响应。

涵盖的 BCS 端点组:
- 群组 CRUD (create/get/delete group)
- 群组消息 (get messages)
- 群组会话 (get sessions, participant mode, session member mode)
- Bot 详情/发现/查询/可见性
- Bot 好友关系 (friends list, friend request CRUD)
- Bot onboard

### Rule #8 — Engine 代理 (`test_rule08_engine_proxy.py`)

**接口**: 8 个 AgentClawProxy 端点

**Mock 策略**: `@responses.activate` 拦截 `AGENTCLAWPROXY_URL`

涵盖: create/list sessions, list models, engine status/restart/switch, update session, delete messages。

### Rule #10 — 技能市场 (`test_rule10_skills.py`)

**接口**: GET `/api/skills/market/list`, POST `market/search`, GET `skillset/active`

**Mock 策略**（两套依赖绑定）:

1. **`_bind_skill_deps(app)`** — 用于 market/list, market/search:
   - `SkillServiceFactory` → `mock_factory.create()` 返回 mock service
   - `BotRepository` → `_get_path_params()` 路径解析
   - `WorkspacePathFactory` → `_get_path_params()` 文件系统路径（`get_bot_skills_dir`, `get_bot_data_dir`, `get_bot_skills_local_dir`, `get_bot_skills_repo_dir`）

2. **`_bind_skillset_active_deps(app)`** — 用于 skillset/active:
   - `SkillSetRepository` → 直接注入（不是 SkillServiceFactory）
   - `BotRepository` → `_get_path_params()` 路径解析

**关键**:
- Handler 将 `service.list_market_skills()` / `service.search_market_skills()` 返回值直接传给 `SearchResponse(data=results)`，Pydantic 期望 `data` 是列表。Mock 必须返回 `[{...}]` 而非 `{"success": True, "data": [...]}`
- `skillset/active` 端点注入的是 `SkillSetRepository`（不是 `SkillServiceFactory`），Mock 目标需区分

**保留的防御性断言**: market/list 和 skillset/active 的 `if data:` 判断 — 因 `_get_path_params` 在 Mock 场景下可能返回空列表，空列表不应触发字段断言

### Rule #13 — 定时任务 (`test_rule13_cron.py`)

**接口**: GET/POST `/api/cron`, GET `status`, DELETE/GET `/api/cron/{taskId}`, POST `run`, GET `runs`

**Mock 策略**: `bind_mock_service(CronRelayService, mock_svc, app)`，异步方法使用 `AsyncMock`。

### Rule #15 — 能力集管理 (`test_rule15_skillsets.py`)

**接口**: GET/POST `/api/skillsets`, DELETE `/api/skillsets/{id}`, GET `with-mcps`

**Mock 策略**:
- `bind_mock_service(SkillSetServiceFactory, mock_factory, app)` — factory mock
- `bind_mock_service(BotRepository, mock_bot_repo, app)` — `_get_path_params()` 调用

**关键**:
- `SkillSetServiceFactory.create()` 返回 service mock
- Service 方法返回**原始数据**（列表或 bool），非 `{"success": True, "data": [...]}` 包装
- `dict` 中字段名是 `bolt_id`（Handler 内部映射为 `bot_id`）
- `id` 字段类型为 `str`（Pydantic `SkillSetResponse.id` 要求 string）
- 异步方法 `add_skills_to_set`、`remove_skill_from_set` 等需用 `AsyncMock`
- 所有断言使用 `assert_success` + 直接 `body["data"]`，不再使用 `if body.get("success")` 或 `if isinstance(data, list)` 防御性守卫

### Rule #16 — MCP 市场 (`test_rule16_mcp.py`)

**接口**: GET `market/list`, GET `market/permission`, GET `tenants`

**Mock 策略**: `bind_mock_service(MCPMarketService, ...)` / `bind_mock_service(MCPAuthService, ...)`

**关键**: `MCPPermissionResponse` 的 `has_permission`、`access_level` 在响应**顶层**（与 `success` 平级），而非嵌套在 `data` 下。

### Rule #17 — MCP 办公网鉴权 (`test_rule17_mcp_meta_auth.py`)

**接口**: POST `market/permission/apply`（后端）+ HTTP Mock（MCP Center）

**混合策略**: 后端 Handler 用 DI Mock，MCP Center 外部调用用 `@responses.activate`。

## 修复过的关键陷阱

### 1. 认证依赖未覆盖 → 401

**现象**: `{'detail': 'Authentication required'}`，HTTP 401

**根因**: 大量路由使用 `Depends(get_request_context)` 而非 `get_current_user`。后者调用 `AuthPlugin.resolve_user_from_request()` 失败后抛 `HTTPException(401)`。

**修复**: `conftest.py` 中 `gw_client` fixture 增加 `get_request_context` override。

### 2. SingletonScope 缓存 → Mock 不生效

**现象**: `bind_mock_service()` 后请求仍使用真实服务实例

**根因**: `injector` 库的 `SingletonScope._context` 字典缓存了已解析的单例。`binder.bind()` 虽然覆盖了 `Binder._bindings`，但 `Injector.get(X)` 先从 `_context` 查找，命中旧实例直接返回。

**修复**: `bind_mock_service()` 末尾清除缓存:
```python
scope_binding, _ = injector.binder.get_binding(SingletonScope)
scope_instance = scope_binding.provider.get(injector)
scope_instance._context.pop(service_cls, None)
```

### 3. Mock 返回值格式不匹配 → Pydantic ValidationError

**现象**: `ValidationError: Input should be a valid list [type=list_type, ...]`

**根因**: Handler 将 service 返回值直接传入 Pydantic Response 模型（如 `SearchResponse(data=results)`），`data` 字段期望列表。Mock 返回了 `{"success": True, "data": [...]}` 包装格式。

**修复**: Mock 应返回 Handler 期望的原始数据结构:
- ✅ `svc.list_market_skills.return_value = [MOCK_SKILL_ITEM]`
- ❌ `svc.list_market_skills.return_value = {"success": True, "data": [MOCK_SKILL_ITEM]}`

### 4. 字段名映射: `bolt_id` vs `bot_id`

**现象**: `KeyError: 'bot_id'`

**根因**: SkillSet 服务层使用 `bolt_id`，Handler 内部映射为 `bot_id`：`skill_set_dict["bot_id"] = skill_set_dict.get('bolt_id') or "default"`。Mock 数据应使用 `bolt_id`。

### 5. 字段类型: `id` str vs int

**现象**: `ValidationError: Input should be a valid string [type=string_type, input_value=1]`

**根因**: `SkillSetResponse.id` 类型为 `str`，Mock 数据 `"id": 1` 应为 `"id": "1"`。

### 6. Handler 内部调用链: `get_engine_paths()`

**现象**: `IndexError: list index out of range`

**根因**: `list_bots_by_owner` Handler 对每个 bot 调用 `bot_service.get_engine_paths()`，然后用 `list(engine_paths.values())[0]` 取第一个路径。Mock 默认返回 `MagicMock()`，转列表为空，取 `[0]` 越界。

**修复**: `mock_svc.get_engine_paths.return_value = {"openclaw": "/tmp/test/openclaw"}`

### 7. 响应字段层级: 顶层 vs 嵌套

**现象**: `assert_has_fields` 找不到 `has_permission` 字段

**根因**: `MCPPermissionResponse(success=True, has_permission=..., access_level=...)` 将字段放在顶层，而非嵌套在 `data` 下。这是 Pydantic Response Model 的定义方式决定。

### 8. 防御性断言导致测试静默通过（已修复）

**现象**: 使用 `if body.get("success")` 或 `if isinstance(data, dict)` 守卫的测试，当 Mock 不完整导致 DI 解析失败时，整个断言块被跳过，测试显示通过（假绿）。

**根因**: 测试编写者不确定 Mock 是否完整，用条件守卫"防御性"跳过了断言。当后续开发者修改 Mock 或 Handler 后引入 bug 时，这些测试无法检测到回归。

**修复**:
- Rule #1 (`create_bot`): 补全 5 个 DI 依赖（`BotService`, `BotRepository`, `PassportPlugin`, `AuthRelationshipPlugin`, `SkillSetServiceFactory`），移除 `if body.get("success")` 守卫
- Rule #5 (`devices`): 新增 `DeviceService` 完整 Mock，移除所有 `if body.get("success")` 守卫
- Rule #10 (`skills/skillset/active`): 修正 Mock 目标（`SkillSetRepository` 替代错误的 `SkillServiceFactory`），移除不必要的守卫
- Rule #13 (`cron`): 移除 `if isinstance(data, dict)` 和 `if runs:` 守卫
- Rule #15 (`skillsets`): 移除 `if isinstance(data, list) and data:` 和 `if body.get("success")` 守卫
- Rule #16 (`mcp`): 移除 `if body.get("success")` 和 `if isinstance(data, list) and data:` 守卫

**保留的合法守卫**（4 处）:
- Rule #10 `market/list` 和 `skillset/active`: `if data:` — Mock 场景下 `_get_path_params` 可能返回空列表，空列表不应触发 `data[0]` 字段断言
- Rule #03 `expert_chats`: `if isinstance(data, list) / elif isinstance(data, dict)` — Handler 存在两种合法响应格式

### 9. DI 依赖缺失 → 500 而非测试失败

**现象**: 使用 `if body.get("success")` 或 `if isinstance(data, dict)` 守卫的测试，当 Mock 不完整导致 DI 解析失败时，整个断言块被跳过，测试显示通过（假绿）。

**根因**: 测试编写者不确定 Mock 是否完整，用条件守卫"防御性"跳过了断言。当后续开发者修改 Mock 或 Handler 后引入 bug 时，这些测试无法检测到回归。

**修复**:
- Rule #1 (`create_bot`): 补全 5 个 DI 依赖（`BotService`, `BotRepository`, `PassportPlugin`, `AuthRelationshipPlugin`, `SkillSetServiceFactory`），移除 `if body.get("success")` 守卫
- Rule #5 (`devices`): 新增 `DeviceService` 完整 Mock，移除所有 `if body.get("success")` 守卫
- Rule #10 (`skills/skillset/active`): 修正 Mock 目标（`SkillSetRepository` 替代错误的 `SkillServiceFactory`），移除不必要的守卫
- Rule #13 (`cron`): 移除 `if isinstance(data, dict)` 和 `if runs:` 守卫
- Rule #15 (`skillsets`): 移除 `if isinstance(data, list) and data:` 和 `if body.get("success")` 守卫
- Rule #16 (`mcp`): 移除 `if body.get("success")` 和 `if isinstance(data, list) and data:` 守卫

**保留的合法守卫**（4 处）:
- Rule #10 `market/list` 和 `skillset/active`: `if data:` — Mock 场景下 `_get_path_params` 可能返回空列表，空列表不应触发 `data[0]` 字段断言
- Rule #03 `expert_chats`: `if isinstance(data, list) / elif isinstance(data, dict)` — Handler 存在两种合法响应格式

### 9. DI 依赖缺失 → 500 而非测试失败

**现象**: Mock 不完整时请求返回 HTTP 500，但测试中的 `if body.get("success")` 守卫跳过了断言，测试通过。

**根因**: FastAPI injector 在解析到缺失的依赖时抛出 `CallError` 或 `UnsatisfiedRequirement`，Handler 返回 500 错误。如果测试用 `if body.get("success") is True` 包裹断言，则整个块被跳过。

**修复**: 不使用条件守卫，而是用 `assert_success(body, ...)` 在错误时立即失败。补全所有缺失的 DI Mock 依赖。

## WebSocket 协议契约测试

### 策略 C: 协议帧校验

WebSocket 接口 (WS-1, WS-1a, WS-2, WS-MUX) 为 Gateway 层代理路由，后端不实现 WS 服务端。
测试策略为验证后端 WS 客户端代码发送/解析的协议帧是否符合文档规范。

**核心方法**:
1. **帧数据类序列化/反序列化** — 验证 `RequestFrame`, `ResponseFrame`, `EventFrame` 等 dataclass 的 `to_dict()`/`from_dict()` 与文档规范一致
2. **MockWebSocket** — 模拟 WS 连接，捕获客户端发送的帧，自动回复 hello-ok 握手响应
3. **`AsyncMock` + `patch`** — Mock `websockets.connect` 以隔离网络 I/O

### 测试文件一览

| 文件 | 协议 | 测试数 | 关键验证点 |
|------|------|--------|-----------|
| `test_ws01_engine_chat.py` | WS-1 Moltis | 38 | RequestFrame/ResponseFrame/EventFrame 结构, ConnectParams 握手, HelloOk 响应解析, chat.send/chat.abort/approval.resolve 帧格式, ping/pong 心跳, MoltisGatewayClient.connect() 集成流 |
| `test_ws01a_grt_chat.py` | WS-1a GRT | 15 | GRT connect 帧 (x-moltis-mcp-token, scopes), chat.send 参数差异 (sessionKey/message/deliver/idempotencyKey vs _session_key/text), ChatResult/ChatStreamEvent 数据模型, WS-1 vs WS-1a 认证差异对照 |
| `test_ws02_bcs_group_chat.py` | WS-2 BCS | 11 | bot.connect 握手帧, group chat.send (bot_uuid, group_id, mentions, sender_id, thinking, timeoutMs), group 事件帧 (chat/agent/bot.status), BCS 方法清单 (bot.connect, bot.status, chat.send, chat.inject) |

### Moltis 协议帧结构

```
请求帧: {"type":"req", "id":"<uuid>", "method":"<method>", "params":{...}}
响应帧: {"type":"res", "id":"<uuid>", "ok":true|false, "payload":{...}, "error":{code, message}}
事件帧: {"type":"event", "event":"<name>", "payload":{...}, "seq":<int>}
心跳:   {"type":"ping"} / {"type":"pong"}
```

### WS-1 vs WS-1a 认证差异

| 维度 | WS-1 (MoltisGatewayClient) | WS-1a (GrtChatService) |
|------|---------------------------|----------------------|
| connect auth | `params.auth.token` | `params["x-moltis-mcp-token"]` |
| scopes | 不包含 | `["operator.admin", "operator.read", "operator.write"]` |
| chat.send params | `_session_key`, `text` | `sessionKey`, `message`, `deliver`, `idempotencyKey` |
| client.id | `openclaw-enterprise` | `cli` |

### WS-2 BCS 群聊帧扩展

群聊帧在 Moltis 基础帧上增加字段:
- `bot.connect`: params 含 `bot_uuid`, `group_id`
- `chat.send`: params 含 `bot_uuid`, `group_id`, `mentions`, `sender_id`, `thinking`, `timeoutMs`
- event frame: payload 含 `bot_uuid`, `group_id`

## 尚未覆盖的接口

| 类别 | 接口 | 原因 |
|------|------|------|
| Rule #1 剩余 | `get_engine_config`, `update_engine_config`, `auth-status` | 时间优先级 |
| Rule #2 剩余 | `search/authorized`, `search/unauthorized` | 次要端点 |
| Rule #3 SSE 流 | `grt-chat/stream` | SSE 长流测试需特殊处理 |
| Rule #4 | WS 相关辅助接口 | 需 WS 测试框架 |
| WS 辅助 | `/ws/info`, `/ws/health` | Gateway 级 HTTP 端点，后端未实现 |
| Rule #9 | 未在 Gateway 文档中定义 | — |

## 运行方式

```bash
cd src/backend

# 全部契约测试
uv run pytest tests/contracts/gateway/ -v

# 单个 Rule
uv run pytest tests/contracts/gateway/test_rule01_bot_management.py -v

# 仅 DI Mock 测试
uv run pytest tests/contracts/gateway/ -v -k "not bcs and not engine_proxy"

# 仅 HTTP Mock 测试
uv run pytest tests/contracts/gateway/ -v -k "bcs or engine_proxy"

# Schema 快照一致性测试
uv run pytest tests/contracts/gateway/test_schema_conformance.py -v

# 更新 Schema 快照（当模型有意变更时）
uv run pytest tests/contracts/gateway/test_schema_conformance.py -v --snapshot-update
```

## 新增测试 Checklist

为新的 Gateway 路由添加契约测试时:

1. **确认路由所在服务**: 后端路由 → 策略 A（DI Mock），外部代理路由 → 策略 B（HTTP Mock）
2. **确认认证方式**: 检查 Handler 使用 `get_current_user` 还是 `get_request_context`
3. **确认 Handler 对 Service 返回值的处理方式**:
   - 直接传入 Pydantic Model → Mock 返回原始类型（列表/dict/bool）
   - Handler 自行封装 ApiResponse → Mock 可返回包装格式
4. **确认 Pydantic Response Model 的字段类型**: `id` 是 `str` 还是 `int`
5. **确认字段名映射**: 服务层命名可能与 Handler 输出不同（如 `bolt_id` → `bot_id`）
6. **异步方法**: 使用 `AsyncMock` 而非 `MagicMock`
7. **Factory 模式**: `SkillSetServiceFactory`/`SkillServiceFactory` 需 mock `factory.create()` 返回 service mock
8. **内部调用链**: Handler 可能调用 service 的多个方法（如 `get_engine_paths`），需全部 mock
9. **完整 DI 依赖**: Handler 注入的**所有** DI 服务都必须 mock，否则 injector 解析失败返回 500。不要依赖条件守卫（`if body.get("success")`）来跳过断言 — 用 `assert_success()` 在错误时立即失败
10. **优先使用类型断言**: `assert_has_fields(data, {"id": int, "name": str})` — 已弃用 `list[str]` 模式，必须使用 `dict[str, type|tuple]` 模式
11. **严格模式检测新增字段**: 用 `strict=True` 或 `assert_no_extra_fields()` 检测 API 响应中未声明的新字段
12. **宽泛接口必须加实际 data 快照**: `ApiResponse`/`dict`/`Any` 响应无法靠 Pydantic schema 捕获 `data` 漂移，应调用 `assert_response_data_contract()`
13. **Schema 快照测试**: 新增 Pydantic 响应模型时，在 `test_schema_conformance.py` 的 `_collect_models()` 中注册，首次运行会自动生成快照
14. **Mock 数据契约验证**: 新增 BCS/Engine 接口时，在 `schema_snapshots/bcs/` 下创建 JSON Schema 契约文件，并在测试中用 `validate_mock_against_schema()` 验证 mock 数据符合契约
