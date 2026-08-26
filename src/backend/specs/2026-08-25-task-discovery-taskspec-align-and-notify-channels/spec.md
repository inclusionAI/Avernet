# DiscoveredTask 对齐 TaskSpec + 发现流程接入双通知通道（钉钉 NotifySender seam + 工单 NOTICE）

## 概述

本 spec 汇总 2026-08-24 到 2026-08-25 期间在 `task_discovery` 模块内的两类改动：

1. **DiscoveredTask 对齐执行层 TaskSpec**：把 `DiscoveredTask` 的 `project_name` / `description` / `business_scenario` 三个领域字段改名为 `title` / `instruction` / `background`，并新增 TaskSpec.goal 维度的 `objective` / `acceptances`；同步同步 `discovered_tasks.db` 列名、`to_discovery_prompt` 改为按 4 个维度（目标 / 预期交付物 / 验收标准 / 约束）组织，session 标题前缀 `[DreamMode]` → `[DreamMode-任务发现]`，移除已废弃的 `work_item_url` 字段。
2. **发现流程接通知双通道**：
   - **钉钉交互卡**：通过 `NotifySenderPlugin` seam 注入 `DingTalkNotifySender`（装饰 `CommunityNotifySender`），由 DI 在凭证就绪时绑定；`DiscoveryService` 仍只依赖 `notify_sender` 抽象，钉钉 SDK 不漏进发现主流程。
   - **工单 NOTICE 通知**：`DiscoveryService` 新增 `work_order_service` 依赖，发现成功后直接调用 `WorkOrderService.create_work_order_event`（`event_category=NOTICE`，`event_type=TASK_DISCOVERED`），把"待确认任务"写入 `ac_work_order_notification`，作为通知中心一个独立通道。

**关联文档**：
- `2026-08-24-discover-session-url-fix-and-dingtalk-notify-enhance/spec.md` — 前置 spec（session_url / [DreamMode] 标题 / e2e 钉钉卡片）
- `2026-08-20-task-discovery-endpoints-and-dingtalk-notify/spec.md` — 钉钉 SDK 首次接入
- `2026-08-18-task-discovery/spec.md` — task_discovery 模块总览

**领域定位**：
- `DiscoveredTask` 是任务**发现阶段**的领域对象，对应执行前 `(task_id, title, instruction, background, objective, acceptances, ...)` 元组。
- 执行层 `TaskSpec` 由 `Metadata{task_id, title, instruction}` + `Context{background, extend_props}` + `Goal{objective, acceptances[AcceptanceCriteria{id, description}]}` 三块组成；本 spec 让 `DiscoveredTask` 字段名与其语义对齐（不直接变成 `TaskInfoRequest`）。
- "动态任务消息指令"对应 `DiscoveredTask`；"task 发起"用 `POST /openapi/v1/collaboration/tasks/execute`，传 `TaskInfoRequest`，关联由确认后 `to_*` 适配完成。

---

## 需求列表

### REQ-1: DiscoveredTask 字段对齐 TaskSpec（重命名 + 新增 objective/acceptances）

- **描述**：`DiscoveredTask` 字段名不对齐执行层 TaskSpec 语义，发现 → 执行衔接需要手工映射。改为直接对齐：`project_name→title`、`description→instruction`、`business_scenario→background`，并新增 TaskSpec.goal 维度 `objective: str` / `acceptances: list[dict]`（每条 `{"id","description"}`）。
- **验收标准**：
  - `DiscoveredTask` 字段名变为 `title` / `instruction` / `background` / `objective` / `acceptances`
  - `to_session_ext_info` 仍保留旧键名（`project_name` / `description` / `business_scenario`，值取自新字段），保证 engine 兼容
  - `to_card_data` 中 `workitem_name=title`、`workitem_bg=instruction`
  - `to_notification_body` 用 `title` / `instruction`
  - `acceptances` 默认 `field(default_factory=list)`，`objective` 默认 `""`
  - 字段顺序与 TaskSpec 三块语义一致（title/instruction/background 为 metadata/context，objective/acceptances 在末尾）
