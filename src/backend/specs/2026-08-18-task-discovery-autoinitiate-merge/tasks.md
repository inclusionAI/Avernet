# task_discovery × autoInitiate 合并 — TDD 任务清单

## 任务总览

| Slice | 目标 | 文件 | 依赖 |
|---|---|---|---|
| 1 | InitiateResult 模型 + DiscoveredTask 方法 | `models.py` | 无 |
| 2 | TaskInitiator Protocol + AutoInitiateExecutor | `session_creator.py` | Slice 1 |
| 3 | DiscoveryService 编排扩展 | `discovery_service.py` | Slice 1, 2 |
| 4 | NotifySenderPlugin 集成 | `discovery_service.py` | Slice 3 |
| 5 | Lifecycle 适配 | `lifecycle.py` | Slice 3 |
| 6 | HTTP Router 适配 | `router.py` | Slice 3 |
| 7 | DI 接线 | `task_discovery_module.py` | Slice 2 |
| 8 | __init__.py docstring 更新 | `__init__.py` | 无 |
| 9 | E2E 测试更新 | `test_task_discovery_e2e.py` | Slice 1-7 |

---

## Slice 1: InitiateResult 模型 + DiscoveredTask 方法

- **Goal**: 新增 `InitiateResult` dataclass；`DiscoveredTask` 增加 `to_auto_initiate_append_message()` 和 `to_notification_body()`；`DiscoveryResult` 扩展字段
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/models.py`
- **Tests first**:
  - 测试 `InitiateResult` 构造和默认值
  - 测试 `DiscoveredTask.to_auto_initiate_append_message()` 含 business_scenario + discovery_basis
  - 测试 `DiscoveredTask.to_notification_body(initiated=True)` 含"已自动启动执行"
  - 测试 `DiscoveredTask.to_notification_body(initiated=False)` 含"待确认"
  - 测试 `DiscoveryResult` 新字段 `initiated`, `initiate_result`, `notification_sent`
  - 测试 `DiscoveryResult.success` 属性在有/无 initiate_result 时的行为
- **Implementation**:
  - 新增 `@dataclass(frozen=True) class InitiateResult`
  - `DiscoveredTask` 加 `to_auto_initiate_append_message()` / `to_notification_body(initiated)` 方法
  - `DiscoveryResult` 加 `initiated: bool`, `initiate_result: InitiateResult | None`, `notification_sent: bool` 字段
  - 更新 `success` property 逻辑
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "initiate_result or discovered_task_methods or discovery_result_fields" -v`
- **Dependencies**: 无
- **Human confirmation needed**: no

## Slice 2: TaskInitiator Protocol + AutoInitiateExecutor

- **Goal**: 新增 `TaskInitiator` Protocol 和 `AutoInitiateExecutor` 实现；映射 `DiscoveredTask` → `run_single_auto_initiate` 参数
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/session_creator.py`
- **Tests first**:
  - 测试 `AutoInitiateExecutor.initiate()` 成功路径：Mock `CronRelayServiceProtocol.run_single_auto_initiate()` 返回含 session_id 的响应 → `InitiateResult(success=True)`
  - 测试 `initiate()` 失败路径：Mock 抛 `ValueError` → `InitiateResult(success=False, error="...")`
  - 测试 `work_item_url` 正确映射为 `dima_url` 参数
  - 测试 `append_message` 包含 `business_scenario` + `discovery_basis`
  - 测试 `nick_name` 缺省用 `user_id`
- **Implementation**:
  - 新增 `from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol` import
  - 新增 `from agentclaw.community.core.task.task_discovery.models import InitiateResult` import
  - 定义 `class TaskInitiator(Protocol)` 含 `async initiate(...)` 方法
  - 定义 `class AutoInitiateExecutor` 含 `@inject __init__(self, cron_relay: CronRelayServiceProtocol)` + `async initiate(...)` 实现
  - `initiate()` 内部：映射参数 → 调 `self._cron_relay.run_single_auto_initiate()` → 解析响应 → 返回 `InitiateResult`
  - try/except 包裹，失败返 `InitiateResult(success=False, error=str(exc))`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "auto_initiate_executor" -v`
