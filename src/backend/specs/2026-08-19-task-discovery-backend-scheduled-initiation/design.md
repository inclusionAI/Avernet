# Task Discovery 后端定时发起 — 技术设计 (HOW)

- **日期**: 2026-08-19
- **状态**: 设计中
- **前置 spec**: `2026-08-18-task-discovery`（模块落地）、`2026-08-18-task-discovery-autoinitiate-merge`（通知集成）
- **替代方案**: `frolicking-exploring-ritchie.md`（engine cron 回调方案 — 已否决，方向反转）

---

## 1. 背景与动机

### 1.1 现状

`task_discovery` 模块已落地，流程为：

```
TaskDiscoveryLifecycle (asyncio.sleep 每日11:00)
  → 遍历 BotService.list_bots() 所有 bot
    → DiscoveryService.discover()
      → TaskReader.read_pending_tasks()        读 mock 数据
      → HttpSessionCreator.create_session()     backend → engine POST /api/sessions
      → NotifySenderPlugin.send()              发通知
```

**问题**：
1. `TaskDiscoveryLifecycle` 用 `asyncio.sleep()` while 循环实现定时，与系统 cron 基础设施完全解耦，不支持 cron 表达式/时区/故障恢复
2. `HttpSessionCreator.create_session()` 只创建空 session，**没有初始消息** — bot 不会在 session 中主动告诉用户发现了什么（engine 无 HTTP 发消息接口，需走 WebSocket）
3. 用户收到的通知只是「发现待执行任务」+ 任务详情文本，没有 session 交互引导

### 1.2 被否决的方案 (frolicking-exploring-ritchie.md)

原计划将方向反转为 **engine cron 触发 → engine 回调 backend `/pending` → engine 创建 session**。

否决原因：用户要求所有「engine 调用 backend」的方式改为 **backend 侧定时触发 → backend 调用 engine 接口创建 session**。核心原则是 backend 是编排者，engine 是执行者。

### 1.3 新需求

1. **定时触发**：在 backend 上用非 asyncio 的方式实现定时调度
2. **backend → engine**：backend 调用 engine 的接口创建 session
3. **session 中 bot 告知用户**：bot 在 session 中主动告诉用户「系统为你发现了一些可能有意义的事情，是否确认执行」
4. **session 创建后通知**：session 创建完成后发送通知给用户

---

## 2. 流程对比

### Before（现状）

```
asyncio.sleep 循环
  → backend 读 tasks
  → backend POST /api/sessions（空 session，无初始消息）
  → backend 发通知（纯文本，无 session 引导）
```

### Rejected Plan（engine cron 回调）

```
engine cron (kind=taskDiscovery) 触发
  → engine GET backend /pending 拉取任务
  → engine 创建 session
  → engine 在 session 中呈现任务
```

### New Design（本次设计）

```
backend 定时调度（APScheduler BackgroundScheduler，非 asyncio）
  → backend 读 tasks（按 bot_id/owner_id/dt 过滤）
  → backend build_discovery_prompt()（构造发现提示消息，内含完整任务数据）
  → backend CronRelayService.forward_request(POST /api/sessions)
    → engine 创建 session（extInfo 携带任务数据）
    → 返回 session_id
  → backend WebSocket 连 engine (/api/{engine}/ws)
    → connect 握手 (protocol v3)
    → chat.send(sessionKey=session_id, message=发现提示消息)
    → bot 在 session 中主动呈现发现任务并询问确认
    → 关闭 WebSocket
  → backend 构造 session_url
  → backend 发通知（NotifySenderPlugin，携带发现摘要 + session 链接）
  → 用户点击通知 → 打开 session → 看到 bot 已呈现的发现任务 → 确认/拒绝
```

**关键变化**：
- 调度从 asyncio.sleep → APScheduler BackgroundScheduler（线程级，非事件循环）
- 方向保持 backend → engine（不反转为 engine → backend）
- session 创建从 `HttpSessionCreator`（直连 HTTP）→ `CronRelayService.forward_request`（统一 relay 通道）
- **session 初始消息**：backend 创建 session 后通过 WebSocket `chat.send` 注入发现提示，bot 主动呈现 — 无需 engine 侧改动
- 通知从纯文本 → 发现摘要 + session deep_link

---

## 3. 引擎侧约束分析

### 3.1 Session 创建 API

Engine 暴露 `POST /api/sessions`（router.py:145），接收 `CreateSessionBody`：

```python
class CreateSessionBody(BaseModel):
    title: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[str] = None
    engine: Optional[str] = None
    uuid: Optional[str] = None
    extInfo: Optional[dict[str, Any]] = None    # 扩展信息
    payload: Optional[dict[str, Any]] = None    # 透传 payload
```

**约束**：`POST /api/sessions` 只创建 session 记录，**不发送初始聊天消息**。

### 3.2 Engine 消息发送接口

确认结论：
- `POST /api/sessions/{id}/update` — 更新 session 元数据，不发消息
- `GET /api/sessions/{id}/messages` — 读消息历史
- **WebSocket `/api/{engine}/ws`** — 唯一的消息发送通道
  - `connect` 帧握手（protocol v3，minProtocol=3, maxProtocol=3）
  - `chat.send` 帧（params: `sessionKey` + `message`）— 发送用户消息，引擎启动 agent turn
  - `chat.abort` — 中断 agent turn
- Engine 无 HTTP 发送消息接口

### 3.3 已验证的 WebSocket 协议参考

`test_create_session_e2e.py` 已完整实现 backend 侧 WebSocket 消息注入，可直接复用：

```python
# 1. HTTP 创建 session → 拿 session_id
resp = httpx.post(f"http://{target}/api/sessions", json={...})
session_id = resp.json()["data"]["id"]

# 2. WebSocket 连 engine
async with websockets.connect(f"ws://{target}/api/openclaw/ws") as ws:
    # 2a. connect 握手
    await ws.send(json.dumps({
        "type": "req", "id": "1", "method": "connect",
        "params": {"minProtocol": 3, "maxProtocol": 3,
                   "client": {"id": "task-discovery", "version": "1.0.0", ...},
                   "role": "operator"},
    }))
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    assert hello["ok"]

    # 2b. chat.send 发消息
    await ws.send(json.dumps({
        "type": "req", "id": "2", "method": "chat.send",
        "params": {"sessionKey": session_id, "message": "发现提示消息"},
    }))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    assert ack["ok"]

    # 2c. 可选：读事件等到 state=final，或直接断开
```

