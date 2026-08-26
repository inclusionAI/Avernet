# 技术设计 — DiscoveredTask 对齐 TaskSpec + 发现流程双通知通道

## 1. 改动范围

```
源码：
├── core/task/task_discovery/
│   ├── models.py             (修改)  DiscoveredTask 字段 + to_discovery_prompt 4 维度
│   ├── task_reader.py        (修改)  DDL/SELECT/_row_to_task/init DROP+CREATE 重命名
│   ├── session_initiator.py  (修改)  URL helper 环境感知 + 4 维度 prompt + DreamMode-任务发现 标题
│   ├── session_creator.py    (修改)  body["title"] = task.title
│   └── discovery_service.py  (修改)  __init__ +work_order_service；_send_notification(session)；
│                                    新增 _send_work_order_event；双通道并发
├── core/work_orders/models.py     (修改)  WorkOrderEventType.TASK_DISCOVERED + NOTICE 注册
├── di/modules/infrastructure/community/notify.py  (修改)  条件绑定 DingTalkNotifySender
├── di/modules/task_discovery_module.py            (修改)  注入 work_order_service
├── plugins/community/notify_sender.py             (修改)  新增 DingTalkNotifySender
├── adapters/http/task/router.py                   (修改)  /discover / /status 字段名兼容
└── docs/task-discovery-api.zh-CN.md               (修改)  删除 work_item_url 行

测试：
├── core/task/test_task_discovery_unit.py          (修改)  mock dict 新键 + DB 往返断言
├── endpoints/test_task_discovery_router.py        (修改)  _status_task + 显式 DiscoveredTask 映射
└── core/task/singlebox_e2e/
    ├── test_cron_scheduler_e2e.py                  (修改)  mock 新键 + 删除 TestDingTalkCardE2E
    ├── test_cron_timed_fire_e2e.py                  (修改)  mock 新键 + 删除 Phase6 钉钉
    ├── test_discover_and_notify_e2e.py              (删除)  独立钉钉 standalone 脚本，已下沉
    ├── test_task_discovery_e2e.py                   (修改)  mock 新键 + status 断言
    └── test_cron_timed_fire_workorder_e2e.py         (新增)  workorder NOTICE e2e
```

## 2. DiscoveredTask 字段对齐 TaskSpec

### Before

```python
@dataclass
class DiscoveredTask:
    task_id: str
    bot_id: str
    owner_id: str
    dt: str
    project_name: str           # ~ TaskSpec.metadata.title
    description: str           # ~ TaskSpec.metadata.instruction
    business_scenario: str     # ~ TaskSpec.context.background
    discovery_basis: str
    work_item_url: Optional[str] = None
    priority: str = "medium"
    discovered_at: Optional[str] = None
    status: str = "pending_confirmation"
```

### After

```python
@dataclass
class DiscoveredTask:
    task_id: str
    bot_id: str
    owner_id: str
    dt: str
    title: str                  # 对齐 TaskSpec.metadata.title
    instruction: str            # 对齐 TaskSpec.metadata.instruction
    background: str            # 对齐 TaskSpec.context.background
    discovery_basis: str
    priority: str = "medium"
    discovered_at: Optional[str] = None
    status: str = "pending_confirmation"
    # 执行层衔接字段（不落 discovered_tasks.db；确认后用于 to_task_info_request）
    objective: str = ""                                              # ~ TaskSpec.goal.objective
    acceptances: list[dict] = field(default_factory=list)           # ~ TaskSpec.goal.acceptances
```

- `to_session_ext_info` 仍输出旧键名（`project_name` / `description` / `business_scenario`），值取自 `title` / `instruction` / `background` → engine 兼容不破。

## 3. discovered_tasks.db schema 同步

### DDL

