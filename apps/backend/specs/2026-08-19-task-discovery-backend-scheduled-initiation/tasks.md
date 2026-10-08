# task_discovery 后端定时发起 + Session 消息注入 — TDD 任务清单

## 任务总览

| Slice | 目标 | 关键文件 | 依赖 |
|---|---|---|---|
| 1 | 依赖添加 | `pyproject.toml` | 无 |
| 2 | 模型字段扩展 | `models.py` | 无 |
| 3 | DB Schema 改造 | `task_reader.py` | Slice 2 |
| 4 | Session Initiator（HTTP+WS） | `session_initiator.py` | Slice 2 |
| 5 | DiscoveryService 改造 | `discovery_service.py` | Slice 3, 4 |
| 6 | 协议层改造 | `protocols.py` | 无 |
| 7 | Scheduler | `scheduler.py` | Slice 5 |
| 8 | HTTP 端点改造 | `router.py` | Slice 5, 7 |
| 9 | DI 模块改造 | `task_discovery_module.py` | Slice 4, 5, 7 |
| 10 | 清理 + __init__ 更新 | `lifecycle.py`（删）, `session_creator.py`（删）, `__init__.py` | Slice 5, 9 |
| 11 | E2E 测试 | `test_task_discovery_e2e.py` | Slice 1-10 |

---

## Slice 1: 依赖添加

- **Goal**: `pyproject.toml` 新增 `apscheduler`、`websockets` 依赖
- **Files**:
  - `src/backend/pyproject.toml`
- **Tests first**: 无（依赖变更）
- **Implementation**:
  - 在 `dependencies` 数组中添加 `"apscheduler>=3.10"` 和 `"websockets>=12.0"`
  - 运行 `uv lock` 更新锁文件
- **Validation**: `cd src/backend && uv sync && python -c "import apscheduler; import websockets; print('ok')"`
- **Dependencies**: 无
- **Human confirmation needed**: no

---

## Slice 2: 模型字段扩展 — DiscoveredTask

- **Goal**: `DiscoveredTask` 新增 `bot_id`/`owner_id`/`dt` 字段；新增 `to_discovery_prompt()`/`to_notification_body()`/`to_card_data()` 方法
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/models.py`
- **Tests first**:
  - 测试 `DiscoveredTask` 可用 `bot_id`/`owner_id`/`dt` 构造
  - 测试 `to_session_ext_info()` 返回 dict 包含 `bot_id`/`owner_id`/`dt`/`source: "task_discovery"`
  - 测试 `to_discovery_prompt()` 返回文本包含项目名称、简介、业务场景、发现依据、「是否确认执行」
  - 测试 `to_notification_body(3)` 返回文本包含「发现 3 件」「请点击进入会话」
  - 测试 `to_card_data()` 返回 dict 包含 `card_name`/`workitem_name`/`workitem_bg`，不含任何服务商品牌名
- **Implementation**:
  - `DiscoveredTask` 新增字段 `bot_id: str`、`owner_id: str`、`dt: str`
  - `to_session_ext_info()` 追加 `bot_id`/`owner_id`/`dt` 字段
  - 新增 `to_discovery_prompt() -> str`：生成发现提示消息文本
  - 新增 `to_notification_body(task_count: int) -> str`：生成通知消息体
  - 新增 `to_card_data() -> dict`：生成交互卡片数据（通用抽象）
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "discovered_task_model" -v`
- **Dependencies**: 无
- **Human confirmation needed**: no

---

## Slice 3: DB Schema 改造 — task_reader.py

- **Goal**: `discovered_tasks` 表增加 `bot_id`/`owner_id`/`dt` 列+索引；新增 `read_pending_tasks_for_bot()` 方法
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/task_reader.py`
- **Tests first**:
  - 测试 `init_discovered_tasks_db()` 写入含 `bot_id`/`owner_id`/`dt` 的数据成功
  - 测试 `read_pending_tasks_for_bot("bot-001", "user-001", "2026-08-19")` 返回匹配且 `pending_confirmation` 的任务
  - 测试 `read_pending_tasks_for_bot("wrong-bot", ...)` 返回空列表
  - 测试 `read_pending_tasks()` / `read_discovered_tasks()` 仍可调用（向后兼容）
  - 测试 `read_pending_tasks_for_bot()` 返回的 `DiscoveredTask` 含 `bot_id`/`owner_id`/`dt` 字段
- **Implementation**:
  - 更新 `_CREATE_TABLE_SQL`：增加 `bot_id TEXT NOT NULL`、`owner_id TEXT NOT NULL`、`dt TEXT NOT NULL`
  - 新增 `CREATE INDEX idx_discovered_tasks_bot_owner_dt ON discovered_tasks(bot_id, owner_id, dt)`
  - 更新 `_SELECT_ALL_SQL` 和 `_row_to_task()` 包含新字段
  - 更新 `init_discovered_tasks_db()` 的 INSERT 语句
  - `SqliteTaskReader` 新增 `read_pending_tasks_for_bot(bot_id, owner_id, dt) -> list[DiscoveredTask]`
  - `MockTaskReader` 同步更新
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "task_reader" -v`
- **Dependencies**: Slice 2
- **Human confirmation needed**: no

