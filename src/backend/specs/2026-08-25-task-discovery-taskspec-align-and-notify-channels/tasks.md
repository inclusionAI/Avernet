# 任务清单 — DiscoveredTask 对齐 TaskSpec + 发现流程双通知通道

## 已完成

### DiscoveredTask 对齐 TaskSpec

- [x] T1: `DiscoveredTask` 字段重命名 — `project_name→title`、`description→instruction`、`business_scenario→background`
  - 文件: `core/task/task_discovery/models.py`
  - 同步 `to_notification_body` / `to_card_data`（`workitem_name=title`、`workitem_bg=instruction`） / `to_session_ext_info`（保留旧 key 兼容 engine）

- [x] T2: `DiscoveredTask` 新增 `objective: str = ""` + `acceptances: list[dict] = field(default_factory=list)`
  - 文件: `core/task/task_discovery/models.py`
  - docstring 说明对齐 TaskSpec.goal.objective / acceptances[AcceptanceCriteria{id, description}]

- [x] T3: 移除 `DiscoveredTask.work_item_url`
  - 文件: `core/task/task_discovery/models.py`、`task_reader.py`、`session_initiator.py`、`docs/task-discovery-api.zh-CN.md`

- [x] T4: `to_discovery_prompt`（单任务）4 维度 — 目标/预期交付物/验收标准/约束
  - 文件: `core/task/task_discovery/models.py`

- [x] T5: `_build_discovery_prompt`（多任务）4 维度 — 同上，编号 `1.` `2.`，末尾"请向用户展示..."
  - 文件: `core/task/task_discovery/session_initiator.py`

- [x] T6: session title 前缀 `[DreamMode] → [DreamMode-任务发现]`
  - 文件: `core/task/task_discovery/session_initiator.py` (2 处分支)
  - 单任务：`[DreamMode-任务发现] {first_task.title}`；多任务：`[DreamMode-任务发现] 发现 {task_count} 件...`

### discovered_tasks.db schema 同步

- [x] T7: DDL 列重命名 + 新增 `objective` / `acceptances` 列
  - 文件: `core/task/task_discovery/task_reader.py`

- [x] T8: `init_discovered_tasks_db` 改 `DROP TABLE IF EXISTS` + `CREATE TABLE`，保证新 schema 就位
  - 文件: `core/task/task_discovery/task_reader.py`

- [x] T9: `_SELECT_ALL_SQL` / `_SELECT_PENDING_FOR_BOT_SQL` 改 `SELECT *`，`_row_to_task` 防御性读 `objective` / `acceptances`
  - 文件: `core/task/task_discovery/task_reader.py`
  - 防御点：列缺失降级为默认值（`objective=""`、`acceptances=[]`），`acceptances` 非 list 回退 `[]`，JSON 解析失败回退 `[]`

- [x] T10: `init_discovered_tasks_db` INSERT 列名同步 + `acceptances` 用 `json.dumps(..., ensure_ascii=False)`
  - 文件: `core/task/task_discovery/task_reader.py`

- [x] T11: `MockTaskReader` mock 数据 + 构造同步新字段名
  - 文件: `core/task/task_discovery/task_reader.py`

### session_initiator URL 解析增强

- [x] T12: 新增 `_resolve_frontend_url()` — `FRONTEND_URL` env > singlebox → `http://agentclaw-local.stable.alipay.net:8000` > `http://localhost:8000`
  - 文件: `core/task/task_discovery/session_initiator.py`

- [x] T13: 新增 `_resolve_backend_url()` — `BACKEND_URL` env > `SINGLEBOX_BACKEND_URL` env > singlebox 模式 → `http://agentclaw-local.stable.alipay.net:8888` > `http://localhost:8888`
  - 文件: `core/task/task_discovery/session_initiator.py`

- [x] T14: `CronRelaySessionInitiator.__init__` 用两个 helper，移除内联 `os.environ.get`
  - 文件: `core/task/task_discovery/session_initiator.py`

### 钉钉通知集成到 DiscoveryService（NotifySender seam）

- [x] T15: 新增 `DingTalkNotifySender(NotifySenderPlugin)` — 装饰 `CommunityNotifySender`，`send()` 先 inner 后钉钉卡片，永不抛异常
  - 文件: `plugins/community/notify_sender.py`