- **改动文件**：
  - `src/agentclaw/community/core/task/task_discovery/models.py` — 字段重命名 + 新增 `objective` / `acceptances` + 类 docstring 更新 + `to_notification_body` / `to_card_data` / `to_session_ext_info` 同步
- **状态**：已完成

### REQ-2: 移除 DiscoveredTask.work_item_url

- **描述**：`work_item_url` 字段未在执行链路实际使用，从模型 / DB 列 / e2e mock / API 文档中统一移除。
- **验收标准**：
  - `DiscoveredTask` 不再有 `work_item_url` 字段
  - `discovered_tasks.db` DDL 不再有 `work_item_url` 列
  - `init_discovered_tasks_db` 的 INSERT SQL 不再写该列
  - `docs/task-discovery-api.zh-CN.md` 表格中 `work_item_url` 行删除
  - `_build_discovery_prompt` / `to_card_data` 删除对 `work_item_url` 的引用
- **改动文件**：
  - `core/task/task_discovery/models.py`、`core/task/task_discovery/task_reader.py`、`core/task/task_discovery/session_initiator.py`、`docs/task-discovery-api.zh-CN.md`
- **状态**：已完成

### REQ-3: discovered_tasks.db schema 同步重命名 + init DROP+CREATE

- **描述**：DB 表列名沿用旧 `project_name` / `description` / `business_scenario` / `work_item_url`，与模型不匹配；`CREATE TABLE IF NOT EXISTS` 也不会给已存在的旧库加列。
- **验收标准**：
  - DDL 列改为 `title TEXT NOT NULL` / `instruction TEXT` / `background TEXT` / `objective TEXT` / `acceptances TEXT`，不再有 `work_item_url`
  - `init_discovered_tasks_db` 改为 `DROP TABLE IF EXISTS` + `CREATE TABLE`，保证每次初始化使用最新 schema
  - `_SELECT_ALL_SQL` / `_SELECT_PENDING_FOR_BOT_SQL` 改为 `SELECT *`，由 `_row_to_task` 防御性读取
  - `_row_to_task` 对 `objective` / `acceptances` 缺列容错（旧库读取），`acceptances` JSON 解析失败回退 `[]`
  - INSERT 列名同步为 `title` / `instruction` / `background` / `objective` / `acceptances`（acceptances 用 `json.dumps(..., ensure_ascii=False)` 落库）
- **改动文件**：
  - `core/task/task_discovery/task_reader.py` — DDL / SELECT / `_row_to_task` / `init_discovered_tasks_db` / MockTaskReader mock 数据 + 构造同步
- **状态**：已完成

### REQ-4: 发现提示词按 TaskSpec 4 维度组织

- **描述**：单任务 / 多任务的发现提示词原为"简介 + 业务场景"二维，应按执行层 TaskSpec 的 4 个维度（目标 / 预期交付物 / 验收标准 / 约束）重组，让 session 内 bot 直接看到 TaskSpec 语义。
- **验收标准**：
  - `DiscoveredTask.to_discovery_prompt()`（单任务）输出：
    ```
    /task 我为您发现了以下可能有意义的事情：
    【{title}】
    目标：{objective or title}
    预期交付物：{instruction}
    验收标准：
      - [{id}] {description}
    约束：{background}
    是否确认执行？请在下方回复确认或拒绝。
    ```
  - `SessionInitiator._build_discovery_prompt()`（多任务）输出每条任务带编号 `1.` / `2.`，4 维度同上，末行 "请向用户展示以上任务，并询问是否确认执行。"
  - `acceptances` 为空时打印 `验收标准：（确认时可由你补充）`
- **改动文件**：
  - `core/task/task_discovery/models.py` — `to_discovery_prompt`
  - `core/task/task_discovery/session_initiator.py` — `_build_discovery_prompt`
- **状态**：已完成

### REQ-5: session 标题前缀 [DreamMode] → [DreamMode-任务发现]

- **描述**：发现创建的 session title 前缀改为 `[DreamMode-任务发现]`，与普通工作 session 区分更明确。
- **验收标准**：
  - 单任务：`[DreamMode-任务发现] {first_task.title}`
  - 多任务：`[DreamMode-任务发现] 发现 {task_count} 件可能有意义的事情`
  - 仍按 `2026-08-24-discover-session-url-fix-and-dingtalk-notify-enhance` 的 Step 2.5 单独 update title 流程（engine 创建时 title 不生效）