---

## Slice 4: Session Initiator（HTTP 创建 + WS 消息注入）

- **Goal**: 新增 `session_initiator.py`，实现 `CronRelaySessionInitiator` — relay 创建 session + WebSocket chat.send 注入发现提示
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/session_initiator.py`（新增）
- **Tests first**:
  - 测试 `initiate_session()` 调用 `cron_relay.forward_request(POST /api/sessions)` — Mock relay 返回 session_id
  - 测试 `initiate_session()` 返回 `DiscoverySession` 含正确 session_id 和 session_url
  - 测试 `_ws_send_message()` 调用 `websockets.connect` + 发送 connect 帧和 chat.send 帧 — Mock `websockets.connect`
  - 测试 WS 握手失败时仅 log warning 不抛异常
  - 测试 WS chat.send 被拒绝时仅 log warning 不抛异常
  - 测试 `_build_discovery_prompt()` 返回包含所有任务项目名称的文本
  - 测试 relay 创建 session 失败时抛 RuntimeError
  - 测试 `wait_for_reply=True` 时读取事件直到 `state=final`
  - 测试 engine target 解析失败时 WS 注入跳过，session 仍返回成功
- **Implementation**:
  - 定义 `SessionInitiator` Protocol
  - `CronRelaySessionInitiator.__init__(cron_relay, frontend_url, wait_for_reply=False)`
  - `initiate_session(tasks, bot_id, owner_id, agent_id, model)`:
    - Step 1: `cron_relay.forward_request(POST /api/sessions, body={title, user_id, agent_id, extInfo})`
    - Step 2: `_extract_engine_target()` 复用 backend connection API 查 target
    - Step 3: `_ws_send_message(target, session_id, discovery_prompt)`
    - 返回 `DiscoverySession(task_id, session_id, session_url)`
  - `_ws_send_message()`: WebSocket connect → connect 握手帧 → chat.send 帧 → 可选等 final → 关闭
  - `_build_discovery_prompt(tasks)`: 生成发现提示消息
  - `_build_session_url(session_id, agent_id)`: 构造前端 URL
  - WS 失败仅 log warning（降级策略）
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "session_initiator" -v`
- **Dependencies**: Slice 2
- **Human confirmation needed**: no

---

## Slice 5: DiscoveryService 改造

- **Goal**: 移除 `SessionCreator` 依赖，改用 `SessionInitiator`；新增 `discover_all_bots()`；通知消息改造
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/discovery_service.py`
- **Tests first**:
  - 测试 `__init__()` 接受 `reader`/`session_initiator`/`notify_sender`
  - 测试 `discover(bot_id, owner_id, agent_id)` 调用 `reader.read_pending_tasks_for_bot()`
  - 测试 `discover()` 无任务时返回空列表
  - 测试 `discover()` 有任务时调用 `session_initiator.initiate_session()`
  - 测试 `_discover_single()` session 创建成功后调 `notify_sender.send()`
  - 测试 `NotifyMessage.body` 由 `task.to_notification_body(task_count)` 生成
  - 测试 `NotifyMessage.deep_link` 为 `session_url`
  - 测试 `NotifyMessage.extra` 包含 `channel`/`card_template_id`/`card_biz_id`/`card_data`/`session_url`
  - 测试 `card_template_id` 从环境变量 `TASK_DISCOVERY_CARD_TEMPLATE_ID` 读取
  - 测试代码中不出现任何具体服务商品牌名
  - 测试 `_discover_single()` session 创建失败时 `error` 记录异常，`notification_sent=False`
  - 测试 `discover_all_bots()` 调用 `bot_service.list_bots()` 遍历所有 bot
- **Implementation**:
  - 移除 `from ...session_creator import SessionCreator, HttpSessionCreator`
  - 改为 `from ...session_initiator import SessionInitiator`
  - `__init__()` 参数 `session_creator` → `session_initiator`
  - `discover()` 调用 `reader.read_pending_tasks_for_bot(bot_id, owner_id, dt)`
  - `_discover_single()` 调用 `session_initiator.initiate_session(all_tasks, ...)`
  - `_send_notification()` 构造 `NotifyMessage`：body=to_notification_body, deep_link=session_url, extra=卡片参数
  - 新增 `discover_all_bots()` — 遍历 BotService.list_bots() 逐 bot discover
  - 移除 `create_default_service()` 工厂方法（改为 DI provider 构建）
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "discovery_service" -v`
- **Dependencies**: Slice 3, 4
- **Human confirmation needed**: no