- [x] T16: `DingTalkNotifySender._configured()` — 四项凭证齐全判断，带 `SINGLEBOX_*` 兼容回退
  - 文件: `plugins/community/notify_sender.py`

- [x] T17: `DingTalkNotifySender._send_dingtalk_card` — corp 钉钉 SDK 惰性导入，发 `send_robot_interactive_card_with_options`
  - 文件: `plugins/community/notify_sender.py`
  - 惰性导入：`alipay_antdingopensdk_client`、`alibabacloud_tea_openapi`、`alibabacloud_tea_util`

- [x] T18: `CommunityNotifyModule._notify_sender` 条件分支 — `_configured()` True → `DingTalkNotifySender(inner)`，否则 `inner`
  - 文件: `di/modules/infrastructure/community/notify.py`

### DiscoveryService 集成 WorkOrderService（NOTICE 工单事件）

- [x] T19: `WorkOrderEventType.TASK_DISCOVERED = "TASK_DISCOVERED"` + 在 `EVENT_CATEGORIES` 注册为 `NotificationCategory.NOTICE`
  - 文件: `core/work_orders/models.py`

- [x] T20: `DiscoveryService.__init__` 加 `work_order_service: WorkOrderServiceProtocol | None = None`
  - 文件: `core/task/task_discovery/discovery_service.py`
  - `TYPE_CHECKING` only import；运行期按鸭子类型调用

- [x] T21: 新增 `_send_work_order_event(task, user_id, session) -> bool` — 直接调用 `create_work_order_event`
  - 文件: `core/task/task_discovery/discovery_service.py`
  - 参数：`event_category=NOTICE` / `event_type=TASK_DISCOVERED` / `applicant_user_id=None` / `approver_user_ids=[]` / `recipient_user_ids=[user_id]` / `biz_type="task_discovery"` / `biz_id=task_id` / `biz_data` 含 session_id/session_url
  - 失败永不抛；`work_order_service is None` returns False（向后兼容）

- [x] T22: `_send_notification` 签名 `session_url: str → session: DiscoverySession`，`card_data` 补 `click=""` + `session_url`
  - 文件: `core/task/task_discovery/discovery_service.py`

- [x] T23: `_discover_single` 双通道并发 — `card_sent = _send_notification(...)` + `work_order_sent = _send_work_order_event(...)`，`notification_sent = card_sent or work_order_sent`
  - 文件: `core/task/task_discovery/discovery_service.py`
  - 日志：`card_sent=%s, work_order_sent=%s, notified=%s`

- [x] T24: `TaskDiscoveryModule.provide_discovery_service` 注入 `WorkOrderServiceProtocol`
  - 文件: `di/modules/task_discovery_module.py`

### HTTP API 兼容

- [x] T25: `/discovery/discover` / `/discovery/status` 用 `r.task.title` / `t.title` 填 `project_name` 输出字段（前端契约保持）
  - 文件: `adapters/http/task/router.py` (2 处)

- [x] T26: `session_creator.create_session` 的 `body["title"] = task.title`
  - 文件: `core/task/task_discovery/session_creator.py`

### 测试同步

- [x] T27: 单元 `test_task_discovery_unit.py` — `_TASK` dict + init 入参改新键 + objective + acceptances 样例
  - 文件: `tests/community/core/task/test_task_discovery_unit.py`
  - `test_read_pending_for_bot` 断言 `objective` / `acceptances` 在 init→INSERT→SELECT 往返不丢

- [x] T28: Router 单元 `test_task_discovery_router.py` — `_status_task` dict 新键 + 显式 `DiscoveredTask(...)` 映射
  - 文件: `tests/community/endpoints/test_task_discovery_router.py`
  - 显式映射避免 `**kwargs` 解包旧 `work_item_url` TypeError

- [x] T29: E2E `test_cron_scheduler_e2e.py` — mock dict 新键 + 删除独立 `TestDingTalkCardE2E` Phase6（钉钉下沉 DingTalkNotifySender）
  - 文件: `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`

- [x] T30: E2E `test_cron_timed_fire_e2e.py` — mock dict 新键 + 删除 Phase6 独立钉钉 SDK
  - 文件: `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_e2e.py`

- [x] T31: 删除 `test_discover_and_notify_e2e.py` — 独立钉钉 standalone 脚本，已下沉到 DingTalkNotifySender
  - 文件: `tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py`（delete）