- **改动文件**：
  - `core/task/task_discovery/session_initiator.py` — title 构造两处分支 + `first_task.project_name → first_task.title`
- **状态**：已完成

### REQ-6: session_initiator URL 解析环境感知化（_resolve_frontend_url / _resolve_backend_url）

- **描述**：硬编码 `http://localhost:8888` / `http://localhost:8000` 在 singlebox 本地域名场景下会绕过 `/etc/hosts`，导致 session_url 不可外发。改为环境感知解析。
- **验收标准**：
  - 新增 `_resolve_frontend_url()` 优先级：`FRONTEND_URL` env > `DEPLOY_PROFILE=singlebox` → `http://agentclaw-local.stable.alipay.net:8000` > fallback `http://localhost:8000`
  - 新增 `_resolve_backend_url()` 优先级：`BACKEND_URL` env > `SINGLEBOX_BACKEND_URL` env > singlebox 模式 → `http://agentclaw-local.stable.alipay.net:8888` > fallback `http://localhost:8888`
  - `CronRelaySessionInitiator.__init__` 用上述两个 helper，移除原内联 `os.environ.get("FRONTEND_URL", ...)` 逻辑
  - 常量从 `_DEFAULT_FRONTEND_PORT="8000"` / `_DEFAULT_BACKEND_URL` 调整为 `_DEFAULT_FRONTEND_URL` / `_DEFAULT_BACKEND_URL` + `_SINGLEBOX_HOST`
- **改动文件**：
  - `core/task/task_discovery/session_initiator.py` — 新增两个 helper + `__init__` 调用
- **状态**：已完成

### REQ-7: 钉钉通知集成到 DiscoveryService（NotifySenderPlugin seam → DingTalkNotifySender）

- **描述**：钉钉 SDK 此前仅在 e2e 测试脚本中手动调用；下沉到 `DingTalkNotifySender` 实现 `NotifySenderPlugin`，作为 `CommunityNotifySender` 的装饰器，由 DI 在凭证就绪时绑定。`DiscoveryService` 仍只依赖 `notify_sender`，钉钉 SDK 不漏进发现主流程。
- **验收标准**：
  - 新增 `DingTalkNotifySender(NotifySenderPlugin)` 接收一个 `inner` 通道，`send()` 先走 inner（日志/兜底），再 `_send_dingtalk_card()` 投递钉钉交互卡片，**永不抛异常**（钉钉失败仅 warning）
  - 静态 `_configured()` 要求四项齐全：`TASK_DISCOVERY_DINGTALK_AK_ID`（回退 `SINGLEBOX_DINGTALK_AK_ID`）、`_AK_SECRET`（回退 `SINGLEBOX_DINGTALK_AK_SECRET`）、`_ROBOT_CODE`（回退 `SINGLEBOX_DINGTALK_ROBOT_CODE`）、`TASK_DISCOVERY_CARD_TEMPLATE_ID`（回退 `SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID`）
  - `account_id` 取 `TASK_DISCOVERY_DINGTALK_ACCOUNT_ID`（回退 `SINGLEBOX_DINGTALK_ACCOUNT_ID`），再回退 `message.recipient`
  - corp 钉钉 SDK（`alipay_antdingopensdk_client`、`alibabacloud_tea_openapi` / `alibabacloud_tea_util`）**惰性导入**（在 `_send_dingtalk_card` 内），凭证缺失时根本不导入
  - 卡片载荷来自 `NotifyMessage.extra`：`card_template_id` / `card_biz_id` / `card_data` / `session_url`，由 `DiscoveryService._send_notification` 填入
  - 凭证未注入时 `_configured()=False`，`send()` 静默只走 inner，钉钉侧 no-op
- **改动文件**：
  - `plugins/community/notify_sender.py` — 新增 `DingTalkNotifySender` + `_env()` 兼容回退
  - `di/modules/infrastructure/community/notify.py` — `CommunityNotifyModule._notify_sender` 条件分支：`DingTalkNotifySender._configured()` 为 True → `DingTalkNotifySender(CommunityNotifySender())`，否则 `CommunityNotifySender()`
