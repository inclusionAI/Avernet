# task_discovery × autoInitiate 合并 — 技术设计 (HOW)

## 澄清结果复核

- Readiness score: 8/10
- Human-confirmed decisions: 执行路径通过 Protocol seam 解耦注入；无 work_item_url 仅展示不报错；通知在执行成功后；保留双 Protocol
- Assumptions carried into design: `CronRelayServiceProtocol` 已由 `CronModule` 绑定；`NotifySenderPlugin` 已由 `CommunityNotifyModule` 绑定
- Open questions deferred: 无
- Why it is safe to design now: 代码事实清晰，Protocol seam 完备，DI 绑定已验证

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

### 现有 autoInitiate 体系

```
core/cron/services/
├── cron_relay.py            # CronRelayService.run_single_auto_initiate()
├── aicoding/cron_auto_setup.py  # _build_cron_command(kind="autoInitiate")
```

`run_single_auto_initiate(bot_id, user_id, nick_name, dima_url, append_message, model)`：
1. 查 bot device binding + 状态检查
2. 读 template_config.ext.devflow_workflow
3. `forward_request(POST /api/cron/auto-initiate/run-single)` 到 engine
4. engine 侧调 `auto_initiate.execute_single()` 创建 session 并启动执行

### 现有通知基础设施

```
plugin_api/notify_sender.py     # NotifySenderPlugin Protocol + NotifyMessage
plugins/community/notify_sender.py  # CommunityNotifySender (log-only)
plugins/local/notify_sender.py      # NoopNotifySender (test)
di/modules/infrastructure/community/notify.py  # CommunityNotifyModule (singleton bind)
```

`NotifySenderPlugin.send(NotifyMessage, channel="markdown") -> str | None` — Protocol 约定从不抛异常。

### 现有 DI 绑定

- `CronRelayServiceProtocol` — `CronModule` → singleton provider（`cron_module.py:44`）
- `NotifySenderPlugin` — `CommunityNotifyModule` → singleton provider（`notify.py:22`）
- `BotService` — `BotManagementModule` → 已绑定
- `TaskDiscoveryLifecycle` — `TaskDiscoveryModule` → singleton bind

## 相似实现

| 参考文件 | 参考点 |
|---|---|
| `adapters/http/cron/cron_noauth_router.py:57` | `POST /auto-initiate/run-single` 端点：调 `CronRelayServiceProtocol.run_single_auto_initiate()` 的模式 |
| `core/cron/services/cron_relay.py:420` | `run_single_auto_initiate()` 完整签名和返回值结构 |
| `plugins/community/notify_sender.py:27` | `CommunityNotifySender.send()` log-only 实现 |
| `core/bot_dormant/notify_log.py` | `NotifySenderPlugin` 在其他模块中的集成模式 |

## 影响范围

| 文件 | 层级 | 变更类型 | 影响 |
|---|---|---|---|
| `models.py` | core/domain | 扩展 | 新增 `InitiateResult`；`DiscoveredTask` 加方法；`DiscoveryResult` 加字段 |
| `session_creator.py` | core/service | 扩展 | 新增 `TaskInitiator` Protocol + `AutoInitiateExecutor` |
| `discovery_service.py` | core/service | 扩展 | 编排逻辑重构；新增依赖 |
| `lifecycle.py` | core/lifecycle | 修改 | `_discover_once()` 服务构建变更 |
| `router.py` | adapter/http | 修改 | `_build_service()` 适配；响应格式 |
| `task_discovery_module.py` | di | 修改 | 新增 `TaskInitiator` 绑定 |
| `__init__.py` | core | 修改 | docstring 更新 |
| `test_task_discovery_e2e.py` | test | 修改 | 断言更新 |

## 架构约束

- **transport-agnostic**: `TaskInitiator` Protocol 在 core 层定义，不含 transport import
- **Protocol seam**: `AutoInitiateExecutor` 依赖 `CronRelayServiceProtocol`（Protocol），不直接依赖 `CronRelayService`（实现）
- **DI composition root**: `TaskDiscoveryModule` 是 task_discovery 唯一接线点
- **thin adapter**: `router.py` 只转协议，不持领域策略
- **NotifySenderPlugin 从不抛异常**: 通知失败不影响发现流程

