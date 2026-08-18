# task_discovery 通知集成 — 技术设计 (HOW)

## 澄清结果复核

- Readiness score: 9/10
- Human-confirmed decisions: discover 阶段所有任务仅展示等确认；不按 work_item_url 分支；autoInitiate 执行是后续独立步骤
- Assumptions carried into design: `NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定为 singleton
- Open questions deferred: 无
- Why it is safe to design now: 范围明确，代码事实清晰，DI 绑定已验证

---

## 代码背景

### 现有 task_discovery 模块结构

```
core/task/task_discovery/
├── __init__.py              # 模块 docstring
├── models.py                # DiscoveredTask + DiscoverySession（frozen dataclass）
├── discovery_service.py     # DiscoveryService 编排核心 + DiscoveryResult
├── task_reader.py           # TaskReader Protocol + SqliteTaskReader + MockTaskReader
├── session_creator.py       # SessionCreator Protocol + EngineSessionCreator
└── lifecycle.py             # TaskDiscoveryLifecycle（定时调度）
```

现有流程：`TaskReader.read_pending_tasks()` → `SessionCreator.create_session()` → 返回 `DiscoveryResult(task, session, notification_message)`

通知消息仅作为 `notification_message` 字符串存在于 `DiscoveryResult` 中，不投递到任何通道。

### 现有通知基础设施

```
plugin_api/notify_sender.py     # NotifySenderPlugin Protocol + NotifyMessage
plugins/community/notify_sender.py  # CommunityNotifySender (log-only)
plugins/local/notify_sender.py      # NoopNotifySender (test)
di/modules/infrastructure/community/notify.py  # CommunityNotifyModule (singleton bind)
```

`NotifySenderPlugin.send(NotifyMessage, channel="markdown") -> str | None` — Protocol 约定从不抛异常。返回 `str` 为消息 ID（成功），`None` 为失败。

### 现有 DI 绑定

- `NotifySenderPlugin` — `CommunityNotifyModule` → singleton provider（`notify.py:22`）
- `BotService` — `BotManagementModule` → 已绑定
- `TaskDiscoveryLifecycle` — `TaskDiscoveryModule` → singleton bind

`TaskDiscoveryModule` 无需修改 — `NotifySenderPlugin` 由 `CommunityNotifyModule` 绑定，injector 自动解析。

## 相似实现

| 参考文件 | 参考点 |
|---|---|
| `adapters/http/cron/cron_noauth_router.py:65` | `Injected(CronRelayServiceProtocol)` — router 中通过 DI 注入依赖的模式 |
| `plugins/community/notify_sender.py:39` | `CommunityNotifySender.send()` log-only 实现 |
| `core/bot_dormant/notify_log.py` | `NotifySenderPlugin` 在其他模块中的集成模式 |

## 影响范围

| 文件 | 层级 | 变更类型 | 影响 |
|---|---|---|---|
| `discovery_service.py` | core/service | 扩展 | 新增 `notify_sender` 依赖 + `_send_notification()` + `DiscoveryResult.notification_sent` |
| `lifecycle.py` | core/lifecycle | 修改 | `__init__` 注入 `NotifySenderPlugin`；`_discover_once()` 传参 |
| `router.py` | adapter/http | 修改 | `Injected(NotifySenderPlugin)` + 响应格式 |
| `__init__.py` | core | 修改 | docstring 更新 |
| `test_task_discovery_e2e.py` | test | 修改 | 通知日志断言 |

**不修改的文件**: `models.py`, `task_reader.py`, `session_creator.py`, `task_discovery_module.py`

## 架构约束

- **transport-agnostic**: `DiscoveryService` core 层不 import transport；`NotifySenderPlugin` 是 Protocol
- **DI composition root**: `NotifySenderPlugin` 由 `CommunityNotifyModule` 绑定，lifecycle 通过 `@inject` 解析，router 通过 `Injected()` 解析
- **thin adapter**: `router.py` 只转协议，不持领域策略
- **NotifySenderPlugin 从不抛异常**: 通知失败不影响发现流程

## 接口变更

### 修改: `DiscoveryResult`

```python
# discovery_service.py