- **状态**：已完成

### REQ-8: DiscoveryService 集成 WorkOrderService（直接调用 NOTICE 工单事件）

- **描述**：除钉钉卡片外，发现流程需要把"待确认任务"作为一条 NOTICE 通知写入工单收件箱（`ac_work_order_notification`），供工单/通知中心展示。`DiscoveryService` 直接调用 `WorkOrderService.create_work_order_event`，不通过 NotifySender seam。
- **验收标准**：
  - `DiscoveryService.__init__` 新增 `work_order_service: WorkOrderServiceProtocol | None = None` 参数（`TYPE_CHECKING` only，运行期按鸭子类型调用）
  - DI 模块 `TaskDiscoveryModule` 注入 `WorkOrderServiceProtocol` 到 `DiscoveryService`
  - 新增 `_send_work_order_event(task, user_id, session) -> bool`，调用 `create_work_order_event`：
    - `event_category=NotificationCategory.NOTICE`
    - `event_type=WorkOrderEventType.TASK_DISCOVERED.value`
    - `applicant_user_id=None`（NOTICE 约束）
    - `approver_user_ids=[]`
    - `recipient_user_ids=[user_id]`
    - `biz_type="task_discovery"`、`biz_id=task.task_id`
    - `title=task.title`，`content` 含 `to_card_data()` + `session_url` + `task_id`
    - `biz_data` 含 `task_id` / `bot_id` / `owner_id` / `session_id` / `session_url`
    - `actor_id=user_id`
  - 失败永不抛异常，仅 warning + 返回 False
  - `_work_order_service` 未注入（None）时 returns False（向后兼容）
  - `_discover_single` 同时跑两个通道：`card_sent = self._send_notification(...)`（接收 `session` 而非 `session_url`）+ `work_order_sent = self._send_work_order_event(...)`，`notification_sent = card_sent or work_order_sent`
- **改动文件**：
  - `core/task/task_discovery/discovery_service.py` — `__init__` 加 `work_order_service` 参数；`_send_notification` 签名 `session_url` → `session`（内部取 `session_url`，extra.card_data 补 `click` / `session_url`）；新增 `_send_work_order_event`
  - `core/work_orders/models.py` — `WorkOrderEventType.TASK_DISCOVERED = "TASK_DISCOVERED"` + 在 `EVENT_CATEGORIES` 注册为 `NotificationCategory.NOTICE`
  - `di/modules/task_discovery_module.py` — `provide_discovery_service` 新增 `work_order_service: WorkOrderServiceProtocol` 参数并注入
- **状态**：已完成

### REQ-9: HTTP /discovery/discover 与 /discovery/status 响应字段名兼容

- **描述**：模型字段重命名后，router 仍以旧 `project_name` 为 JSON key 输出给前端，与 DB / 模型名不一致。
- **验收标准**：
  - `/discovery/discover` 返回的每个 task 仍以 `project_name` 为 key（保持前端兼容），值取自 `r.task.title`
  - `/discovery/status` 返回的每个 task 同样以 `project_name` 为 key，值取自 `t.title`
- **改动文件**：
  - `adapters/http/task/router.py` — 两处 `r.task.project_name → r.task.title` / `t.project_name → t.title`
- **状态**：已完成

### REQ-10: session_creator title 使用新字段

- **描述**：session 创建请求体的 `title` 原 `task.project_name`，需要随模型字段重命名同步。
- **验收标准**：
  - `HttpSessionCreator.create_session` 的 `body["title"] = task.project_name` → `task.title`
- **改动文件**：
  - `core/task/task_discovery/session_creator.py`
- **状态**：已完成

### REQ-11: 单元 / e2e 测试 mock 数据全量对齐新字段名 + objective + acceptances