`sessionKey` 即 `POST /api/sessions` 返回的 `id`。

### 3.4 可选方案

| 方案 | 描述 | 优势 | 劣势 |
|---|---|---|---|
| **A. session + extInfo + 通知** | 创建 session（extInfo 携带任务数据），通知消息承载发现摘要，用户打开 session 后 bot 基于 extInfo 呈现 | 不需要 engine 改动；session_id 同步返回 | bot 不在 session 创建时主动发消息，需用户打开 session 后触发 |
| **A+WS. session + WebSocket chat.send** | 创建 session（HTTP）→ WebSocket 发送发现提示消息 → bot 立刻在 session 中呈现 | 不需要 engine 改动；bot 主动发消息；已有 e2e 参考代码 | backend 需 WebSocket 客户端逻辑 + `websockets` 依赖 |
| **B. cron agentTurn** | 创建一次性 cron job，触发执行，engine 创建隔离 session 并发送消息 | bot 主动在 session 中呈现 | cron run 是 fire-and-forget，不返回 session_id；需清理 cron job |
| **C. 新 engine 端点** | 新增 `POST /api/cron/task-discovery/initiate`，engine 内部 session.create + chat.send | 同步返回 session_id；bot 主动呈现；最干净 | 需要 engine 侧开发 |

### 3.5 设计决策

**Primary: 方案 A+WS（session + WebSocket chat.send 注入）** — 不依赖 engine 侧改动，bot 在 session 创建后立即主动呈现发现任务。

选择理由：
1. **零 engine 改动** — WebSocket 端点 (`/api/{engine}/ws`)、`connect` 握手、`chat.send` 处理器全部已就位
2. **已有参考实现** — `test_create_session_e2e.py` 完整实现了同样的 connect + chat.send 流程，协议格式可直接复用
3. **bot 立即呈现** — 消息在 session 创建后直接注入，用户打开 session 时 bot 回复已生成或正在生成
4. **engine target 已可解析** — `CronRelaySessionInitiator` 创建 session 后已知 engine target（relay 内部解析），可直接建立 WebSocket 连接

**A+WS 与方案 A 的区别**：方案 A 依赖 bot template 配置 `extInfo.source` 检测逻辑（需要模板配合），A+WS 通过 `chat.send` 直接把发现消息发给 bot，bot 自然处理（无需特殊模板逻辑）。

---

## 4. 详细设计

### 4.1 调度器 — `TaskDiscoveryScheduler`

替代 `TaskDiscoveryLifecycle`（asyncio.sleep），使用 **APScheduler BackgroundScheduler**（线程级调度，非 asyncio 事件循环）。

```python
# core/task/task_discovery/scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

class TaskDiscoveryScheduler(LifecycleBase):
    """后端定时任务发现调度器 — 线程级 cron 调度，非 asyncio。

    使用 APScheduler BackgroundScheduler：
    - 在独立线程中运行 tick 循环，不占用 asyncio 事件循环
    - 支持标准 cron 表达式 + 时区
    - DreamMode 开启 → 添加 job；关闭 → 移除 job
    """

    @inject
    def __init__(
        self,
        discovery_service: DiscoveryService,
    ) -> None:
        self._service = discovery_service
        self._scheduler: BackgroundScheduler | None = None

    async def startup(self) -> None:
        if os.environ.get("TASK_DISCOVERY_AUTO_START", "true").lower() != "true":
            return
        cron_expr = os.environ.get("TASK_DISCOVERY_CRON", "0 11 * * *")
        tz = os.environ.get("TASK_DISCOVERY_TIMEZONE", "Asia/Shanghai")
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._run_discovery,
            CronTrigger.from_crontab(cron_expr, timezone=tz),
            id="task_discovery_daily",
            replace_existing=True,
        )
        self._scheduler.start()

    async def shutdown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def _run_discovery(self):
        """在 scheduler 线程中执行；通过 asyncio.run 在事件循环上调用 async discover。"""
        import asyncio
        asyncio.run(self._service.discover_all_bots())

    def enable_for_bot(self, bot_id: str, owner_id: str) -> None:
        """DreamMode 开启 — 确保调度器运行。"""
        ...

    def disable_for_bot(self, bot_id: str, owner_id: str) -> None:
        """DreamMode 关闭 — 可选：移除特定 bot 的调度。"""
        ...
```

**配置项**：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TASK_DISCOVERY_AUTO_START` | `true` | 是否启动时自动开启调度 |
| `TASK_DISCOVERY_CRON` | `0 11 * * *` | cron 表达式（每日 11:00） |
| `TASK_DISCOVERY_TIMEZONE` | `Asia/Shanghai` | 调度时区 |
| `TASK_DISCOVERY_DATA_FILE` | `scripts/.dependencies/data/discovered_tasks.db` | 任务数据文件 |

**替代方案**（企业部署）：外部 scheduler（system cron / K8s CronJob）定时调用 `POST /api/public/task-discovery/scheduled-trigger`，backend 进程内不运行任何定时器。两种方式通过 `TASK_DISCOVERY_AUTO_START` 切换。

### 4.2 模型改造 — `models.py`

`DiscoveredTask` 增加字段，支持按 `(bot_id, owner_id, dt)` 维度查询：

```python
@dataclass(frozen=True)
class DiscoveredTask:
    task_id: str           # discover_task_{bot_id}_{owner_id}_{dt}
    bot_id: str            # NEW: 所属 bot
    owner_id: str          # NEW: bot owner
    dt: str                # NEW: 日期 YYYY-MM-DD
    project_name: str
    description: str
    business_scenario: str
    discovery_basis: str
    work_item_url: str | None = None
    priority: str = "medium"
    discovered_at: str | None = None
    status: str = "pending_confirmation"