- **Dependencies**: Slice 1
- **Human confirmation needed**: no

## Slice 3: DiscoveryService 编排扩展

- **Goal**: `DiscoveryService` 新增 `task_initiator` 依赖，编排逻辑分流（有 URL → initiate / 无 URL → session）
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/discovery_service.py`
- **Tests first**:
  - 测试有 `work_item_url` 的任务走 `task_initiator.initiate()` 路径
  - 测试无 `work_item_url` 的任务走 `session_creator.create_session()` 路径
  - 测试 `DiscoveryResult.initiated=True` 对应有 URL 任务
  - 测试 `DiscoveryResult.initiated=False` 对应无 URL 任务
  - 测试单任务失败不阻塞其他任务
  - 测试 `create_default_service()` 构建含全部依赖的服务
- **Implementation**:
  - import `TaskInitiator` from `session_creator`、`NotifySenderPlugin` + `NotifyMessage` from `plugin_api/notify_sender`
  - `__init__()` 新增 `task_initiator: TaskInitiator` + `notify_sender: NotifySenderPlugin` 参数
  - 重构 `_discover_single()` 为两条路径
  - 路径 A: `task.work_item_url` 存在 → `self._task_initiator.initiate(task, ...)` → 成功后发通知
  - 路径 B: `task.work_item_url` 为 None → `self._session_creator.create_session(task, ...)` → 成功后发通知
  - 更新 `create_default_service()` 工厂方法签名
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "discovery_service_orchestration" -v`
- **Dependencies**: Slice 1, 2
- **Human confirmation needed**: no

## Slice 4: NotifySenderPlugin 集成

- **Goal**: `DiscoveryService._send_notification()` 方法调 `NotifySenderPlugin.send(NotifyMessage)`
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/discovery_service.py`
- **Tests first**:
  - 测试 autoInitiate 成功后调 `notify_sender.send()`
  - 测试 `NotifyMessage.title` 正确（"发现并启动了新任务" / "发现待确认任务"）
  - 测试 `NotifyMessage.body` 含任务详情
  - 测试 `NotifyMessage.recipient` 为 `user_id`
  - 测试 `NotifyMessage.deep_link` 为 `session_url`
  - 测试 `send()` 抛异常时不阻塞流程（catch + log）
  - 测试 `notification_sent=True` 在成功发送后
- **Implementation**:
  - 新增 `_send_notification(task, user_id, session_url, initiated)` 方法
  - 构造 `NotifyMessage(title, body=task.to_notification_body(initiated), recipient=user_id, deep_link=session_url)`
  - 调 `self._notify_sender.send(message)` → catch 异常 → log warning
  - 在 `_discover_single()` 两条路径中调 `_send_notification()`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "notify_integration" -v`
- **Dependencies**: Slice 3
- **Human confirmation needed**: no

## Slice 5: Lifecycle 适配

- **Goal**: `_discover_once()` 构建含 `TaskInitiator` + `NotifySenderPlugin` 的 `DiscoveryService`
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/lifecycle.py`
- **Tests first**:
  - 测试 `_discover_once()` 构建的服务含 `task_initiator` 和 `notify_sender`
  - 测试 lifecycle 启动/取消行为不变
  - 测试 `@inject` 注入 `CronRelayServiceProtocol` + `NotifySenderPlugin`（额外依赖）
- **Implementation**:
  - `TaskDiscoveryLifecycle.__init__()` 新增 `@inject` 参数：`cron_relay: CronRelayServiceProtocol`、`notify_sender: NotifySenderPlugin`
  - `_discover_once()` 中构建 `AutoInitiateExecutor` 并传入 `create_default_service()` 或直接构造 `DiscoveryService`
  - 注意：lifecycle 现有模式是每 bot 创建独立 `DiscoveryService`——需要确保 `AutoInitiateExecutor` 可复用（singleton 或每 bot 新建）
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "lifecycle_discover_once" -v`
- **Dependencies**: Slice 3
- **Human confirmation needed**: no

## Slice 6: HTTP Router 适配

