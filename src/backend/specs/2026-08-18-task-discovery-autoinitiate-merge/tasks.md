# task_discovery 通知集成 — TDD 任务清单

## 任务总览

| Slice | 目标 | 文件 | 依赖 |
|---|---|---|---|
| 1 | DiscoveryService 通知集成 | `discovery_service.py` | 无 |
| 2 | Lifecycle 适配 | `lifecycle.py` | Slice 1 |
| 3 | HTTP Router 适配 | `router.py` | Slice 1 |
| 4 | __init__.py docstring 更新 | `__init__.py` | 无 |
| 5 | E2E 测试更新 | `test_task_discovery_e2e.py` | Slice 1-3 |

---

## Slice 1: DiscoveryService 通知集成

- **Goal**: `DiscoveryService` 新增 `notify_sender` 依赖；session 创建成功后调 `NotifySenderPlugin.send()`；`DiscoveryResult` 加 `notification_sent` 字段
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/discovery_service.py`
- **Tests first**:
  - 测试 `_send_notification()` 成功路径：Mock `NotifySenderPlugin.send()` 返回 msg_id → `notification_sent=True`
  - 测试 `_send_notification()` 失败路径：Mock `send()` 返回 `None` → `notification_sent=False`，不抛异常
  - 测试 `discover()` session 创建成功后调 `notify_sender.send()`
  - 测试 `discover()` session 创建失败时不调 `notify_sender.send()`
  - 测试 `NotifyMessage.title` 为"发现待确认任务"
  - 测试 `NotifyMessage.recipient` 为 `user_id`
  - 测试 `NotifyMessage.deep_link` 为 `session_url`
  - 测试 `DiscoveryResult.notification_sent` 默认为 `False`
  - 测试 `create_default_service()` 构建含 `notify_sender` 的服务
- **Implementation**:
  - import `NotifySenderPlugin` + `NotifyMessage` from `plugin_api/notify_sender`
  - `DiscoveryResult` 加 `notification_sent: bool = False` 字段
  - `DiscoveryService.__init__()` 新增 `notify_sender: NotifySenderPlugin` 参数
  - 新增 `_send_notification(task, user_id, session_url) -> bool` 方法
  - `_discover_single()` 中 session 创建成功后调 `_send_notification()`，结果写入 `DiscoveryResult.notification_sent`
  - session 创建失败时 `notification_sent=False`
  - `create_default_service()` 新增 `notify_sender` 参数并传入 `DiscoveryService`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "discovery_service_notify" -v`
- **Dependencies**: 无
- **Human confirmation needed**: no

## Slice 2: Lifecycle 适配

- **Goal**: `TaskDiscoveryLifecycle` 通过 `@inject` 注入 `NotifySenderPlugin`，传给 `create_default_service()`
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/lifecycle.py`
- **Tests first**:
  - 测试 `__init__` 接受 `notify_sender` 参数
  - 测试 `_discover_once()` 构建的服务含 `notify_sender`
  - 测试 lifecycle 启动/取消行为不变
- **Implementation**:
  - import `NotifySenderPlugin` from `plugin_api/notify_sender`
  - `__init__()` 新增 `@inject` 参数 `notify_sender: NotifySenderPlugin`
  - `_discover_once()` 中 `create_default_service()` 调用传入 `notify_sender=self._notify_sender`
- **Validation**: `cd src/backend && python -m pytest tests/community/core/task/ -k "lifecycle_notify" -v`
- **Dependencies**: Slice 1
- **Human confirmation needed**: no

## Slice 3: HTTP Router 适配

- **Goal**: `POST /discover` 通过 `Injected(NotifySenderPlugin)` 注入依赖；响应含 `notification_sent`
- **Files**:
  - `src/backend/src/agentclaw/community/adapters/http/task_discovery/router.py`
- **Tests first**:
  - 测试 `POST /discover` 响应含 `notification_sent` 字段
  - 测试 `_build_service()` 接受 `notify_sender` 并传入服务
  - 测试 `GET /status` 行为不变
- **Implementation**:
  - import `NotifySenderPlugin` from `plugin_api/notify_sender`
  - import `Injected` (参考 `cron_noauth_router.py`)
  - `_build_service(notify_sender)` 接受并传递 `notify_sender`
  - `discover_tasks()` 新增 `notify_sender: NotifySenderPlugin = Injected(NotifySenderPlugin)` 参数
  - 响应中每个 task 加 `"notification_sent": r.notification_sent`
- **Validation**: `cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v`
- **Dependencies**: Slice 1
- **Human confirmation needed**: no

## Slice 4: __init__.py docstring 更新

- **Goal**: 更新模块 docstring 反映通知投递
- **Files**:
  - `src/backend/src/agentclaw/community/core/task/task_discovery/__init__.py`
- **Tests first**: 无（文档变更）
- **Implementation**:
  - 更新 docstring：1. 读已发现任务 2. 为每个任务创建 engine session 3. session 创建成功后投递通知 4. 用户在 session 中确认后执行
- **Validation**: `python -c "from agentclaw.community.core.task.task_discovery import __doc__; print(__doc__)"`
- **Dependencies**: 无
- **Human confirmation needed**: no

## Slice 5: E2E 测试更新

- **Goal**: e2e 测试加通知日志断言 + `notification_sent` 字段断言
- **Files**:
  - `src/backend/tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **Tests first**:
  - 断言每个 task 响应含 `notification_sent` 字段
  - 断言通知日志可见（`CommunityNotifySender` log 输出）
- **Implementation**:
  - 在逐任务验证循环中加 `notification_sent` 字段断言
  - 可选：捕获日志输出验证 `[CommunityNotifySender]` 出现
- **Validation**: `SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v`
- **Dependencies**: Slice 1-3
- **Human confirmation needed**: no

---

## 验证命令汇总

```bash
# 单元测试
cd src/backend && python -m pytest tests/community/core/task/ -v

# 端点测试
cd src/backend && python -m pytest tests/community/endpoints/ -k "task_discovery" -v

# 配置门禁
cd src/backend && python -m ruff check src/agentclaw/community/core/task/task_discovery/
cd src/backend && python -m mypy src/agentclaw/community/core/task/task_discovery/

# E2E (gated)
SINGLEBOX_TASK_E2E=1 cd src/backend && python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -v
```