## 方案选项

### 选项 A: 直接注入 CronRelayService（耦合 core 模块）

`AutoInitiateExecutor` 通过 `@inject` 直接注入 `CronRelayService`。

- **优点**: 简单直接，调用链短
- **缺点**: task_discovery 直接依赖 cron 模块服务，跨模块耦合
- **风险**: 中 — 违反模块边界独立原则

### 选项 B: 通过 Protocol seam 注入 CronRelayServiceProtocol（推荐）

`AutoInitiateExecutor` 通过 `@inject` 注入 `CronRelayServiceProtocol`（已有 DI 绑定）。

- **优点**: 解耦——task_discovery core 只依赖 Protocol，不依赖实现；`CronRelayServiceProtocol` 已由 `CronModule` 绑定
- **缺点**: 多一层 Protocol 间接
- **风险**: 低

### 选项 C: 走 HTTP 端点

`AutoInitiateExecutor` 通过 httpx 调 `POST /api/public/cron/auto-initiate/run-single`。

- **优点**: 最大解耦——跨进程级别隔离
- **缺点**: 多一跳 HTTP（自己调自己的 API），性能损失；需处理网络错误
- **风险**: 低但 overhead 不必要

## 推荐方案

**选项 B**: Protocol seam + DI 注入 `CronRelayServiceProtocol`。

理由：用户要求解耦；`CronRelayServiceProtocol` 已定义并已由 `CronModule` 绑定为 singleton；Protocol seam 使 core 层不直接依赖 cron 实现；DI 自动解析依赖链。

## 模块/层级变更

### 层级分配

| File | Module | Layer | Change | Rules | Tests |
|---|---|---|---|---|---|
| `models.py` | task_discovery | Domain | add InitiateResult + DiscoveredTask methods + DiscoveryResult fields | R-002 | unit |
| `session_creator.py` | task_discovery | Service | add TaskInitiator Protocol + AutoInitiateExecutor | R-002,R-011 | unit + integration |
| `discovery_service.py` | task_discovery | Service | extend orchestration with initiate + notify | R-002,R-011 | unit + integration |
| `lifecycle.py` | task_discovery | Lifecycle | update _discover_once service construction | R-002 | unit |
| `router.py` | task_discovery | Adapter | update _build_service + response format | R-001 (thin) | endpoint test |
| `task_discovery_module.py` | di | DI | bind TaskInitiator → AutoInitiateExecutor | R-012 | DI test |
| `__init__.py` | task_discovery | Core | update docstring | - | - |
| `test_task_discovery_e2e.py` | tests | Test | update assertions for autoInitiate | - | e2e |

## 接口变更

### 新增: `TaskInitiator` Protocol

```python
# session_creator.py

class TaskInitiator(Protocol):
    """任务执行接口 — 对已发现任务触发 autoInitiate 执行。"""

    async def initiate(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> InitiateResult:
        ...
```

### 新增: `InitiateResult` dataclass

```python
# models.py

@dataclass(frozen=True)
class InitiateResult:
    """autoInitiate 执行结果。"""
    task_id: str
    session_id: str = ""
    session_url: str = ""
    success: bool = False
    error: str | None = None
    raw_response: dict | None = None
```

### 新增: `AutoInitiateExecutor`

```python
# session_creator.py

class AutoInitiateExecutor:
    """通过 CronRelayServiceProtocol 调 run_single_auto_initiate 的实现。"""

    @inject
    def __init__(self, cron_relay: CronRelayServiceProtocol) -> None:
        self._cron_relay = cron_relay

    async def initiate(
        self,
        task: DiscoveredTask,
        *,
        user_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> InitiateResult:
        # 映射 DiscoveredTask → run_single_auto_initiate 参数
        # 调用 self._cron_relay.run_single_auto_initiate(...)
        # 从响应中提取 session_id / session_url
        # 返回 InitiateResult
```

### 修改: `DiscoveryService.__init__()`

```python
class DiscoveryService:
    def __init__(
        self,
        reader: TaskReader,
        session_creator: SessionCreator,
        task_initiator: TaskInitiator,
        notify_sender: NotifySenderPlugin,
    ):
        self._reader = reader
        self._session_creator = session_creator
        self._task_initiator = task_initiator
        self._notify_sender = notify_sender
```