---

## Slice 6: 协议层改造 — protocols.py

- **Goal**: `BotServiceProtocol` 新增 `get_bot()`；新增 `CronRelayServiceProtocol`
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/protocols.py`
- **Tests first**: 无（接口变更，在 Slice 5/9 的 mock 中覆盖）
- **Implementation**:
  - `BotServiceProtocol` 新增 `def get_bot(self, *args, **kwargs) -> Any: ...`
  - 新增 `@runtime_checkable class CronRelayServiceProtocol(Protocol)` 含 `forward_request` / `list_all_crons`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "protocols" -v`
- **Dependencies**: 无
- **Human confirmation needed**: no

---

## Slice 7: Scheduler — scheduler.py

- **Goal**: 新增 `TaskDiscoveryScheduler`，APScheduler BackgroundScheduler，线程级 cron 调度
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/scheduler.py`（新增）
- **Tests first**:
  - 测试 `startup()` 当 `TASK_DISCOVERY_AUTO_START=true` 时启动 BackgroundScheduler
  - 测试 `startup()` 当 `TASK_DISCOVERY_AUTO_START=false` 时不启动
  - 测试 `shutdown()` 正确调用 `scheduler.shutdown(wait=False)`
  - 测试 `_run_discovery()` 调用 `asyncio.run(service.discover_all_bots())`
  - 测试 cron 表达式从 `TASK_DISCOVERY_CRON` 环境变量读取
  - 测试时区从 `TASK_DISCOVERY_TIMEZONE` 环境变量读取
  - 测试 `enable_for_bot()` / `disable_for_bot()` 不抛异常
- **Implementation**:
  - `class TaskDiscoveryScheduler(LifecycleBase)`，`@inject __init__(discovery_service)`
  - `startup()`: 读环境变量 → `BackgroundScheduler()` → `add_job(_run_discovery, CronTrigger)` → `start()`
  - `shutdown()`: `scheduler.shutdown(wait=False)`
  - `_run_discovery()`: `asyncio.run(self._service.discover_all_bots())`
  - `enable_for_bot(bot_id, owner_id)`: 确保 scheduler 运行
  - `disable_for_bot(bot_id, owner_id)`: 停止调度（可选移除特定 job）
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "scheduler" -v`
- **Dependencies**: Slice 5
- **Human confirmation needed**: no

---

## Slice 8: HTTP 端点改造 — router.py

- **Goal**: 新增 `/scheduled-trigger`、`/dream-mode` 端点；`/discover` 改为直接调用
- **Files**:
  - `src/backend/src/agentclaw/community/adapters/http/task_discovery/router.py`
- **Tests first**:
  - 测试 `POST /scheduled-trigger` 调用 `service.discover_all_bots()` 返回 `total_discovered`
  - 测试 `POST /discover?bot_id=X&owner_id=Y&agent_id=Z` 调用 `service.discover()` 返回 `discovered` count
  - 测试 `POST /dream-mode?enabled=true&bot_id=X&owner_id=Y` 返回 `enabled: true`
  - 测试 `POST /dream-mode` 对非 owner 的 bot 返回失败
  - 测试 `GET /status` 支持按 bot_id/owner_id 过滤
  - 测试响应中每个 task 包含 `session_url` 和 `notification_sent`
- **Implementation**:
  - 移除 `_build_service()` 和 `HttpSessionCreator` import
  - `POST /scheduled-trigger`: `Injected(DiscoveryService)` → `service.discover_all_bots()`
  - `POST /discover`: `Injected(DiscoveryService)` → `service.discover(bot_id, owner_id, agent_id, model)`
  - `POST /dream-mode`: `Injected(TaskDiscoveryScheduler)` + `Injected(BotServiceProtocol)` → ownership 校验 → enable/disable
  - `GET /status`: `SqliteTaskReader` 支持按 bot_id/owner_id/dt 过滤
- **Validation**: `cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v`
- **Dependencies**: Slice 5, 7
- **Human confirmation needed**: no

---

## Slice 9: DI 模块改造 — task_discovery_module.py

- **Goal**: 移除 Lifecycle 绑定，新增 Scheduler/Service/Initiator provider
- **Files**:
  - `src/backend/src/agentclaw/community/di/modules/task_discovery_module.py`