```sql
CREATE TABLE IF NOT EXISTS discovered_tasks (
    task_id            TEXT PRIMARY KEY,
    bot_id             TEXT NOT NULL,
    owner_id           TEXT NOT NULL,
    dt                 TEXT NOT NULL,
    title              TEXT NOT NULL,    -- was: project_name
    instruction        TEXT,             -- was: description
    background         TEXT,             -- was: business_scenario
    discovery_basis    TEXT,
    priority           TEXT DEFAULT 'medium',
    discovered_at      TEXT,
    status             TEXT DEFAULT 'pending_confirmation',
    objective          TEXT,             -- 新增
    acceptances        TEXT               -- 新增（JSON 字符串）
);
CREATE INDEX IF NOT EXISTS idx_discovered_tasks_bot_owner_dt
    ON discovered_tasks(bot_id, owner_id, dt);
```

### init_discovered_tasks_db — DROP+CREATE

```python
def init_discovered_tasks_db(db_path: str | Path, tasks: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # DROP+CREATE：每次初始化使用最新 schema
        # （CREATE TABLE IF NOT EXISTS 不会给已存在的旧库加 objective/acceptances）
        conn.executescript("DROP TABLE IF EXISTS discovered_tasks;\n" + _CREATE_TABLE_SQL)
        conn.executemany(
            "INSERT INTO discovered_tasks "
            "(task_id, bot_id, owner_id, dt, title, instruction, "
            " background, discovery_basis, priority, "
            " discovered_at, status, objective, acceptances) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            [
                (
                    t["task_id"], t.get("bot_id", ""), t.get("owner_id", ""),
                    t.get("dt", ""),
                    t.get("title", ""), t.get("instruction", ""),
                    t.get("background", ""),
                    t.get("discovery_basis", ""),
                    t.get("priority", "medium"),
                    t.get("discovered_at"),
                    t.get("status", "pending_confirmation"),
                    t.get("objective", ""),
                    json.dumps(t.get("acceptances", []), ensure_ascii=False),
                )
                for t in tasks
            ],
        )
        conn.commit()
```

### _row_to_task — 防御性读 objective/acceptances

```python
def _row_to_task(row: sqlite3.Row) -> DiscoveredTask:
    keys = set(row.keys())
    raw_acceptances = row["acceptances"] if "acceptances" in keys else None
    try:
        acceptances = json.loads(raw_acceptances) if raw_acceptances else []
    except (TypeError, ValueError):
        acceptances = []
    return DiscoveredTask(
        task_id=row["task_id"], bot_id=row["bot_id"], owner_id=row["owner_id"], dt=row["dt"],
        title=row["title"], instruction=row["instruction"] or "", background=row["background"] or "",
        discovery_basis=row["discovery_basis"] or "",
        priority=row["priority"] or "medium",
        discovered_at=row["discovered_at"],
        status=row["status"] or "pending_confirmation",
        objective=row["objective"] if "objective" in keys and row["objective"] else "",
        acceptances=acceptances if isinstance(acceptances, list) else [],
    )
```

- `_SELECT_ALL_SQL` / `_SELECT_PENDING_FOR_BOT_SQL` 改为 `SELECT *`，由 `_row_to_task` 处理列缺失场景，旧库降级读取。

## 4. 发现提示词 — 4 维度（对齐 TaskSpec Goal/Context）

### 单任务 `to_discovery_prompt`

```python
def to_discovery_prompt(self) -> str:
    lines = [
        f"/task 我为您发现了以下可能有意义的事情：\n",
        f"【{self.title}】",
        f"目标：{self.objective or self.title}",
        f"预期交付物：{self.instruction}",
    ]
    if self.acceptances:
        lines.append("验收标准：")
        for a in self.acceptances:
            lines.append(f"  - [{a.get('id', '')}] {a.get('description', '')}")
    else:
        lines.append("验收标准：（确认时可由你补充）")
    lines.append(f"约束：{self.background}")
    lines.append("\n是否确认执行？请在下方回复确认或拒绝。")
    return "\n".join(lines)
```