```

**新增方法**：

```python
def to_session_ext_info(self) -> dict:
    """序列化为 engine session 的 extInfo — 包含完整任务数据供 bot 呈现。"""
    return {
        "source": "task_discovery",
        "task_id": self.task_id,
        "bot_id": self.bot_id,
        "owner_id": self.owner_id,
        "dt": self.dt,
        "project_name": self.project_name,
        "description": self.description,
        "business_scenario": self.business_scenario,
        "discovery_basis": self.discovery_basis,
        "work_item_url": self.work_item_url,
        "priority": self.priority,
    }

def to_discovery_prompt(self) -> str:
    """生成给 bot 的发现提示消息，嵌入完整任务数据。

    此消息作为通知 body 和 session extInfo 的人类可读版本，
    bot 可基于此内容在 session 中呈现并询问确认。
    """
    return (
        f"我为您发现了以下可能有意义的事情：\n\n"
        f"【{self.project_name}】\n"
        f"简介：{self.description}\n"
        f"业务场景：{self.business_scenario}\n"
        f"发现依据：{self.discovery_basis}\n\n"
        f"是否确认执行？请在下方回复确认或拒绝。"
    )

def to_notification_body(self, task_count: int) -> str:
    """生成通知消息体 — 包含发现摘要和确认引导。"""
    return (
        f"我为您发现了 {task_count} 件可能有意义的事情，"
        f"是否确认执行？\n\n"
        f"1. {self.project_name}：{self.description[:50]}...\n"
        f"\n请点击进入会话查看详情并确认。"
    )

def to_card_data(self) -> dict:
    """生成交互卡片数据（通用抽象，不绑定服务商）。"""
    return {
        "card_name": "为你发现以下任务",
        "workitem_name": self.project_name,
        "workitem_bg": self.description,
    }
```

### 4.3 DB Schema 改造 — `task_reader.py`

```sql
CREATE TABLE IF NOT EXISTS discovered_tasks (
    task_id            TEXT PRIMARY KEY,
    bot_id             TEXT NOT NULL,
    owner_id           TEXT NOT NULL,
    dt                 TEXT NOT NULL,        -- YYYY-MM-DD
    project_name       TEXT NOT NULL,
    description        TEXT,
    business_scenario  TEXT,
    discovery_basis    TEXT,
    work_item_url      TEXT,
    priority           TEXT DEFAULT 'medium',
    discovered_at      TEXT,
    status             TEXT DEFAULT 'pending_confirmation'
);
CREATE INDEX IF NOT EXISTS idx_discovered_tasks_bot_owner_dt
    ON discovered_tasks(bot_id, owner_id, dt);
```

**新增方法**：

```python
def read_pending_tasks_for_bot(
    self, bot_id: str, owner_id: str, dt: str,
) -> list[DiscoveredTask]:
    """返回指定 bot 当天的待确认任务。"""
    ...
```

### 4.4 Session 创建+消息注入 — `TaskDiscoverySessionInitiator`

替代 `HttpSessionCreator`。两步流程：(1) HTTP 创建 session → (2) WebSocket `chat.send` 注入发现提示消息。

```python
# core/task/task_discovery/session_initiator.py

import asyncio
import json

import httpx
import websockets

class SessionInitiator(Protocol):
    """Engine session 创建+消息注入接口。"""

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """为发现任务创建 engine session 并注入发现提示消息。"""
        ...