- **描述**：DB 列改名后，所有 mock 数据字典需要从 `project_name` / `description` / `business_scenario` / `work_item_url` 改为 `title` / `instruction` / `background` + 新增 `objective` / `acceptances` 样例；测试中的 `DiscoveredTask` 显式构造（防 `**kwargs` TypeError）也要同步。
- **验收标准**：
  - `test_task_discovery_unit.py`：`_TASK` dict + `init_discovered_tasks_db` 入参改为新键 + `objective` / `acceptances` 样例；`test_read_pending_for_bot` 断言 `objective` / `acceptances` 在 DB 往返不丢
  - `test_task_discovery_router.py`：`_status_task` dict 新键；`DiscoveredTask(...)` 显式键值映射
  - `singlebox_e2e/test_cron_scheduler_e2e.py` / `.../test_cron_timed_fire_e2e.py`：mock dict 新键 + 前缀 `[DreamMode-任务发现]` 断言；钉钉 Phase6（独立 SDK 调用）从测试脚本中删除（已下沉到 DingTalkNotifySender）
  - `singlebox_e2e/test_discover_and_notify_e2e.py`：整文件删除（纯独立钉钉 standalone 脚本，已被 DingTalkNotifySender 集成取代）
  - `singlebox_e2e/test_task_discovery_e2e.py`：mock 任务字段 + /discovery/status 断言新键
  - 新增 `singlebox_e2e/test_cron_timed_fire_workorder_e2e.py` — workorder 通道 e2e（discover → 工单 NOTICE 通知落库），mock dict 新键