### 多任务 `_build_discovery_prompt`

```python
def _build_discovery_prompt(self, tasks: list[DiscoveredTask]) -> str:
    lines = ["/task 我为您发现了以下可能有意义的事情，请确认是否执行：\n"]
    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. 【{task.title}】")
        lines.append(f"   目标：{task.objective or task.title}")
        lines.append(f"   预期交付物：{task.instruction}")
        if task.acceptances:
            lines.append("   验收标准：")
            for a in task.acceptances:
                lines.append(f"     - [{a.get('id', '')}] {a.get('description', '')}")
        else:
            lines.append("   验收标准：（确认时可由你补充）")
        lines.append(f"   约束：{task.background}")
        lines.append("")
    lines.append("请向用户展示以上任务，并询问是否确认执行。")
    return "\n".join(lines)
```

### session title

```python
title = (
    f"[DreamMode-任务发现] 发现 {task_count} 件可能有意义的事情"
    if task_count > 1
    else f"[DreamMode-任务发现] {first_task.title}"
)
```

- 仍按 `2026-08-24-discover-session-url-fix-and-dingtalk-notify-enhance` 的 Step 2.5 单独 update title（engine `POST /api/sessions/{id}/update?title=xxx`）；update 失败不阻断。

## 5. session URL / backend URL 环境感知解析

```python
_DEFAULT_BACKEND_URL = "http://localhost:8888"
_DEFAULT_FRONTEND_URL = "http://localhost:8000"
_SINGLEBOX_HOST = "agentclaw-local.stable.alipay.net"


def _resolve_frontend_url() -> str:
    url = os.environ.get("FRONTEND_URL")
    if url:
        return url
    if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == "singlebox":
        return f"http://{_SINGLEBOX_HOST}:8000"
    return _DEFAULT_FRONTEND_URL


def _resolve_backend_url() -> str:
    url = os.environ.get("BACKEND_URL")
    if url:
        return url
    if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == "singlebox":
        return os.environ.get(
            "SINGLEBOX_BACKEND_URL", f"http://{_SINGLEBOX_HOST}:8888"
        )
    return _DEFAULT_BACKEND_URL


class CronRelaySessionInitiator:
    def __init__(self, cron_relay, frontend_url=None, ...):
        self._frontend_url = frontend_url or _resolve_frontend_url()
        self._backend_url = _resolve_backend_url()
        ...
```

- singlebox 启动时 `/etc/hosts` 已配 `agentclaw-local.stable.alipay.net → 127.0.0.1`，本地域名让前端 / 钉钉卡片可达。

## 6. DiscoveryService 双通知通道

### 类图

```
            ┌─────────────────────────── DiscoveryService ───────────────────────────┐
            │                                                                          │
            │   reader    session_initiator  bot_service  discovery_lock_repo          │
            │       \           \                |              /                     │
            │        \   ─────────────────────────────────────────                    │
            │         \                                                          │
            │      notify_sender (NotifySenderPlugin seam)                      work_order_service
            │                │                                                          │
            │   ┌────────────┴─────────────┐                                create_work_order_event
            │   │  CommunityNotifySender   │   ← inner                       (event_category=NOTICE,
            │   │   日志通道               │                                    event_type=TASK_DISCOVERED)
            │   └────────────▲─────────────┘
            │                │ wraps
            │   ┌────────────┴─────────────┐ if DingTalkNotifySender._configured()
            │   │ DingTalkNotifySender      │ else: CommunityNotifySender 单独
            │   │  (装饰 inner)             │
            │   └───────────────────────────┘
            └──────────────────────────────────────────────────────────────────────────┘
```

### `_discover_single` 双通道并发