@dataclass
class DiscoveryResult:
    """单次发现流程的结果。"""

    task: DiscoveredTask
    session: Optional[DiscoverySession] = None
    notification_message: str = ""
    notification_sent: bool = False        # 新增
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.session is not None and self.error is None
```

### 修改: `DiscoveryService.__init__()`

```python
class DiscoveryService:
    def __init__(
        self,
        reader: TaskReader,
        session_creator: SessionCreator,
        notify_sender: NotifySenderPlugin,     # 新增
    ):
        self._reader = reader
        self._session_creator = session_creator
        self._notify_sender = notify_sender
        self._discoveries: dict[str, DiscoveryResult] = {}
```

### 新增: `DiscoveryService._send_notification()`

```python
def _send_notification(
    self,
    task: DiscoveredTask,
    user_id: str,
    session_url: str,
) -> bool:
    """通过 NotifySenderPlugin 投递通知，返回是否发送成功。"""
    message = NotifyMessage(
        title="发现待确认任务",
        body=task.to_notification_message(session_url),
        recipient=user_id,
        deep_link=session_url,
    )
    msg_id = self._notify_sender.send(message)
    if msg_id:
        logger.info(
            "[task_discovery] notification sent for task %s (msg_id=%s)",
            task.task_id, msg_id,
        )
        return True
    else:
        logger.warning(
            "[task_discovery] notification send returned None for task %s",
            task.task_id,
        )
        return False
```

### 修改: `DiscoveryService._discover_single()`

```python
async def _discover_single(
    self,
    task: DiscoveredTask,
    *,
    user_id: str,
    agent_id: str,
    model: str | None,
) -> DiscoveryResult:
    """处理单个任务的发现流程。"""
    try:
        session = await self._session_creator.create_session(
            task,
            user_id=user_id,
            agent_id=agent_id,
            model=model,
        )
        message = task.to_notification_message(session.session_url)

        # session 创建成功后投递通知
        notification_sent = self._send_notification(
            task, user_id, session.session_url
        )

        logger.info(
            "[task_discovery] task %s → session %s (url=%s, notified=%s)",
            task.task_id,
            session.session_id,
            session.session_url,
            notification_sent,
        )

        return DiscoveryResult(
            task=task,
            session=session,
            notification_message=message,
            notification_sent=notification_sent,
        )
    except Exception as exc:
        logger.error(
            "[task_discovery] failed to create session for task %s: %s",
            task.task_id,
            exc,
        )
        return DiscoveryResult(
            task=task,
            error=str(exc),
            notification_sent=False,
        )
```

### 修改: `create_default_service()`

```python
def create_default_service(
    data_file: str,
    notify_sender: NotifySenderPlugin,        # 新增
    engine_base_url: str | None = None,
    engine_frontend_url: str | None = None,
) -> DiscoveryService:
    return DiscoveryService(
        reader=SqliteTaskReader(data_file),
        session_creator=EngineSessionCreator(
            engine_base_url=engine_base_url,
            engine_frontend_url=engine_frontend_url,
        ),
        notify_sender=notify_sender,
    )
```

### 修改: `TaskDiscoveryLifecycle.__init__()`

```python
@inject
def __init__(
    self,
    bot_service: "BotService",
    notify_sender: NotifySenderPlugin,       # 新增
) -> None:
    self._bot_service: Any = bot_service
    self._notify_sender = notify_sender
    self._task: asyncio.Task | None = None
```

### 修改: `TaskDiscoveryLifecycle._discover_once()`

```python
service = create_default_service(
    data_file=data_file,
    notify_sender=self._notify_sender,      # 新增
    engine_base_url=engine_url,
    engine_frontend_url=frontend_url,
)
```

### 修改: `router.py` — discover_tasks() + _build_service()

```python
from agentclaw.community.plugin_api.notify_sender import (
    NotifySenderPlugin,
)