- **改动文件**：
  - `tests/community/core/task/test_task_discovery_unit.py`
  - `tests/community/endpoints/test_task_discovery_router.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py`（删除）
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_workorder_e2e.py`（新增）
- **状态**：已完成

---

## 改动文件清单

### 源码

| 文件 | 改动 |
|---|---|
| `core/task/task_discovery/models.py` | 字段对齐 TaskSpec（title/instruction/background + objective/acceptances）；`to_discovery_prompt` 4 维度；`to_session_ext_info` 保留旧键名兼容 engine；移除 `work_item_url` |
| `core/task/task_discovery/task_reader.py` | DDL 列重命名 + 新增 objective/acceptances 列；`init_discovered_tasks_db` DROP+CREATE；`SELECT *` + 防御性 `_row_to_task`；MockTaskReader 同步 |
| `core/task/task_discovery/session_initiator.py` | 4 维度 `_build_discovery_prompt`；`_build_session_url` 用 `_resolve_frontend_url`；`_resolve_backend_url`；title 前缀 `[DreamMode-任务发现]` + `first_task.title` |
| `core/task/task_discovery/session_creator.py` | `body["title"] = task.title` |
| `core/task/task_discovery/discovery_service.py` | `__init__` + `work_order_service`；`_send_notification` 签名 `session_url → session`；新增 `_send_work_order_event`；`_discover_single` 双通道 card_sent + work_order_sent |
| `core/work_orders/models.py` | `WorkOrderEventType.TASK_DISCOVERED` + `EVENT_CATEGORIES[TASK_DISCOVERED] = NOTICE` |
| `di/modules/infrastructure/community/notify.py` | `_notify_sender` 条件分支：DingTalkNotifySender vs CommunityNotifySender |
| `di/modules/task_discovery_module.py` | `provide_discovery_service` 注入 `work_order_service` |
| `plugins/community/notify_sender.py` | 新增 `DingTalkNotifySender` + `_env()` 兼容回退；惰性导入 corp SDK |
| `adapters/http/task/router.py` | `/discovery/discover` / `/discovery/status` 返回 `project_name` 字段取自 `r.task.title` / `t.title` |
| `docs/task-discovery-api.zh-CN.md` | 表格移除 `work_item_url` 行 |

### 测试

| 文件 | 改动 |
|---|---|
| `tests/.../test_task_discovery_unit.py` | mock dict 新键 + objective/acceptances；`test_read_pending_for_bot` 断言 DB 往返 |
| `tests/.../test_task_discovery_router.py` | `_status_task` 新键；`DiscoveredTask(...)` 显式映射 |
| `tests/.../singlebox_e2e/test_cron_scheduler_e2e.py` | mock dict 新键；删除独立钉钉 SDK Phase6（TestDingTalkCardE2E） |
| `tests/.../singlebox_e2e/test_cron_timed_fire_e2e.py` | mock dict 新键；删除独立钉钉 Phase6（DingTalkNotifySender 接管） |
| `tests/.../singlebox_e2e/test_discover_and_notify_e2e.py` | 整文件删除 |
| `tests/.../singlebox_e2e/test_task_discovery_e2e.py` | mock dict 新键 + status 断言 |
| `tests/.../singlebox_e2e/test_cron_timed_fire_workorder_e2e.py` | **新增**：workorder 通道 e2e（discover → 工单 NOTICE 落库），mock dict 新键 |

### 外层单仓（非 ocb-public，仅在 spec 中记录依赖关系）

| 文件 | 改动 |
|---|---|
| `src/backend/pyproject.toml`（外层单仓） | `corp` 组新增钉钉 SDK 依赖（运行期 corp 集群才用到） |
| `src/backend/uv.lock`（外层单仓） | 重新生成（含钉钉 SDK transitive 依赖） |
| `scripts/modules/backend.sh`（外层单仓） | 新增注释（说明钉钉凭证经 env 注入，不硬编码到脚本） |

---

## SDK 依赖（钉钉 NotifySender）

需在 `pyproject.toml` 的 `corp` 组固化（singlebox 测试时也需手动 `uv pip install`）：

| 包 | 版本 | 用途 |
|---|---|---|
| `antdingopensdk` | 1.0.47（或 1.0.46） | antding opensdk client |
| `alibabacloud-tea-openapi` | 0.3.10 | openapi models（**版本必须 0.3.x，不要用最新的 0.4.6 — 签名方法不兼容**） |
| `alibabacloud-tea-util` | 0.3.15 | RuntimeOptions |
| `alibabacloud-openapi-util` | 0.2.2 | 签名 util |
| `alibabacloud-endpoint-util` | 0.0.3 | endpoint 解析 |
| `alibabacloud-credentials` | 0.3.6 | 凭证 |
| `alibabacloud-credentials-api` | 1.0.1 | credentials API |
| `darabonba-core` | 1.0.8 | tea core |

---

## 环境变量

### 钉钉 NotifySender（DingTalkNotifySender）

| 环境变量 | 兼容回退 | 备注 |
|---|---|---|
| `TASK_DISCOVERY_DINGTALK_AK_ID` | `SINGLEBOX_DINGTALK_AK_ID` | 钉钉 AK ID |
| `TASK_DISCOVERY_DINGTALK_AK_SECRET` | `SINGLEBOX_DINGTALK_AK_SECRET` | 钉钉 AK Secret |
| `TASK_DISCOVERY_DINGTALK_ROBOT_CODE` | `SINGLEBOX_DINGTALK_ROBOT_CODE` | 机器人 code |
| `TASK_DISCOVERY_CARD_TEMPLATE_ID` | `SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID` | 卡片模板 ID |
| `TASK_DISCOVERY_DINGTALK_ACCOUNT_ID` | `SINGLEBOX_DINGTALK_ACCOUNT_ID`（再回退 `message.recipient`） | 通知目标 user |

### session URL 解析（SessionInitiator）

| 环境变量 | 默认 | 备注 |
|---|---|---|
| `FRONTEND_URL` | singlebox 模式下 `http://agentclaw-local.stable.alipay.net:8000`，否则 `http://localhost:8000` | 前端 workbench 地址 |
| `BACKEND_URL` / `SINGLEBOX_BACKEND_URL` | singlebox 模式下 `http://agentclaw-local.stable.alipay.net:8888`，否则 `http://localhost:8888` | backend 自身地址 |
| `DEPLOY_PROFILE` | — | 值为 `singlebox` 时启用本地域名 |

### 工单 notice（NOTICE 工单事件）

后端需有 `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` 才能放行 `POST /openapi/v1/bots/work-orders/events` 等 `/openapi/v1/*` 请求；工单创建的 NOTICE 路径由 `WorkOrderEventType.TASK_DISCOVERED` 在 `EVENT_CATEGORIES` 注册为 `NotificationCategory.NOTICE` 触发。

---

## 验证命令

### 单元测试

```bash
cd ocb-public/src/backend

# task_discovery 单元（含 DiscoveredTask 新字段 + DB 往返）
.venv/bin/python -m pytest tests/community/core/task/test_task_discovery_unit.py -s -v

# router 单元（status 响应字段名）
.venv/bin/python -m pytest tests/community/endpoints/test_task_discovery_router.py -s -v
```