```python
card_sent = self._send_notification(task, owner_id, session, len(all_tasks))
work_order_sent = self._send_work_order_event(task, owner_id, session)
notification_sent = card_sent or work_order_sent

logger.info(
    "[task_discovery] task %s → session %s "
    "(card_sent=%s, work_order_sent=%s, notified=%s)",
    task.task_id, session.session_id,
    card_sent, work_order_sent, notification_sent,
)
```

### `_send_notification` — 钉钉卡片通道

签名变化：`session_url: str → session: DiscoverySession`；`card_data` 额外补 `click=""` 和 `session_url`：

```python
def _send_notification(self, task, user_id, session, task_count) -> bool:
    session_url = session.session_url
    message = NotifyMessage(
        title="发现待确认任务",
        body=task.to_notification_body(task_count),
        recipient=user_id,
        extra={
            "card_template_id": os.environ.get("TASK_DISCOVERY_CARD_TEMPLATE_ID", ""),
            "card_biz_id": f"discover_things_{task.task_id}",
            "card_data": json.dumps(
                {"click": "", **task.to_card_data(), "session_url": session_url}
            ),
            "session_url": session_url,
        },
    )
    try:
        msg_id = self._notify_sender.send(message, channel="markdown")
        logger.info("[task_discovery] external card sent for task %s (msg_id=%s)",
                    task.task_id, msg_id)
        return True
    except Exception as exc:
        logger.warning("[task_discovery] external notify failed for task %s: %s",
                       task.task_id, exc)
        return False
```

### `_send_work_order_event` — 工单 NOTICE 通道

```python
def _send_work_order_event(self, task, user_id, session) -> bool:
    svc = self._work_order_service
    if svc is None:
        return False
    try:
        result = svc.create_work_order_event(
            event_category=NotificationCategory.NOTICE,
            biz_type="task_discovery",
            biz_id=task.task_id,
            event_type=WorkOrderEventType.TASK_DISCOVERED.value,
            applicant_user_id=None,        # NOTICE 约束
            approver_user_ids=[],
            recipient_user_ids=[user_id],
            title=task.title,
            content={
                **task.to_card_data(),
                "session_url": session.session_url,
                "task_id": task.task_id,
            },
            apply_reason=None,
            biz_data={
                "task_id": task.task_id,
                "bot_id": task.bot_id,
                "owner_id": task.owner_id,
                "session_id": session.session_id,
                "session_url": session.session_url,
            },
            actor_id=user_id,
        )
        logger.info(
            "[task_discovery] work-order event created for task %s "
            "(notification_ids=%s, work_order_id=%s)",
            task.task_id,
            getattr(result, "notification_ids", None),
            getattr(result, "work_order_id", None),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[task_discovery] work-order event failed for task %s: %s",
            task.task_id, exc,
        )
        return False
```

- **NOTICE 约束**：`applicant_user_id=None` + `approver_user_ids=[]` + `recipient_user_ids` 非空；否则 `WorkOrderService` 返回 400 / 400201 `event_type not registered`。
- `biz_data` 把 `task_id` / `session_id` 等放进去，便于后续从工单反向查询发现记录。

### 工单事件类型注册

```python
class WorkOrderEventType(StrEnum):
    HUMAN2BOT_PUBLIC_ORDER_CREATED = "HUMAN2BOT_PUBLIC_ORDER_CREATED"
    HUMAN2BOT_PUBLIC_ORDER_COMPLETED = "HUMAN2BOT_PUBLIC_ORDER_COMPLETED"
    BOT2BOT_PUBLIC_ORDER_CREATED = "BOT2BOT_PUBLIC_ORDER_CREATED"
    BOT2BOT_PUBLIC_ORDER_COMPLETED = "BOT2BOT_PUBLIC_ORDER_COMPLETED"
    TASK_DISCOVERED = "TASK_DISCOVERED"        # 新增

EVENT_CATEGORIES: dict[WorkOrderEventType, NotificationCategory] = {
    ...
    WorkOrderEventType.TASK_DISCOVERED: NotificationCategory.NOTICE,
}
```