### 修改: `DiscoveryService.discover()` 编排逻辑

```python
async def discover(self, *, user_id, agent_id, model=None) -> list[DiscoveryResult]:
    tasks = self._reader.read_pending_tasks()
    results = []
    for task in tasks:
        result = await self._discover_single(task, user_id=user_id, agent_id=agent_id, model=model)
        results.append(result)
    return results

async def _discover_single(self, task, *, user_id, agent_id, model) -> DiscoveryResult:
    if task.work_item_url:
        # 路径 A: autoInitiate 执行
        initiate_result = await self._task_initiator.initiate(task, user_id=user_id, agent_id=agent_id, model=model)
        if initiate_result.success:
            self._send_notification(task, user_id, initiate_result.session_url, initiated=True)
        return DiscoveryResult(task=task, initiate_result=initiate_result, ...)
    else:
        # 路径 B: SessionCreator 仅展示
        session = await self._session_creator.create_session(task, user_id=user_id, agent_id=agent_id, model=model)
        self._send_notification(task, user_id, session.session_url, initiated=False)
        return DiscoveryResult(task=task, session=session, ...)
```

### 修改: `DiscoveryResult`

```python
@dataclass
class DiscoveryResult:
    task: DiscoveredTask
    initiated: bool = False          # 是否走了 autoInitiate
    initiate_result: InitiateResult | None = None
    session: DiscoverySession | None = None  # 降级路径
    notification_sent: bool = False
    notification_message: str = ""
    error: str | None = None

    @property
    def success(self) -> bool:
        if self.initiated:
            return self.initiate_result is not None and self.initiate_result.success
        return self.session is not None and self.error is None
```

### 修改: `DiscoveredTask` 新增方法

```python
def to_auto_initiate_append_message(self) -> str:
    """生成 autoInitiate 的 append_message（补充说明）。"""
    parts = []
    if self.business_scenario:
        parts.append(f"业务场景：{self.business_scenario}")
    if self.discovery_basis:
        parts.append(f"挖掘依据：{self.discovery_basis}")
    return "\n".join(parts)

def to_notification_body(self, initiated: bool) -> str:
    """生成通知消息正文。"""
    action = "已自动启动执行" if initiated else "待确认"
    lines = [
        f"项目名称：{self.project_name}",
        f"项目简介：{self.description}",
        f"业务场景：{self.business_scenario}",
        f"挖掘依据：{self.discovery_basis}",
        f"状态：{action}",
    ]
    if self.work_item_url:
        lines.append(f"需求链接：{self.work_item_url}")
    return "\n".join(lines)
```

## 数据变更

不涉及数据库 schema 变更。`discovered_tasks` 表结构不变（9 字段，含已有的 `work_item_url` 列）。

## 权限与安全

- `AutoInitiateExecutor` 通过 `CronRelayServiceProtocol` 调用 `run_single_auto_initiate()`，该方法内部已做 bot device binding 检查和状态验证
- `NotifySenderPlugin.send()` Protocol 约定从不抛异常——通知失败不泄露用户信息
- HTTP 端点 `/api/public/task-discovery/discover` 保持现有无需认证模式（与 `cron_noauth_router` 一致）

## 兼容性

- **SessionCreator Protocol**: 接口不变，`EngineSessionCreator` 实现不变
- **TaskReader Protocol**: 接口不变，`SqliteTaskReader` / `MockTaskReader` 不变
- **HTTP API**: `POST /discover` 响应格式扩展（新增 `initiated` 字段），不删除现有字段
- **DI**: `TaskDiscoveryModule` 新增绑定，不修改现有绑定
- **lifecycle**: 定时调度行为不变，仅服务构建方式变更

## 性能与稳定性

- `run_single_auto_initiate()` 内部通过 `forward_request()` 到 engine，有完整超时和错误处理
- `_discover_single()` 保持 per-task try/except，单任务失败不阻塞其他任务
- `NotifySenderPlugin.send()` 是同步方法（Protocol 约定），在 async 上下文中直接调用（log-only，不阻塞）
- lifecycle 串行遍历 bot（与现有行为一致），bot 规模大时需评估（非本次范围）

## 灰度/发布/回滚