### E2E — 钉钉 NotifySender 通道（singlebox 启动后）

```bash
cd ocb-public/src/backend

# 重启后端带上钉钉凭证
#   ocb-public/scripts/singlebox.sh restart backend
# 或手动 export：
#   export TASK_DISCOVERY_DINGTALK_AK_ID=... \
#           TASK_DISCOVERY_DINGTALK_AK_SECRET=... \
#           TASK_DISCOVERY_DINGTALK_ROBOT_CODE=... \
#           TASK_DISCOVERY_CARD_TEMPLATE_ID=... \
#           TASK_DISCOVERY_DINGTALK_ACCOUNT_ID=440718

SINGLEBOX_CRON_E2E=1 SINGLEBOX_USER_ID=440718 \
  .venv/bin/python -m pytest \
    tests/community/core/task/singlebox_e2e/test_cron_timed_fire_e2e.py -s -v
```

### E2E — 工单 NOTICE 通道

```bash
# 后端必须注入 principal signing key（dev key），否则 /openapi/v1 返回 401
#   ocb-public/scripts/singlebox.sh restart backend

SINGLEBOX_CRON_E2E=1 SINGLEBOX_USER_ID=440718 \
  .venv/bin/python -m pytest \
    tests/community/core/task/singlebox_e2e/test_cron_timed_fire_workorder_e2e.py -s -v
```

### 通道运行验证要点

- 钉钉：发现成功后 `processQueryKey` 返回，session title 前缀 `[DreamMode-任务发现]`
- 工单：`ac_work_order_notification` 出现 `event_type=TASK_DISCOVERED` 行，`notification_ids` 非空
- 两通道并发，任一失败仅在日志 warning，不影响发现主流程

---

## TODO

- **修复 `WorkOrderListItem.work_order_no: str` → `str | None`**：`GET /openapi/v1/bots/work-orders` 当前因 NOTICE 工单无 `work_order_no`，pydantic 校验阶段返回 500（与发现流程不冲突，但已观察到）。建议下次 bugfix spec 单独修复。
- **`test_cron_timed_fire_workorder_e2e.py` Phase6 改为自动断言**：目前 `discovery` 阶段不直接断言 `TASK_DISCOVERED` 通知自动创建，仍走手动 POST；可改为依赖 `DiscoveryService._send_work_order_event` 自动落库，仅断言 `ac_work_order_notification` 行。
- **`acceptances` 单一 mock 样例**：当前 e2e mock 仅一套 objective/acceptances 样例，可补一套 variant run 验证 `ext_info` / `card_data` 多样性。
- **`WorkOrderNotifySender` 路径（已废弃）**：早期讨论过把工单也做成 `NotifySenderPlugin` seam，最终采纳"直接领域调用 WorkOrderService"方案；本 spec 不再涵盖该废弃路径。
- **凭证管控**：钉钉凭证在生产环境应由部署平台注入 `TASK_DISCOVERY_DINGTALK_*`，不要硬编码到 `scripts/modules/backend.sh`；singlebox 联调时可手动 export 后 `restart backend`。

---

## 技术决策

### 为什么 DiscoveredTask 不直接变成 TaskInfoRequest？

`DiscoveredTask` 是发现阶段的领域对象，承载"被发现状态 / 待确认状态 / 优先级 / 挖掘依据"等发现特有字段；执行层 `TaskInfoRequest`（`POST /openapi/v1/collaboration/tasks/execute`）只关心执行所需的 TaskSpec 三块（Metadata / Context / Goal）。两者通过字段名对齐让"确认 → 执行"的适配变成纯字段映射（如 `to_session_ext_info` 内部就保留旧 key 兼容 engine），但**保留两个对象**避免发现侧状态污染执行侧契约。

### 为什么钉钉用 NotifySender seam、工单用直接领域调用？