- NOTICE 工单不创建 `WorkOrder` 行（`work_order_id=null`），仅写入 `ac_work_order_notification` + `recipient_user_ids` 一行收件人记录。

## 7. DingTalkNotifySender — NotifySenderPlugin seam

```python
class DingTalkNotifySender(NotifySenderPlugin):
    def __init__(self, inner: NotifySenderPlugin) -> None:
        self._inner = inner

    @property
    def channels(self) -> frozenset[str]:
        return self._inner.channels

    def send(self, message, *, channel="markdown") -> str | None:
        # 先照常走 inner 通道（日志/兜底），再额外投递钉钉卡片。两者同时进行。
        msg_id = self._inner.send(message, channel=channel)
        try:
            self._send_dingtalk_card(message)
        except Exception as exc:
            log.warning("[DingTalkNotifySender] dingtalk card failed (recipient=%s): %s",
                        message.recipient, exc)
        return msg_id

    @staticmethod
    def _configured() -> bool:
        return all([
            _env("TASK_DISCOVERY_DINGTALK_AK_ID", "SINGLEBOX_DINGTALK_AK_ID"),
            _env("TASK_DISCOVERY_DINGTALK_AK_SECRET", "SINGLEBOX_DINGTALK_AK_SECRET"),
            _env("TASK_DISCOVERY_DINGTALK_ROBOT_CODE", "SINGLEBOX_DINGTALK_ROBOT_CODE"),
            _env("TASK_DISCOVERY_CARD_TEMPLATE_ID",
                 "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID"),
        ])

    def _send_dingtalk_card(self, message) -> None:
        if not self._configured():
            return
        ak_id = _env("TASK_DISCOVERY_DINGTALK_AK_ID", "SINGLEBOX_DINGTALK_AK_ID")
        ak_secret = _env("TASK_DISCOVERY_DINGTALK_AK_SECRET",
                         "SINGLEBOX_DINGTALK_AK_SECRET")
        robot_code = _env("TASK_DISCOVERY_DINGTALK_ROBOT_CODE",
                          "SINGLEBOX_DINGTALK_ROBOT_CODE")
        template_id = _env("TASK_DISCOVERY_CARD_TEMPLATE_ID",
                           "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID")
        account_id = (
            _env("TASK_DISCOVERY_DINGTALK_ACCOUNT_ID",
                 "SINGLEBOX_DINGTALK_ACCOUNT_ID")
            or message.recipient
        )
        extra = message.extra or {}
        card_biz_id = extra.get("card_biz_id") or f"discover_things_{int(time.time())}"
        card_data = extra.get("card_data")
        if not card_data:
            return

        # corp 钉钉 SDK 惰性导入
        from alibabacloud_tea_openapi import models as open_api_models  # type: ignore[import-not-found]
        from alibabacloud_tea_util import models as util_models  # type: ignore[import-not-found]
        from alipay_antdingopensdk_client import models as antding_models
        from alipay_antdingopensdk_client.client import Client as AntDingClient

        config = open_api_models.Config()
        config.access_key_id = ak_id
        config.access_key_secret = ak_secret
        client = AntDingClient(config)
        headers = antding_models.HttpHeader()
        headers.account_context = antding_models.AccountContext(account_id=account_id)
        req = antding_models.SendRobotInteractiveCardRequest()
        req.card_template_id = template_id
        req.robot_code = robot_code
        req.card_biz_id = card_biz_id
        req.card_data = card_data
        req.user_id = account_id
        resp = client.send_robot_interactive_card_with_options(
            req, headers, util_models.RuntimeOptions()
        )
        biz = resp.body
        resp_map = biz.to_map() if hasattr(biz, "to_map") else {"raw": str(biz)}
        log.info(
            "[DingTalkNotifySender] card sent (recipient=%s, template=%s, "
            "robot=%s, card_biz_id=%s) -> %s",
            account_id, template_id, robot_code, card_biz_id,
            json.dumps(resp_map, ensure_ascii=False),
        )
```