- **灰度**: 通过 `TASK_DISCOVERY_AUTO_START` 环境变量控制 lifecycle 自动调度（已有机制）
- **回滚**: 将 `DiscoveryService` 的 `task_initiator` 依赖设为可选（`None` 时降级为仅 SessionCreator 路径）。但根据用户确认保留双 Protocol，最简回滚方式是 DI 绑定切换
- **发布**: 无数据迁移，无 schema 变更，直接替换代码

## 测试策略

| 层级 | 测试内容 | 方式 |
|---|---|---|
| Unit | `InitiateResult` 序列化 | 纯 dataclass 断言 |
| Unit | `DiscoveredTask.to_auto_initiate_append_message()` | 内联数据断言 |
| Unit | `AutoInitiateExecutor.initiate()` 成功/失败 | Mock `CronRelayServiceProtocol` |
| Unit | `DiscoveryService.discover()` 分流逻辑 | Mock reader + initiator + creator + notify |
| Unit | `DiscoveryService` 通知发送 | Mock `NotifySenderPlugin` |
| Integration | DI 绑定正确性 | Mini container 构建 |
| E2E | discover → autoInitiate → notify 全链路 | gated singlebox e2e |

## 风险与取舍

| 风险 | 级别 | 缓解 |
|---|---|---|
| `CronRelayServiceProtocol` 在 community profile 下未绑定 | 低 — `CronModule` 已绑定 | DI 构建时即发现 |
| `run_single_auto_initiate` 需要设备在线 | 中 | `InitiateResult` 含 `error` 字段，日志记录 |
| 通知 send() 同步阻塞 async | 低 — community log-only | 若 corp 版需 async，后续 Protocol 升级 |
| lifecycle 串行遍历 bot 规模限制 | 低 — 现有行为 | 非本次范围 |

## 领域模型理解

- **TaskInitiator**: 任务执行接口，将"发现"转为"执行"。生命周期：接收 `DiscoveredTask` → 调 autoInitiate → 返回 `InitiateResult`。属 task_discovery core 层。
- **AutoInitiateExecutor**: `TaskInitiator` 默认实现，桥接 task_discovery 和 cron 模块。通过 `CronRelayServiceProtocol` 解耦。
- **InitiateResult**: 执行结果值对象。frozen dataclass，含 session 信息和错误。
- **NotifySenderPlugin 集成**: 通知发送——执行成功后告知用户。community log-only，corp 可替换钉钉。

## 现有模型重合度

| Requested concept | Existing | Overlap | Decision |
|---|---|---|---|
| TaskInitiator | SessionCreator | medium | coexist — 双路径 |
| AutoInitiateExecutor | EngineSessionCreator | low | create — 不同实现 |
| InitiateResult | DiscoverySession | low | coexist — 不同生命周期阶段 |
| NotifySenderPlugin | (无现有集成) | none | extend — 新增到 DiscoveryService |

## 领域边界与所有权

- **task_discovery** 拥有：发现编排流程、`DiscoveredTask` 模型、`TaskInitiator` Protocol
- **cron 模块** 拥有：`CronRelayService`、autoInitiate 执行链路、设备检查逻辑
- **plugin_api** 拥有：`NotifySenderPlugin` Protocol、`NotifyMessage` 模型
- **边界**: task_discovery 通过 Protocol 调用 cron 服务和通知插件，不跨边界持有实现

## 方案取舍记录

1. **选 Protocol seam 而非直接注入** — 更好的解耦，代价是多一层间接
2. **选执行后通知而非执行前** — 用户确认；告知结果更有价值
3. **保留 SessionCreator 而非完全替换** — 用户确认保留降级路径；无 URL 的任务仍需展示

## 架构决策确认点

所有决策已在 Clarification 阶段由用户确认。无额外确认需求。

## 架构适配性检查

- **layering**: `TaskInitiator` Protocol 在 core 层，`AutoInitiateExecutor` 也在 core 层（与 `EngineSessionCreator` 模式一致——Protocol + 默认实现同文件）
- **dependency direction**: task_discovery → (Protocol) → cron_relay / notify_sender；不反向
- **module boundaries**: 通过 Protocol 跨模块，不 import 实现
- **conventions**: 与现有 `SessionCreator` / `EngineSessionCreator` 模式一致