- **钉钉**：`NotifySenderPlugin` 是"消息外发通道"抽象，本就该用一个 plugin 把"日志/兜底通道"和"真正外发通道"组合。`CommunityNotifySender` 是日志通道，`DingTalkNotifySender` 是装饰它的钉钉通道；`DiscoveryService` 不感知钉钉存在。这种 seam 模式让未来可平滑增加其他外发通道（飞书 / 企业微信）。
- **工单 NOTICE**：`ac_work_order_notification` 是**领域内**的工单收件箱，不是消息外发通道。工单事件创建涉及 `event_category` / `event_type` / `applicant_user_id` / `approver_user_ids` / `recipient_user_ids` / `biz_data` 等领域语义，硬塞进 NotifySender 会让 seam 越界。最终选择 `DiscoveryService` 直接持 `WorkOrderService` 引用并调用 `create_work_order_event`，📟 与 `notify_sender` 平行作为发现流程的"两条通知通道"。

### 为什么 init_discovered_tasks_db 改为 DROP+CREATE？

`CREATE TABLE IF NOT EXISTS` 在已存在旧 schema 时不会添加新列（`objective` / `acceptances`），导致测试初始化后字段不齐。e2e / 单测场景下 `init_discovered_tasks_db` 是"清零 + 重灌"语义，DROP+CREATE 既清空旧数据又保证新 schema 就位，语义更清晰。生产 reader（`SqliteTaskReader.add_task`）走 `INSERT OR IGNORE`，与 `init_discovered_tasks_db` 互不影响。

### 为什么 `_SELECT_ALL_SQL` 改为 `SELECT *`？

旧版 SELECT 显式列出字段名，schema 升级时必须同步 SQL；改 `SELECT *` 后，增加列无需改 SQL，只需在 `_row_to_task` 做防御性读取（`keys = set(row.keys())` 判断 `objective` / `acceptances` 是否存在）。旧库（缺这两列）也能降级读取，向后兼容。

### 为什么 `_row_to_task` 要防御性读 `objective` / `acceptances`？

如上线后 reader 拿到一个旧 schema 的 `discovered_tasks.db`（如生产环境未 DROP），`SELECT *` 只会返回旧列，没有 `objective` / `acceptances`。直接 `row["objective"]` 会 `IndexError`。防御性访问 `row["objective"] if "objective" in keys and row["objective"] else ""`，缺列回退默认值。`acceptances` 还要防 JSON 解析错误回退 `[]`，避免脏数据让 reader 崩。

### 为什么 `_send_notification` 签名从 `session_url: str` 改为 `session: DiscoverySession`？

`_send_work_order_event` 同样需要 `session.session_url` / `session.session_id`，传 `session` 而非 `session_url` 让两个通道用同一接口，并在 `_send_notification` 内部把 `session_url` 补到 `card_data` 的 `click` 和 `session_url` 字段（钉钉卡片模板按这些字段渲染"CTA + 直达链接"），减少参数传递层级。

### 为什么钉钉 SDK 必须**惰性导入**？

corp 钉钉 SDK（`alipay_antdingopensdk_client` 等）只在生产 / 预发 / singlebox 联调时可用。社区 / 开发 profile 没装这些包，模块顶层 import 会让后端启动直接 `ModuleNotFoundError`。`send()` 内部惰性导入配合 `_configured()` 前置判断，保证未配置凭证时根本不触发 import 路径。

### 为什么钉钉 SDK 必须 `tea-openapi==0.3.10` 而非 0.4.6？

`alipay_antdingopensdk_client 1.0.46` 依赖 `tea-openapi 0.3.x` 的签名实现。若被 pip 解析为 0.4.6，`send_robot_interactive_card_with_options` 在签名阶段抛 `sign.verify.error`（请求签名与钉钉验证不匹配）。`pyproject.toml` 必须固定 `alibabacloud-tea-openapi==0.3.10`。同时需要 `alibabacloud-openapi-util==0.2.2` + `darabonba-core==1.0.8` 提供底层签名 / tea core。

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-25 | 初始创建 — 汇总 08-24 → 08-25 期间 task_discovery 模块 DiscoveredTask 对齐 TaskSpec、发现流程接入钉钉 NotifySender seam + 工单 NOTICE 双通道、DreamMode-任务发现 标题、4 维度发现提示词、DB schema 同步重命名等全部改动 |