### DI 条件绑定

```python
class CommunityNotifyModule(Module):
    @singleton
    @provider
    def _notify_sender(self) -> NotifySenderPlugin:
        from agentclaw.community.plugins.community.notify_sender import (
            CommunityNotifySender, DingTalkNotifySender,
        )
        inner = CommunityNotifySender()
        if DingTalkNotifySender._configured():
            logger.info(
                "[community.notify] Binding DingTalkNotifySender(CommunityNotifySender) "
                "(log + dingtalk interactive card)",
            )
            return DingTalkNotifySender(inner)
        logger.info(
            "[community.notify] Binding CommunityNotifySender (log-only; no real delivery)"
        )
        return inner
```

- 与 `2026-08-24-discover-session-url-fix-and-dingtalk-notify-enhance` 中"钉钉下沉 DingTalkNotifySender"的 TODO 呼应完成。

## 8. WorkOrderService 注入

```python
class TaskDiscoveryModule(Module):
    @singleton
    @provider
    def provide_discovery_service(
        self,
        reader: TaskReader,
        session_initiator: SessionInitiator,
        notify_sender: NotifySenderPlugin,
        bot_service: _TaskDiscoveryBotServiceProtocol,
        discovery_lock_repo: TaskDiscoveryLockRepositoryProtocol,
        work_order_service: WorkOrderServiceProtocol,    # 新增
    ) -> DiscoveryService:
        return DiscoveryService(
            reader=reader,
            session_initiator=session_initiator,
            notify_sender=notify_sender,
            bot_service=bot_service,
            discovery_lock_repo=discovery_lock_repo,
            work_order_service=work_order_service,
        )
```

- `WorkOrderService` 由 `WorkOrderModule` 单独提供（不在本 spec 范围）。

## 9. router /discover 与 /status 字段名兼容

```python
# router.py /discovery/discover
"tasks": [
    {
        "task_id": r.task.task_id,
        "project_name": r.task.title,   # 仍输出 project_name（前端兼容），值取 title
        "success": r.success,
        "session_id": r.session.session_id if r.session else None,
        ...
    }
    ...
]

# router.py /discovery/status
{
    "bot_id": t.bot_id,
    "owner_id": t.owner_id,
    "dt": t.dt,
    "project_name": t.title,           # 仍输出 project_name，值取 title
    "status": t.status,
    "priority": t.priority,
    ...
}
```

- 前端契约保持不变；`DiscoveredTask.title` 仅是后端领域字段重命名，API 兼容层在 router 完成。

## 10. 测试改动

### 单元 `test_task_discovery_unit.py`

```python
_TASK = {
    "task_id": "task-001",
    "bot_id": "bot-001",
    "owner_id": "user-001",
    "dt": "2026-08-19",
    "title": "...",                          # was: project_name
    "instruction": "...",                    # was: description
    "background": "...",                     # was: business_scenario
    "discovery_basis": "...",
    "objective": "...",                       # 新增
    "acceptances": [                          # 新增
        {"id": "1", "description": "..."},
    ],
    "priority": "high",
    "discovered_at": "2026-08-17T10:00:00Z",
    "status": "pending_confirmation",
}

# test_read_pending_for_bot 新增断言：
#   objective / acceptances 通过 init→INSERT→SELECT 往返不丢
```

### Router `test_task_discovery_router.py`

`_status_task` 字典同样改为新键；构造 `DiscoveredTask(...)` 时**显式映射**避免 `**kwargs` 解包 `work_item_url` 引发 TypeError（如旧 dict 含 work_item_url 但 DiscoveredTask 已无该字段）。

### E2E — `test_cron_timed_fire_e2e.py` / `test_cron_scheduler_e2e.py`