- [x] T32: E2E `test_task_discovery_e2e.py` — mock 任务新键 + `/discovery/status` 断言新键 + `[DreamMode-任务发现]`
  - 文件: `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`

- [x] T33: 新增 `test_cron_timed_fire_workorder_e2e.py` — 工单 NOTICE 通道 e2e 流程
  - 文件: `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_workorder_e2e.py`
  - 流程：setUp bot → 写 mock 待确认 → scheduled-trigger → status 断言 → POST `/openapi/v1/bots/work-orders/events` 手动验证
  - mock dict 新键 + objective + acceptances 样例

### 外层单仓（非 ocb-public，仅记录依赖关系）

- [x] T34: `src/backend/pyproject.toml` `corp` 组新增钉钉 SDK 依赖（`antdingopensdk` / `alibabacloud-tea-openapi==0.3.10` / `darabonba-core==1.0.8` 等）
  - 外层 monorepo，非本 submodule
  - 注：作为 spec 边界之外的依赖固化记录在此，便于后续追溯

- [x] T35: `src/backend/uv.lock` 重新生成（`--index-strategy unsafe-best-match`）
  - 外层 monorepo

- [x] T36: `scripts/modules/backend.sh` 新增注释 — 说明钉钉凭证经 env 注入，不硬编码
  - 外层 monorepo

---

## 验证

### 单元测试

- [x] `test_task_discovery_unit.py` 全 26 个 case 通过 — 含 DiscoveredTask 新字段、init DB 往返、`objective`/`acceptances` 不丢
  - 命令: `cd ocb-public/src/backend && .venv/bin/python -m pytest tests/community/core/task/test_task_discovery_unit.py -s -v`

- [x] `test_task_discovery_router.py` 通过 — status 输出 `project_name` key 取自 `t.title`

### E2E — 钉钉通道（singlebox 启动 + 钉钉凭证注入后）

- [x] `test_cron_timed_fire_e2e.py` 通过 — Phase 4 real cron fired + `[DreamMode-任务发现]` title + DingTalkNotifySender card sent
  - 输入 env: `TASK_DISCOVERY_DINGTALK_AK_ID` / `_AK_SECRET` / `_ROBOT_CODE` / `TASK_DISCOVERY_CARD_TEMPLATE_ID`
  - 关键日志：`[DingTalkNotifySender] card sent (... processQueryKey=...)`
  - 关键日志：`[task_discovery] task ... → session ... (card_sent=True, work_order_sent=<bool>, notified=True)`

### E2E — 工单 NOTICE 通道

- [x] `test_cron_timed_fire_workorder_e2e.py` 通过 — `POST /openapi/v1/bots/work-orders/events` 返回 201 + `notification_ids` 非空
  - 后端注入 `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE` 否则 401
  - DB `/ac_work_order_notification` 出现 `event_type=TASK_DISCOVERED` 行（手动 POST 验证自动落库约束）

### 整体 unregression

- [x] 后端进程内存 `_discoveries` 缓存跨 run 残留 — Phase 4 假阳性；重启后端清缓存后 cron 可真实触发
- [x] 模型字段重命名后 `DiscoveredTask(**_status_task)` TypeError 已在 `test_task_discovery_router.py` 显式映射解决
- [x] `init_discovered_tasks_db` DROP+CREATE 解决旧 schema 升级 — `objective` / `acceptances` 列就位
- [x] 测试 mock `acceptances` 中 `description` 不被 sed 误改为 `instruction`（上下文敏感 regex 修复）
- [x] `scripts/modules/backend.sh` 注释移到命令块外，backend 启动恢复

---

## TODO（下个 spec 处理）

- [ ] **修复 `WorkOrderListItem.work_order_no: str` → `str | None`**：`GET /openapi/v1/bots/work-orders` 当前因 NOTICE 工单无 work_order_no 返回 500
  - 文件: `src/agentclaw/community/adapters/http/task/schemas.py`
  - 改后重启后端验证 list 返回 200
- [ ] `test_cron_timed_fire_workorder_e2e.py` Phase 6 改为自动断言 — depend on `DiscoveryService._send_work_order_event` 自动落库，仅断言 `ac_work_order_notification` 行而非手动 POST
- [ ] `acceptances` 多套 mock variant run 验证 `ext_info` / `card_data` 多样性
- [ ] 钉钉凭证在生产环境的注入路径文档化（部署平台 → `TASK_DISCOVERY_DINGTALK_*` env）