class CronRelaySessionInitiator:
    """通过 CronRelayService 创建 session + WebSocket 注入发现消息。

    流程：
      Step 1 — CronRelayService.forward_request(POST /api/sessions) 创建 session
      Step 2 — 解析 engine target 地址
      Step 3 — WebSocket 连 engine → connect 握手 → chat.send 发现提示消息
      Step 4 — 返回 session_id + session_url

    WebSocket 信任源：engine 的 /api/{engine}/ws 端点已在 engine API 中就位
    （app.py:334），chat.send 处理器在 ws_server.py:591。
    协议格式参考 test_create_session_e2e.py:128-179 同款实现。

    agent 回复策略：
      - 默认发完即走（fire message）— 不等 state=final
      - 用户打开 session 时 bot 回复可能已生成或正在生成
      - 可选 wait_for_reply=True 等待 final（阻塞，用于测试）
    """

    #: WebSocket 握手参数
    _WS_PROTOCOL = 3
    _WS_HANDSHAKE_TIMEOUT = 10.0
    _WS_SEND_TIMEOUT = 10.0
    _WS_REPLY_TIMEOUT = 60.0  # 仅 wait_for_reply=True 时使用

    def __init__(
        self,
        cron_relay: CronRelayServiceProtocol,
        frontend_url: str | None = None,
        wait_for_reply: bool = False,
    ):
        self._cron_relay = cron_relay
        self._frontend_url = frontend_url or os.environ.get(
            "FRONTEND_URL", "http://localhost:8000",
        )
        self._wait_for_reply = wait_for_reply

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:
        """创建 session + 注入发现消息。

        Steps:
            1. 构造 session body（title + extInfo）→ relay 创建 session
            2. 从 relay 响应中提取 session_id + engine target
            3. WebSocket 连 engine → chat.send 发现提示消息
            4. 返回 DiscoverySession
        """
        first_task = tasks[0]
        task_count = len(tasks)
        title = (
            f"为你发现了 {task_count} 件可能有意义的事情"
            if task_count > 1
            else first_task.project_name
        )

        # ── Step 1: 创建 session ──────────────────────────────
        body: dict[str, Any] = {
            "title": title,
            "user_id": owner_id,
            "agent_id": agent_id,
            "extInfo": {
                "source": "task_discovery",
                "task_count": task_count,
                "discovery_date": first_task.dt,
                "tasks": [t.to_session_ext_info() for t in tasks],
            },
        }
        if model:
            body["model"] = model

        result = await self._cron_relay.forward_request(
            bot_id=bot_id,
            user_id=owner_id,
            nick_name=owner_id,
            method="POST",
            path="/api/sessions",
            body=body,
        )

        if not result.get("success"):
            raise RuntimeError(
                f"engine session creation failed: {result.get('message', result)}"
            )

        session_data = result.get("data", {})
        session_id = (
            session_data.get("id")
            or session_data.get("session_id", "")
        )
        if not session_id:
            raise RuntimeError(f"engine response missing session id: {result}")

        # ── Step 2: 解析 engine target ───────────────────────
        engine_target = self._extract_engine_target(result, bot_id, owner_id)
        if not engine_target:
            logger.warning(
                "[task_discovery] no engine target for bot=%s, "
                "session created but message injection skipped",
                bot_id,
            )
        else:
            # ── Step 3: WebSocket 注入发现消息 ────────────────
            discovery_prompt = self._build_discovery_prompt(tasks)
            await self._ws_send_message(
                engine_target, session_id, discovery_prompt,
            )

        session_url = self._build_session_url(session_id, agent_id)

        return DiscoverySession(
            task_id=first_task.task_id,
            session_id=session_id,
            session_url=session_url,
        )

    # ── WebSocket 消息注入 ────────────────────────────────────

    def _extract_engine_target(
        self, relay_result: dict, bot_id: str, owner_id: str,
    ) -> str | None:
        """从 relay forward_request 响应中提取 engine target 地址。

        relay forward_request 的响应 data 中包含 bot_id/owner_id；
        engine target 需通过 backend connection API 查询。
        复用 HttpSessionCreator._resolve_engine_target 的逻辑（查
        binding_id → device connection → target）。

        在 singlebox mock transport 下，target 为 localhost:20010 等。
        """
        # relay 响应中已注入 bot_id
        bot_id = relay_result.get("data", {}).get("bot_id", bot_id)
        # 退路：直接从 forward_request 返回的 conn_info 中提取
        # （transport 层在转发时已知 target）
        # 最简方案：复用 backend API 查询
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                bot_resp = await cli.get(
                    f"{self._backend_url}/api/bots/{bot_id}",
                    params={"owner_id": owner_id},
                    headers={"x-user-id": owner_id},
                )
                bot_resp.raise_for_status()
                binding_id = (
                    bot_resp.json().get("data") or {}
                ).get("binding_id")
                if not binding_id:
                    return None
                conn_resp = await cli.get(
                    f"{self._backend_url}/api/v1/devices/{binding_id}/connection",
                    headers={"x-user-id": owner_id},
                )
                conn_resp.raise_for_status()
                return (
                    conn_resp.json().get("data") or {}
                ).get("target") or None
        except Exception:
            return None

    async def _ws_send_message(
        self, target: str, session_key: str, message: str,
    ) -> None:
        """WebSocket 连接 engine → 握手 → chat.send → 关闭。

        协议同 test_create_session_e2e.py:128-179：connect(proto3) → chat.send。
        默认发完即走（不等 final）；wait_for_reply=True 时等待 agent 回复。

        Raises:
            仅 log warning，不抛异常 — 消息注入失败不影响 session 创建结果。
            session 已创建，用户仍可通过通知链接打开 session 手动交互。
        """
        uri = f"ws://{target}/api/openclaw/ws"
        connect_params = {
            "minProtocol": self._WS_PROTOCOL,
            "maxProtocol": self._WS_PROTOCOL,
            "client": {
                "id": "task-discovery-initiator",
                "version": "1.0.0",
                "platform": "linux",
                "mode": "operator",
            },
            "role": "operator",
        }

        try:
            async with websockets.connect(
                uri, open_timeout=self._WS_HANDSHAKE_TIMEOUT,
            ) as ws:
                # 1) 握手
                await ws.send(json.dumps({
                    "type": "req", "id": "1",
                    "method": "connect",
                    "params": connect_params,
                }))
                hello = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=self._WS_HANDSHAKE_TIMEOUT,
                ))
                if not hello.get("ok"):
                    logger.warning(
                        "[task_discovery] WS handshake failed: %s",
                        json.dumps(hello)[:200],
                    )
                    return

                # 2) chat.send 发送发现提示消息
                await ws.send(json.dumps({
                    "type": "req", "id": "2",
                    "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": message,
                    },
                }))
                ack = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=self._WS_SEND_TIMEOUT,
                ))
                if not ack.get("ok"):
                    logger.warning(
                        "[task_discovery] WS chat.send rejected: %s",
                        json.dumps(ack)[:200],
                    )
                    return

                logger.info(
                    "[task_discovery] WS message injected: session=%s",
                    session_key,
                )

                # 3) 可选：等待 agent 回复
                if self._wait_for_reply:
                    await self._wait_for_final(ws, session_key)

        except Exception as exc:
            logger.warning(
                "[task_discovery] WS message injection failed for "
                "session %s: %s (session already created, user can "
                "interact manually)",
                session_key, exc,
            )

    async def _wait_for_final(self, ws, session_key: str) -> None:
        """等待 chat agent 输出 state=final 事件。"""
        try:
            while True:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=self._WS_REPLY_TIMEOUT,
                )
                data = json.loads(raw)
                if data.get("type") != "event":
                    continue
                if data.get("event") == "chat":
                    state = (data.get("payload") or {}).get("state")
                    if state in ("final", "error"):
                        break
        except asyncio.TimeoutError:
            logger.warning(
                "[task_discovery] WS reply timeout for session %s",
                session_key,
            )

    def _build_discovery_prompt(self, tasks: list[DiscoveredTask]) -> str:
        """构造发现提示消息 — 作为 chat.send 的 message 发送给 bot。

        此消息发送到 session 后，bot 会基于消息内容主动呈现发现任务
        并询问用户确认。
        """
        lines = ["我为您发现了以下可能有意义的事情，请确认是否执行：\n"]
        for i, task in enumerate(tasks, 1):
            lines.append(f"{i}. 【{task.project_name}】")
            lines.append(f"   简介：{task.description}")
            lines.append(f"   业务场景：{task.business_scenario}")
            if task.work_item_url:
                lines.append(f"   关联需求：{task.work_item_url}")
            lines.append("")
        lines.append("请向用户展示以上任务，并询问是否确认执行。")
        return "\n".join(lines)

    def _build_session_url(self, session_id: str, agent_id: str) -> str:
        """构建前端 workbench session URL。"""
        base = self._frontend_url.rstrip("/")
        return (
            f"{base}/bcn/chat/session"
            f"?bot_uuid={agent_id}&id={agent_id}&session={session_id}"
        )