- **Tests first**: 无（DI 接线在 startup 和 e2e 中验证）
- **Implementation**:
  - 移除 `TaskDiscoveryLifecycle` import 和 bind
  - `configure()`: bind `TaskDiscoveryScheduler` singleton + bind `DiscoveryService` singleton
  - `_provide_discovery_service(reader, session_initiator, notify_sender) -> DiscoveryService`: singleton provider
  - `_provide_session_initiator(cron_relay) -> SessionInitiator`: 返回 `CronRelaySessionInitiator(cron_relay)`
  - `_provide_task_reader() -> TaskReader`: 返回 `SqliteTaskReader(_resolve_db_path())`
  - 保留 `_bridge_bot_service_protocol`
  - 新增 `_bridge_cron_relay_protocol`
- **Validation**: `cd src/backend && python -c "from agentclaw.community.di import get_injector; inj = get_injector(); inj.get(DiscoveryService); print('di ok')"`
- **Dependencies**: Slice 4, 5, 7
- **Human confirmation needed**: no

---

## Slice 10: 清理 + __init__ 更新

- **Goal**: 删除旧文件，更新模块文档
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/lifecycle.py`（删除）
  - `src/backend/src/agentclaw/community/core/task/task_discovery/session_creator.py`（删除）
  - `src/backend/src/agentclaw/community/core/task/task_discovery/__init__.py`（修改）
  - `src/backend/tests/.../test_task_discovery_unit.py`（修改 — 移除旧接口引用）
- **Tests first**:
  - 测试 import `session_initiator` 成功
  - 测试 `from ...task_discovery import __doc__` 包含「创建 session + WebSocket 注入 + 通知」描述
- **Implementation**:
  - 删除 `lifecycle.py`
  - 删除 `session_creator.py`
  - 更新 `__init__.py` docstring
  - 更新测试文件中所有 `SessionCreator`/`HttpSessionCreator`/`create_default_service` 引用
  - 搜索全仓库确认无残留引用：`grep -r "session_creator\|TaskDiscoveryLifecycle\|create_default_service" src/backend/src/`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "task_discovery" -v`
- **Dependencies**: Slice 5, 9
- **Human confirmation needed**: no

---

## Slice 11: E2E 测试

- **Goal**: 更新 e2e 测试覆盖完整新流程
- **Files**:
  - `src/backend/tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **Tests first**:
  - 测试 gated（`SINGLEBOX_TASK_E2E=1`）
  - 测试 mock 数据使用新格式（含 bot_id/owner_id/dt）
  - 测试 `POST /discover` 返回 session_id + session_url + notification_sent
  - 测试 session 创建经 relay 通道（确认 `forward_request` 调用路径）
  - 测试 WebSocket chat.send 发送发现提示消息（`wait_for_reply=True` 等待 bot 回复）
  - 测试 `GET /status` 支持新格式数据
  - 测试通知 `extra` 包含 `card_template_id`/`card_biz_id`/`card_data`
- **Implementation**:
  - 更新 `_MOCK_TASKS` 为新格式（含 bot_id/owner_id/dt）
  - 更新 `init_discovered_tasks_db()` 调用参数
  - discover → 验证 session_id 非空
  - WebSocket 连 engine → `chat.send` → 等待 `state=final` → 断言回复非空
  - 验证通知日志和 extra 字段
- **Validation**: `SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v`
- **Dependencies**: Slice 1-10
- **Human confirmation needed**: no

---

## 验证命令汇总

```bash
# 单元测试
cd src/backend && python -m pytest tests/community/core/task/ -k "task_discovery" -v

# 端点测试
cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v

# 配置门禁
cd src/backend && python -m ruff check src/agentclaw/community/core/task/task_discovery/
cd src/backend && python -m mypy src/agentclaw/community/core/task/task_discovery/

# 残留引用检查
cd src/backend && grep -r "session_creator\|TaskDiscoveryLifecycle\|create_default_service" src/agentclaw/community/ --include="*.py"

# DI 验证
cd src/backend && python -c "from agentclaw.community.di import get_injector; inj = get_injector(); inj.get('DiscoveryService'); print('di ok')"

# E2E (gated)
SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v
```

---

## 实施路径图

```
Slice 1 (deps) ─────────────────────────────────────────┐
Slice 2 (models) ──┬─── Slice 3 (reader) ────┐         │
                   │                          ├── Slice 5 (service) ──┬── Slice 7 (scheduler) ──┐
                   └─── Slice 4 (initiator) ──┘                        │                        │
                                                                        ├── Slice 8 (router)    │
Slice 6 (protocols) ───────────────────────────────────────────────────┘                        │
                                                                        ├── Slice 9 (di) ───────┤
                                                                        │                        │
                                                                        └── Slice 10 (cleanup) ─┤
                                                                                                 │
                                                                                                 └── Slice 11 (e2e)
```