- **Goal**: `_build_service()` 适配新依赖；响应格式含 `initiated` 字段
- **Files**:
  - `src/backend/src/agentclaw/community/adapters/http/task_discovery/router.py`
- **Tests first**:
  - 测试 `POST /discover` 响应含 `initiated` 字段
  - 测试 `POST /discover` 响应有 URL 任务标记 `initiated=True`
  - 测试 `POST /discover` 响应无 URL 任务标记 `initiated=False`
  - 测试 `GET /status` 行为不变
- **Implementation**:
  - `_build_service()` 从 DI 获取或手动构建含全部依赖的 `DiscoveryService`
  - `discover_tasks()` 端点响应中每个 task 加 `"initiated": r.initiated` 字段
  - 有 `initiate_result` 时加 `session_id` / `session_url` 从 `initiate_result` 提取
- **Validation**: `cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v`
- **Dependencies**: Slice 3
- **Human confirmation needed**: no

## Slice 7: DI 接线

- **Goal**: `TaskDiscoveryModule` 新增 `TaskInitiator` → `AutoInitiateExecutor` 绑定
- **Files**:
  - `src/backend/src/agentclaw/community/di/modules/task_discovery_module.py`
- **Tests first**:
  - 测试 DI 容器构建后 `TaskInitiator` 可注入
  - 测试 `AutoInitiateExecutor` 的 `CronRelayServiceProtocol` 依赖可解析
  - 测试 `NotifySenderPlugin` 依赖可解析
  - 测试架构守护测试 `test_lifecycle_discovery.py` 仍通过
- **Implementation**:
  - `from agentclaw.community.core.task.task_discovery.session_creator import TaskInitiator, AutoInitiateExecutor`
  - `configure()` 新增 `binder.bind(TaskInitiator, to=AutoInitiateExecutor, scope=singleton)`
  - 验证 `AutoInitiateExecutor` 通过 `@inject` 注入 `CronRelayServiceProtocol`（由 `CronModule` 解析）
- **Validation**: `cd src/backend && python -m pytest tests/community/architecture/ -k "lifecycle_discovery" -v`
- **Dependencies**: Slice 2
- **Human confirmation needed**: no

## Slice 8: __init__.py docstring 更新

- **Goal**: 更新模块 docstring 反映新流程
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/__init__.py`
- **Tests first**: 无（文档变更）
- **Implementation**:
  - 更新 docstring：1. 读已发现任务 2. 有 work_item_url → autoInitiate 执行 3. 无 work_item_url → 建 session 展示 4. 执行成功后发通知
  - 更新触发方式描述
- **Validation**: `python -c "from agentclaw.community.core.task.task_discovery import __doc__; print(__doc__)"`
- **Dependencies**: 无
- **Human confirmation needed**: no

## Slice 9: E2E 测试更新

- **Goal**: 更新 e2e 测试验证 autoInitiate 触发路径
- **Files**:
  - `src/backend/tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **Tests first**:
  - 测试数据包含有/无 `work_item_url` 的任务
  - 断言有 URL 的任务响应中 `initiated=True`
  - 断言无 URL 的任务响应中 `initiated=False` 且有 `session_url`
  - 断言通知日志可见（`CommunityNotifySender` log 输出）
- **Implementation**:
  - 更新 `_MOCK_TASKS` 确保有/无 `work_item_url` 的任务各至少一条
  - 更新断言逻辑检查 `initiated` 字段
  - 更新 `session_id` / `session_url` 提取逻辑（可能来自 `initiate_result` 或 `session`）
- **Validation**: `SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v`
- **Dependencies**: Slice 1-7
- **Human confirmation needed**: no

---

## 验证命令汇总

```bash
# 单元测试
cd src/backend && python -m pytest tests/community/core/task/ -v

# 架构守护
cd src/backend && python -m pytest tests/community/architecture/ -k "lifecycle_discovery" -v

# 端点测试
cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v

# 配置门禁
cd src/backend && python -m ruff check src/agentclaw/community/core/task/task_discovery/
cd src/backend && python -m mypy src/agentclaw/community/core/task/task_discovery/

# E2E (gated)
SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v
```