```

**agent 回复策略**：
- **默认 `wait_for_reply=False`**（发完即走）：发送 `chat.send` 并确认 engine 接受后关闭 WebSocket。agent 在 engine 侧异步处理，用户打开 session 时回复可能已生成或正在生成。
- **测试中 `wait_for_reply=True`**：等待 `state=final` 事件，确认 bot 回复完整后再返回。用于 e2e 测试验证。

**消息注入失败容忍**：WebSocket 发送失败仅 log warning，不抛异常。session 已成功创建，用户仍可通过通知中的 `deep_link` 打开 session 手动交互。`DiscoveryResult.success` 仍为 `True`。

**与 `HttpSessionCreator` 的对比**：

| 维度 | `HttpSessionCreator` | `CronRelaySessionInitiator` |
|---|---|---|
| session 创建 | 自己查 binding_id → device connection → 直连 engine POST /api/sessions | `CronRelayService.forward_request` 统一通道 |
| 初始消息 | 无 | WebSocket `chat.send` 注入发现提示 |
| engine target | 自己解析（2次 HTTP 调用） | relay 内部解析 + WS target 复用 |
| 错误处理 | session 创建失败即中断 | session 创建失败中断；WS 注入失败降级（session 仍可用） |

### 4.5 DiscoveryService 改造

移除 `SessionCreator` 依赖，改用 `SessionInitiator`。流程精简为：读任务 → 创建 session → 发通知。

```python
class DiscoveryService:
    """任务主动发现编排服务。

    将 TaskReader、SessionInitiator 和 NotifySenderPlugin 编排在一起，
    提供 "发现 → 创建 session → 通知" 流程。
    """

    def __init__(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
    ):
        self._reader = reader
        self._session_initiator = session_initiator
        self._notify_sender = notify_sender

    async def discover_all_bots(self) -> list[DiscoveryResult]:
        """遍历所有 bot，为每个 bot 执行发现流程。

        由 scheduler 线程调用（通过 asyncio.run）。
        """
        # 1. 从 BotService 查出所有 bot
        # 2. 对每个 bot，read_pending_tasks_for_bot(bot_id, owner_id, today)
        # 3. 有待确认任务 → initiate_session → send_notification
        ...

    async def discover(
        self,
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> list[DiscoveryResult]:
        """为单个 bot 执行发现流程（手动触发）。

        1. 读取该 bot 当天的待确认任务
        2. 为所有任务创建一个 engine session（extInfo 携带所有任务数据）
        3. 发送通知（发现摘要 + session 链接）
        """
        dt = datetime.now().strftime("%Y-%m-%d")
        tasks = self._reader.read_pending_tasks_for_bot(bot_id, owner_id, dt)
        if not tasks:
            return []

        results: list[DiscoveryResult] = []
        for task in tasks:
            result = await self._discover_single(
                task,
                all_tasks=tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )
            results.append(result)
        return results

    async def _discover_single(
        self,
        task: DiscoveredTask,
        *,
        all_tasks: list[DiscoveredTask],
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None,
    ) -> DiscoveryResult:
        """处理单个任务：创建 session → 发通知。"""
        try:
            session = await self._session_initiator.initiate_session(
                all_tasks,
                bot_id=bot_id,
                owner_id=owner_id,
                agent_id=agent_id,
                model=model,
            )

            notification_sent = self._send_notification(
                task, owner_id, session.session_url, len(all_tasks),
            )

            return DiscoveryResult(
                task=task,
                session=session,
                notification_sent=notification_sent,
            )
        except Exception as exc:
            logger.error(
                "[task_discovery] failed for task %s: %s",
                task.task_id, exc,
            )
            return DiscoveryResult(task=task, error=str(exc))

    def _send_notification(
        self,
        task: DiscoveredTask,
        user_id: str,
        session_url: str,
        task_count: int,
    ) -> bool:
        """通过 NotifySenderPlugin 投递通知。

        通知 body 是 bot 的「告知」：发现摘要 + 确认引导。
        deep_link 指向 session，用户点击后进入 session 确认。
        extra 携带通用交互卡片参数（不绑定具体服务商）。
        """
        import json
        message = NotifyMessage(
            title="发现待确认任务",
            body=task.to_notification_body(task_count),
            recipient=user_id,
            deep_link=session_url,
            extra={
                "channel": "tc_card",
                "card_template_id": os.environ.get(
                    "TASK_DISCOVERY_CARD_TEMPLATE_ID", ""
                ),
                "card_biz_id": f"discover_things_{task.task_id}",
                "card_data": json.dumps(task.to_card_data()),
                "session_url": session_url,
            },
        )
        msg_id = self._notify_sender.send(message)
        return msg_id is not None
```

### 4.6 内部端点 — `router.py`

```python
router = APIRouter(
    prefix="/api/public/task-discovery",
    tags=["task-discovery"],
)

# ── 定时调度触发（外部 scheduler 调用）──────────────────────

@router.post("/scheduled-trigger")
async def scheduled_trigger(
    service: DiscoveryService = Injected(DiscoveryService),
) -> dict:
    """外部 scheduler 定时触发的入口端点。

    供 system cron / K8s CronJob 等外部调度器调用。
    backend 进程内不运行任何 asyncio 定时器。
    """
    results = await service.discover_all_bots()
    return {
        "success": True,
        "total_discovered": sum(1 for r in results if r.success),
        "results": [...],
    }

# ── 手动触发 ───────────────────────────────────────────────

@router.post("/discover")
async def discover_tasks(
    bot_id: str = Query(...),
    owner_id: str = Query(...),
    agent_id: str = Query(..., description="Bot/Agent ID"),
    model: str | None = Query(None),
    service: DiscoveryService = Injected(DiscoveryService),
) -> dict:
    """手动触发任务发现 — 直接调用 discover，不经过 cron round-trip。"""
    results = await service.discover(
        bot_id=bot_id, owner_id=owner_id, agent_id=agent_id, model=model,
    )
    return {
        "success": True,
        "discovered": len(results),
        "tasks": [...],
    }

# ── DreamMode 开关 ──────────────────────────────────────────

@router.post("/dream-mode")
async def toggle_dream_mode(
    bot_id: str = Query(...),
    owner_id: str = Query(...),
    enabled: bool = Query(True),
    scheduler: TaskDiscoveryScheduler = Injected(TaskDiscoveryScheduler),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> dict:
    """开启/关闭任务发现 DreamMode。

    开启 → 确保 scheduler 运行（添加 cron job）
    关闭 → 移除 cron job
    权限：只能为自己的 bot 操作（通过 BotService.get_bot 校验）
    """
    # 权限校验
    bot = bot_service.get_bot(bot_id, owner_id)
    if not bot:
        return {"success": False, "message": "bot not found"}

    if enabled:
        scheduler.enable_for_bot(bot_id, owner_id)
    else:
        scheduler.disable_for_bot(bot_id, owner_id)

    return {"success": True, "enabled": enabled, "bot_id": bot_id}

# ── 状态查询 ───────────────────────────────────────────────

@router.get("/status")
async def get_status(
    bot_id: str = Query(None),
    owner_id: str = Query(None),
) -> dict:
    """查看任务发现状态（按 bot_id/owner_id/dt 过滤或全量）。"""
    ...
```

### 4.7 协议层改造 — `protocols.py`

```python
@runtime_checkable
class BotServiceProtocol(Protocol):
    """Bot 服务接口。"""
    def list_bots(self, *args: Any, **kwargs: Any) -> Any: ...
    def get_bot(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class CronRelayServiceProtocol(Protocol):
    """Cron relay 服务接口 — 用于 forward_request 创建 session。"""
    async def forward_request(self, *args: Any, **kwargs: Any) -> Any: ...
    async def list_all_crons(self, *args: Any, **kwargs: Any) -> Any: ...
```

### 4.8 DI 模块改造 — `task_discovery_module.py`

```python
class TaskDiscoveryModule(Module):
    def configure(self, binder: Binder) -> None:
        # 替换 TaskDiscoveryLifecycle → TaskDiscoveryScheduler
        binder.bind(TaskDiscoveryScheduler, to=TaskDiscoveryScheduler, scope=singleton)
        binder.bind(DiscoveryService, to=DiscoveryService, scope=singleton)

    @singleton
    @provider
    @inject
    def _provide_discovery_service(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
    ) -> DiscoveryService:
        return DiscoveryService(
            reader=reader,
            session_initiator=session_initiator,
            notify_sender=notify_sender,
        )

    @singleton
    @provider
    @inject
    def _provide_session_initiator(
        self,
        cron_relay: _ApiCronRelayServiceProtocol,
    ) -> SessionInitiator:
        return CronRelaySessionInitiator(cron_relay=cron_relay)

    @singleton
    @provider
    @inject
    def _provide_task_reader(self) -> TaskReader:
        return SqliteTaskReader(_resolve_db_path())

    @singleton
    @provider
    @inject
    def _bridge_bot_service_protocol(
        self,
        bot_service: _ApiBotServiceProtocol,
    ) -> _TaskDiscoveryBotServiceProtocol:
        return bot_service

    @singleton
    @provider
    @inject
    def _bridge_cron_relay_protocol(
        self,
        cron_relay: _ApiCronRelayServiceProtocol,
    ) -> _TaskDiscoveryCronRelayProtocol:
        return cron_relay
```

### 4.9 文件删除

| 文件 | 原因 |
|---|---|
| `lifecycle.py` | asyncio.sleep 调度由 `scheduler.py` (APScheduler) 替代 |
| `session_creator.py` | `HttpSessionCreator` 由 `session_initiator.py` (`CronRelaySessionInitiator`) 替代 |

---

## 5. 通知接口设计

### 5.1 通知消息结构

```python
NotifyMessage(
    title="发现待确认任务",
    body="我为您发现了 3 件可能有意义的事情，是否确认执行？\n\n"
         "1. 卡片内容视觉合规自动化审核Skill：将用户高频...\n"
         "\n请点击进入会话查看详情并确认。",
    recipient=owner_id,
    deep_link=session_url,       # 用户点击打开 session
    extra={
        "channel": "tc_card",
        "card_template_id": os.environ.get("TASK_DISCOVERY_CARD_TEMPLATE_ID", ""),
        "card_biz_id": f"discover_things_{task_id}",
        "card_data": json.dumps({
            "card_name": "为你发现以下任务",
            "session_url": session_url,
            "workitem_name": project_name,
            "workitem_bg": description,
        }),
        "session_url": session_url,
    },
)
```

### 5.2 开源约束

- 代码中**禁止出现任何具体服务商品牌名**
- 环境变量使用通用名：`TASK_DISCOVERY_CARD_TEMPLATE_ID`（不含服务商前缀）
- `CommunityNotifySender.send()` 对 `extra` 中的卡片参数仅日志输出
- 企业部署时在环境变量中配置实际 `card_template_id` 和对应发送通道

### 5.3 与 cron notify 的区别

Engine cron 的 `notify` 配置（`CronNotifyConfig`）是 engine 侧的通知能力，在 cron 执行完成后由 engine 发送。**本设计不在 engine 侧使用 cron notify**，通知完全由 backend 的 `NotifySenderPlugin` 在 session 创建后发送，保持 backend 为通知编排的唯一控制点。

---

## 6. Session 中 Bot 呈现机制

### 6.1 主方案（A+WS）：WebSocket chat.send 注入

```
Backend 创建 session (HTTP POST /api/sessions)
  → session_id 返回
  → extInfo = {source: "task_discovery", tasks: [...]}

Backend WebSocket 连 engine
  → connect 握手 (protocol v3)
  → chat.send(sessionKey=session_id, message=发现提示消息)
  → bot 收到消息 → 启动 agent turn → 呈现发现任务并询问确认
  → backend 关闭 WebSocket（发完即走 / 或等 final）

用户打开 session
  → 看到 bot 已发送的发现提示 + bot 的回复（或回复正在生成）
  → 用户回复确认/拒绝
```

**发现提示消息内容**（`_build_discovery_prompt()` 生成）：

```
我为您发现了以下可能有意义的事情，请确认是否执行：

1. 【卡片内容视觉合规自动化审核Skill】
   简介：将用户高频重复的卡片文案与截图合规审核流程沉淀为标准化Skill...
   业务场景：产品/运营团队对推送卡片、弹窗文案进行批量合规性审查与质检的场景。
   关联需求：https://dima.alipay.com/requirement/123456

请向用户展示以上任务，并询问是否确认执行。
```

**与方案 A（纯 extInfo）的关键区别**：
- 方案 A：bot 需要模板配置检测 `extInfo.source`，用户打开 session 后才触发呈现
- **A+WS**：`chat.send` 直接把发现消息发给 bot，bot 自然处理消息并回复 — 无需特殊模板逻辑

### 6.2 消息注入失败降级

WebSocket 发送可能失败（engine 离线、网络超时等）。降级策略：

| 失败点 | 影响 | 降级行为 |
|---|---|---|
| session 创建失败 | 无法继续 | 抛异常，`DiscoveryResult.error` 记录 |
| engine target 解析失败 | 无法 WS 连接 | session 已创建；log warning；通知正常发送；用户打开 session 后手动交互 |
| WS 握手失败 | 消息未注入 | session 已创建；log warning；通知正常发送 |
| chat.send 被拒绝 | 消息未注入 | session 已创建；log warning；通知正常发送 |

**核心原则**：session 创建成功 = 主流程成功。消息注入是增强体验，失败不影响 session 可用性。用户通过通知 `deep_link` 打开 session 后，`extInfo` 仍携带完整任务数据可供 bot 参考。

### 6.3 后续增强（方案 C）：新 engine 端点

如后续需要更紧密的同步语义（创建 session + 发消息 + 等 bot 回复 全在一个原子操作中），可新增 engine API `POST /api/cron/task-discovery/initiate`：

```python
class TaskDiscoveryInitiateRequest(BaseModel):
    user_id: str
    agent_id: str
    message: str          # 发现提示消息（含完整任务数据）
    ext_info: dict        # 结构化任务数据
    model: str | None = None
```

Engine 内部 `session.create()` + `chat.send()`，同步返回 `{session_id, reply}`。当前 A+WS 方案已满足需求，方案 C 作为后续优化。

---

## 7. DreamMode 机制

DreamMode 是任务发现的启停开关，控制 backend 侧的调度行为。

| 状态 | 行为 |
|---|---|
| DreamMode ON | `TaskDiscoveryScheduler` 运行，按 cron 表达式定时触发 `discover_all_bots()` |
| DreamMode OFF | `TaskDiscoveryScheduler` 停止调度（移除 cron job），不再自动发现 |

**实现**：
- `POST /api/public/task-discovery/dream-mode?enabled=true/false`
- `scheduler.enable_for_bot()` / `scheduler.disable_for_bot()`
- 全局开关：`TASK_DISCOVERY_AUTO_START` 环境变量
- 权限：通过 `BotService.get_bot(bot_id, owner_id)` 校验 ownership

**与原计划的区别**：原计划 DreamMode 控制 engine 侧的 cron job 创建/删除。新设计 DreamMode 控制 backend 侧的 APScheduler job 启停，engine 侧不创建任何 cron job。

---

## 8. 架构约束遵循

| 约束 | 遵循情况 |
|---|---|
| Core 层 transport-agnostic | `DiscoveryService` 不 import transport；`SessionInitiator` 通过 Protocol 隔离 |
| DI composition root | `CronRelayServiceProtocol`、`BotServiceProtocol`、`NotifySenderPlugin` 由 DI 注入 |
| Thin adapter | `router.py` 只转协议，不持领域策略 |
| No hardcoded URLs | engine 地址由 relay 内部解析；frontend URL 从环境变量读取 |
| Plugin Protocol | `NotifySenderPlugin` Protocol 从不抛异常 |
| Contract authority | session 创建经 `CronRelayService.forward_request` 统一通道 |

---

## 9. 修改文件清单

| 文件 | 操作 | 关键变更 |
|---|---|---|
| `core/task/task_discovery/models.py` | 修改 | 增加 bot_id/owner_id/dt；新增 to_discovery_prompt/to_notification_body/to_card_data |
| `core/task/task_discovery/task_reader.py` | 修改 | 更新 DDL+索引；新增 read_pending_tasks_for_bot()；更新 init_discovered_tasks_db() |
| `core/task/task_discovery/session_initiator.py` | **新增** | SessionInitiator Protocol + CronRelaySessionInitiator（HTTP 创建 + WS chat.send 注入） |
| `core/task/task_discovery/discovery_service.py` | 修改 | 移除 SessionCreator 依赖；改用 SessionInitiator；新增 discover_all_bots()；改造通知消息 |
| `core/task/task_discovery/scheduler.py` | **新增** | TaskDiscoveryScheduler（APScheduler BackgroundScheduler） |
| `core/task/task_discovery/lifecycle.py` | **删除** | asyncio 调度由 scheduler.py 替代 |
| `core/task/task_discovery/session_creator.py` | **删除** | HttpSessionCreator 由 session_initiator.py 替代 |
| `core/task/task_discovery/protocols.py` | 修改 | 新增 CronRelayServiceProtocol 引用 |
| `core/task/task_discovery/__init__.py` | 修改 | 更新模块文档 |
| `adapters/http/task_discovery/router.py` | 修改 | 新增 /scheduled-trigger /dream-mode；/discover 改为直接调用 |
| `di/modules/task_discovery_module.py` | 修改 | 移除 Lifecycle 绑定；新增 Scheduler/Service/Initiator provider |
| `tests/.../test_task_discovery_unit.py` | 修改 | 适配新接口 |
| `tests/.../test_task_discovery_router.py` | 修改 | 适配新端点 |
| `tests/.../singlebox_e2e/test_task_discovery_e2e.py` | 修改 | 新 mock 数据格式；session 创建+WS 注入；通知验证 |
| `pyproject.toml` | 修改 | 新增依赖 `apscheduler`、`websockets` |

---

## 10. 实施顺序

1. **依赖添加**：`pyproject.toml` 添加 `apscheduler`、`websockets`
2. **模型+数据层**：`models.py` 新字段 → `task_reader.py` 新 DDL+查询 → 单测
3. **Session Initiator**：`session_initiator.py` 新增（HTTP 创建 + WS 注入 + engine target 解析）→ 单测（mock relay + mock WS）
4. **DiscoveryService 改造**：移除 SessionCreator，改用 SessionInitiator → 单测
5. **Scheduler**：`scheduler.py` 新增 → 单测
6. **Router 改造**：新增 /scheduled-trigger /dream-mode；/discover 直接调用 → 端点测试
7. **通知增强**：通知消息改造（discovery body + deep_link + extra 卡片） → 单测
8. **清理**：删除 lifecycle.py + session_creator.py → 更新 DI 模块
9. **E2E 测试**：`singlebox_e2e/test_task_discovery_e2e.py` 完整流程验证（含 WS 注入）

---

## 11. 验证策略

| 层级 | 测试内容 | 方式 |
|---|---|---|
| Unit | `SqliteTaskReader.read_pending_tasks_for_bot()` 按 (bot_id, owner_id, dt) 过滤 | 内存 SQLite |
| Unit | `CronRelaySessionInitiator.initiate_session()` HTTP 创建 session | Mock `CronRelayServiceProtocol` |
| Unit | `CronRelaySessionInitiator._ws_send_message()` WebSocket 握手+chat.send | Mock `websockets.connect` |
| Unit | `CronRelaySessionInitiator` engine target 解析 | Mock `httpx.AsyncClient` |
| Unit | `CronRelaySessionInitiator` WS 注入失败降级（session 仍成功） | Mock `websockets.connect` raise |
| Unit | `DiscoveryService.discover()` 编排：读任务 → 创建 session+注入 → 发通知 | Mock reader + initiator + notify |
| Unit | `TaskDiscoveryScheduler` 启停 + cron 表达式 | Mock scheduler |
| Unit | `DiscoveredTask.to_discovery_prompt()` / `to_notification_body()` | 纯 dataclass 断言 |
| Unit | `NotifyMessage.extra` 包含 card_template_id/card_biz_id/card_data | 断言 extra dict |
| Endpoint | `POST /scheduled-trigger` 触发发现 | TestClient |
| Endpoint | `POST /dream-mode` 启停调度 | TestClient |
| Endpoint | `POST /discover` 直接触发 | TestClient |
| E2E | scheduler 触发 → session 创建 → WS chat.send 注入 → 通知组装 全链路 | gated singlebox |

---

## 12. 风险与缓解

| 风险 | 级别 | 缓解 |
|---|---|---|
| `forward_request(POST /api/sessions)` 未被 cron relay 验证过 | 中 | cron relay `forward_request` 是通用转发器；`_forward_single_stage_request` 已处理 POST + body；测试中验证 |
| APScheduler BackgroundScheduler 与 asyncio 事件循环的线程安全 | 中 | scheduler 线程通过 `asyncio.run()` 调用 async discover，不共享事件循环；DiscoveryService 内部无共享可变状态 |
| WebSocket 连接在 scheduler 线程中使用 `asyncio.run()` | 中 | `_ws_send_message` 是 async，在 `discover()` 的事件循环内执行；`websockets` 库原生支持 asyncio |
| engine target 解析多次 HTTP 调用 | 低 | 复用 `HttpSessionCreator._resolve_engine_target` 的验证逻辑；e2e 测试已证明可达；后续可优化为 relay 直接返回 target |
| WS 消息注入失败（engine 离线/网络问题） | 低 | 降级策略：session 已创建，log warning；用户通过通知链接手动交互；不影响主流程 success |
| 通知 `extra` 中的卡片参数在 community 实现中被忽略 | 低 | 设计如此 — community log-only，企业部署配置实际发送通道 |
| `forward_request` 在 singlebox mock transport 下行为 | 低 | InMemoryDeviceAdapterTransport 透传请求；e2e 测试覆盖 |

---

## 13. 兼容性

- **SessionInitiator Protocol**：新接口，替换 SessionCreator
- **TaskReader Protocol**：`read_pending_tasks_for_bot()` 新增方法，`read_pending_tasks()` / `read_discovered_tasks()` 保留
- **DiscoveredTask**：新增字段 bot_id/owner_id/dt，旧字段不变
- **DiscoveryResult**：结构不变
- **HTTP API**：`POST /discover` 响应扩展（新增 session_url/notification_sent）；新增 `/scheduled-trigger` `/dream-mode`
- **DI**：`TaskDiscoveryModule` 移除 Lifecycle 绑定，新增 Scheduler/Service/Initiator
- **环境变量**：`TASK_DISCOVERY_SCHEDULE_HOUR/MINUTE` → `TASK_DISCOVERY_CRON`（cron 表达式）
- **依赖**：新增 `apscheduler`（定时调度）、`websockets`（WS 消息注入），均为纯 Python 库

---

## 14. 领域边界与所有权

| 模块 | 拥有 |
|---|---|
| task_discovery | 发现编排流程、DiscoveryService、TaskDiscoveryScheduler、SessionInitiator、DiscoveryResult |
| plugin_api | NotifySenderPlugin Protocol、NotifyMessage 模型 |
| cron (core) | CronRelayService、forward_request 通道 |
| engine | Session 创建 API、Bot template 行为 |

**边界**：task_discovery 通过 `CronRelayServiceProtocol` 调用 relay 通道创建 session，通过 `NotifySenderPlugin` Protocol 投递通知，不跨边界持有实现。