- mock dict 改新键 + objective + acceptances
- session title 前缀断言改为 `[DreamMode-任务发现]`
- **删除独立钉钉 SDK Phase6**（`TestDingTalkCardE2E`、Phase6 standalone `_send_dingtalk_card` 等）：钉钉卡片现由 `DingTalkNotifySender` 集成投递，由 `DiscoveryService._discover_single` 触发；e2e 只设环境变量 + 验证 channel sent 即可。

### E2E — `test_discover_and_notify_e2e.py`

整文件删除。该脚本是首个钉钉 standalone 验证脚本，钉钉正式下沉到 `DingTalkNotifySender` 后该路径不再有用。

### E2E — `test_cron_timed_fire_workorder_e2e.py`（新增）

- 流程：setUp bot → 写 mock 待确认任务 → `POST /api/v1/collaboration/tasks/discovery/scheduled-trigger` → `GET /api/v1/collaboration/tasks/discovery/status`（断言 `[DreamMode-任务发现]` + session_id）→ `POST /openapi/v1/bots/work-orders/events` 构造 NOTICE 工单事件（在 discovery 不自动落库时改为手动单点验证，或下一步迁移到自动断言）
- mock dict 新键 + objective + acceptances 样例
- 与 e2e 之前定下的 workorder NOTICE 约束参数对齐：`applicant_user_id=None`、`approver_user_ids=[]`、`recipient_user_ids=[user_id]`、`event_type=TASK_DISCOVERED`
- 后端必须注入 `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` 才能放行 `/openapi/v1/*`，否则 401

## 11. 已踩过的坑（避免后续重蹈）

| 坑 | 表现 | 修复方式 |
|---|---|---|
| 装错 `tea-openapi` 版本（0.4.6） | 钉钉签名阶段 `sign.verify.error` | 固定 `alibabacloud-tea-openapi==0.3.10`；缺 `darabonba-core` / `credentials-api` 同步装上 |
| `init_discovered_tasks_db` 用 `CREATE TABLE IF NOT EXISTS` 旧库不升级 | `_row_to_task` 缺 `objective` / `acceptances` 列，`row["objective"]` 抛 KeyError | DROP+CREATE 重置；`_row_to_task` 同时防御性读 |
| `DiscoveredTask(**_status_task)` 解包旧 `work_item_url` | `TypeError: __init__() got an unexpected keyword argument 'work_item_url'` | 改显式键值映射 |
| 后端 `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` 未注入 | `/openapi/v1/*` 401 | 部署 / 联调时记得 export 并 restart |
| 后端进程 `_discoveries` 缓存跨 run 残留 | 同一 task_id 第二次 discover 走 `_discovery_status()` 直接读到上次 in-memory 结果判定已发现 | 单测过程重启后端清缓存；生产一般单 task_id 一次性触发不复发 |
| 后端 `bot 丢失`（重启擦 db） | 创建 bot → 重启 backend → bots 列表为 0 | singlebox 模式 SQLite 持久化层级不强，重新 POST `/api/bots` |
| `test_discover_and_notify_e2e.py` 假阳性钉钉卡片 | 测试污染 superbot 重启再跑仍看到旧卡片 | 删除文件下沉到 `DingTalkNotifySender` 后，只验证 channel sent，不依赖手动 SDK 调用 |
| `sed "description":→"instruction":` 误触 `acceptances` 内 `description` | mock 数据中 `{"id": "1", "instruction": "..."}` 应为 `description` | 改用上下文相关 regex（只在工作项 context 内替换，acceptances 内 description 不动） |
| `scripts/modules/backend.sh` 注释在 nohup 命令块内 | restart backend 启动失败 | 把注释放在命令块外（已恢复） |
| `WorkOrderListItem.work_order_no: str` 拒绝 `None` | `GET /openapi/v1/bots/work-orders` 因 NOTICE 工单无 work_order_no 返回 500 | **本 spec 未修复**，TODO 留下个 spec 改为 `str | None` |