def _build_service(notify_sender: NotifySenderPlugin) -> DiscoveryService:
    engine_url = os.environ.get("TASK_DISCOVERY_ENGINE_URL", "http://localhost:20003")
    frontend_url = os.environ.get("TASK_DISCOVERY_FRONTEND_URL", "http://localhost:8000")
    return create_default_service(
        data_file=_resolve_db_path(),
        notify_sender=notify_sender,
        engine_base_url=engine_url,
        engine_frontend_url=frontend_url,
    )


@router.post("/discover")
async def discover_tasks(
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
    notify_sender: NotifySenderPlugin = Injected(NotifySenderPlugin),
) -> dict:
    ...
    service = _build_service(notify_sender=notify_sender)
    results = await service.discover(user_id=user_id, agent_id=agent_id)

    return {
        "success": True,
        "discovered": len(results),
        "tasks": [
            {
                "task_id": r.task.task_id,
                "project_name": r.task.project_name,
                "success": r.success,
                "session_id": r.session.session_id if r.session else None,
                "session_url": r.session.session_url if r.session else None,
                "notification_sent": r.notification_sent,   # 新增
                "error": r.error,
            }
            for r in results
        ],
    }
```

## 数据变更

不涉及数据库 schema 变更。

## 权限与安全

- `NotifySenderPlugin.send()` Protocol 约定从不抛异常——通知失败不泄露用户信息
- HTTP 端点保持现有无需认证模式

## 兼容性

- **SessionCreator Protocol**: 接口不变
- **TaskReader Protocol**: 接口不变
- **DiscoveredTask / DiscoverySession**: 不变（复用现有 `to_notification_message()`）
- **HTTP API**: `POST /discover` 响应扩展（新增 `notification_sent` 字段），不删除现有字段
- **DI**: `TaskDiscoveryModule` 不修改；`NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定
- **lifecycle**: 定时调度行为不变，仅构造函数新增依赖

## 性能与稳定性

- `_send_notification()` 调用同步 `send()` 方法（Protocol 约定），community log-only 不阻塞
- `_discover_single()` 保持 per-task try/except，单任务失败不阻塞其他任务

## 灰度/发布/回滚

- **灰度**: 通过 `TASK_DISCOVERY_AUTO_START` 环境变量控制 lifecycle 自动调度（已有机制）
- **回滚**: 移除 `notify_sender` 参数及 `_send_notification()` 调用，回到仅返回 `notification_message` 字符串的状态
- **发布**: 无数据迁移，无 schema 变更

## 测试策略

| 层级 | 测试内容 | 方式 |
|---|---|---|
| Unit | `DiscoveryService._send_notification()` 成功/失败 | Mock `NotifySenderPlugin` |
| Unit | `DiscoveryService.discover()` 通知发送时机 | Mock reader + creator + notify |
| Unit | `DiscoveryResult.notification_sent` 字段 | 纯 dataclass 断言 |
| Unit | `DiscoveryService` session 创建失败时不发通知 | Mock creator 抛异常 |
| E2E | discover → session → notify 全链路 | gated singlebox e2e |

## 风险与取舍

| 风险 | 级别 | 缓解 |
|---|---|---|
| `NotifySenderPlugin.send()` 同步阻塞 async | 低 — community log-only | 若 corp 版需 async，后续 Protocol 升级 |
| `Injected()` 在 task_discovery router 中不可用 | 低 — cron router 已用同一模式 | DI 构建时即发现 |

## 领域边界与所有权

- **task_discovery** 拥有：发现编排流程、`DiscoveryService`、`DiscoveryResult`
- **plugin_api** 拥有：`NotifySenderPlugin` Protocol、`NotifyMessage` 模型
- **边界**: task_discovery 通过 Protocol 调用通知插件，不跨边界持有实现

## 方案取舍记录

1. **复用 `to_notification_message()` 而非新增方法** — minimize changes，现有方法已包含完整任务详情
2. **通知在 session 创建成功后发送** — 通知的目的是指引用户去 session 确认，先有 session 再通知
3. **不引入 TaskInitiator / ExecutionMode** — autoInitiate 执行不在 discovery 阶段，是后续独立 spec
