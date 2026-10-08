# Tasks — task_discovery HTTP 端点补齐 + 钉钉卡片通知 e2e + discover_all_bots 改造

## 任务总览

| Task | 目标 | 关键文件 | 依赖 | 状态 |
|---|---|---|---|---|
| T1 | 新增 scheduler-status / scheduled-trigger 端点 | `router.py` | 无 | ✓ done |
| T2 | 修复 db 路径级数错位 | `task_discovery_module.py` | 无 | ✓ done |
| T3 | discover_all_bots 改造（交集 + owner 聚合） | `discovery_service.py` | T2 | ✓ done |
| T4 | /discovery/status 改造（关联 _discoveries） | `router.py` + `discovery_service.py` | T1 | ✓ done |
| T5 | 钉钉卡片通知 e2e 测试 | `test_cron_scheduler_e2e.py` | T1 | ✓ done |
| T6 | 测试选取第一个 ACTIVE bot | 两个测试文件 | 无 | ✓ done |
| T7 | test_discover_then_status HTTP 接口测试 | `test_task_discovery_e2e.py` | T4 | ✓ done |
| T8 | 测试 URL 路径同步 | `test_cron_scheduler_e2e.py` | T1 | ✓ done |
| T9 | spec 文档 | `specs/2026-08-20-.../` | T1-T8 | ✓ done |

---

## T1: 新增 scheduler-status / scheduled-trigger 端点

- **Goal**: 在 `task/router.py` 新增两个 discovery 端点
- **Files**: `src/agentclaw/community/adapters/http/task/router.py`
- **Implementation**:
  - import `TaskDiscoveryScheduler`
  - `@router.get("/discovery/scheduler-status")` — 调 `scheduler.get_status()`，返回扁平 JSON `{success, running, jobs, cron, timezone, auto_start}`
  - `@router.post("/discovery/scheduled-trigger")` — 调 `service.discover_all_bots()`，返回扁平 JSON `{success, total_discovered, results[]}`
  - results[] 每条含 `bot_id, task_id, success, session_id, session_url, notification_sent, error`
- **Validation**: `test_01_scheduler_status` + `test_02_scheduled_trigger` PASSED
- **Dependencies**: 无

---

## T2: 修复 db 路径级数错位

- **Goal**: `task_discovery_module.py` 的 `_PROJECT_ROOT` 上溯级数从 9 改为 8
- **Files**: `src/agentclaw/community/di/modules/task_discovery_module.py`
- **Implementation**:
  - `for _ in range(9):` → `for _ in range(8):`
  - 注释 "9 级" → "8 级"
- **Validation**: 三方路径一致 → `ocb-public/scripts/.dependencies/data/discovered_tasks.db`
- **Dependencies**: 无

---

## T3: discover_all_bots 改造

- **Goal**: 从 db pending tasks 提取 bot ∩ list_bots 存活 bot 取交集 + 按 owner_id 聚合
- **Files**: `src/agentclaw/community/core/task/task_discovery/discovery_service.py`
- **Implementation**:
  - Step 1: `reader.read_pending_tasks()` → 提取唯一 `(bot_id, owner_id)`
  - Step 2: `bot_service.list_bots()` → 存活 bot_id 集合
  - Step 3: 交集过滤
  - Step 4: 按 owner_id 去重，同一 owner 只取第一个 bot
  - TODO 注释: 未来通过 dream mode 接口进一步过滤
- **Validation**: `test_02_scheduled_trigger` PASSED — total_discovered=1
- **Dependencies**: T2

---

## T4: /discovery/status 改造

- **Goal**: 关联 `_discoveries` 内存字典，返回 session 信息
- **Files**:
  - `src/agentclaw/community/core/task/task_discovery/discovery_service.py` — 新增 `get_discovery_result(task_id)`
  - `src/agentclaw/community/adapters/http/task/router.py` — `get_discovery_status()` 改造
- **Implementation**:
  - `DiscoveryService.get_discovery_result(task_id)` → 从 `_discoveries` 字典查
  - status 接口注入 `service: DiscoveryService = Injected(DiscoveryService)`
  - 每个 task entry 增加 `discovered/session_id/session_url/notification_sent/error`
  - 顶层增加 `discovered` 统计数
- **Validation**: `test_discover_then_status` PASSED — before discovered=0 → discover → after discovered=1
- **Dependencies**: T1

---

## T5: 钉钉卡片通知 e2e 测试

- **Goal**: 验证钉钉 SDK 发交互卡片 + 串联 discover → session → 钉钉卡片
- **Files**: `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
- **Implementation**:
  - `_FRONTEND_URL` — 固定前端首页 URL，可通过 env var 覆盖
  - `_build_card_data(workitem_name, workitem_bg, session_url)` — 用 `json.dumps` 构建 card_data
  - `_send_dingtalk_card(card_data, ak_id, ak_secret, robot_code, card_template_id, account_id, card_biz_id)` — 封装钉钉 SDK 调用
  - `test_03_dingtalk_card` — 独立发卡片（静态 card_data）
  - `test_04_dingtalk_discover_e2e` — 端到端：discover → 拿 session_url → 用 mock task 的 project_name/description 构建 card_data → 发卡片
- **SDK 依赖**: `antdingopensdk==1.0.46`（内网源）
- **Validation**: test_03 + test_04 PASSED — 钉钉返回 processQueryKey
- **Dependencies**: T1

---

## T6: 测试选取第一个 ACTIVE bot

- **Goal**: setUp 从取 `bots[0]` 改为取第一个 `status=ACTIVE` 的 bot
- **Files**:
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **Implementation**:
  ```python
  active_bots = [b for b in bots if b.get("status") == "ACTIVE"]
  bot = active_bots[0] if active_bots else bots[0]
  ```
- **Validation**: 两个测试文件全部 PASSED
- **Dependencies**: 无

---

## T7: test_discover_then_status HTTP 接口测试

- **Goal**: 新增 `TestDiscoveryStatusE2E.test_discover_then_status`
- **Files**: `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **Implementation**:
  - Step 1: GET /status（discover 前）— discovered=False（或跳过 if 上次结果仍在内存）
  - Step 2: POST /discover
  - Step 3: GET /status（discover 后）— discovered=True + session_id 非空
- **Validation**: PASSED
- **Dependencies**: T4

---

## T8: 测试 URL 路径同步

- **Goal**: test_cron_scheduler_e2e.py 的 URL 从 `/api/public/task-discovery/*` 改为 `/api/v1/collaboration/tasks/discovery/*`
- **Files**: `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
- **Implementation**: 6 处 replace_all
- **Validation**: test_01 + test_02 不再 404
- **Dependencies**: T1

---

## T9: spec 文档

- **Goal**: 汇总当天所有改动到 `specs/2026-08-20-task-discovery-endpoints-and-dingtalk-notify/`
- **Files**: `spec.md` + `design.md` + `tasks.md`
- **Dependencies**: T1-T8

---

## 验证命令汇总

```bash
# 调度 + 触发 + 钉钉全部
SINGLEBOX_CRON_E2E=1 SINGLEBOX_DINGTALK_E2E=1 \
SINGLEBOX_DINGTALK_AK_ID="..." SINGLEBOX_DINGTALK_AK_SECRET="..." \
SINGLEBOX_DINGTALK_ROBOT_CODE="..." SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID="..." \
SINGLEBOX_USER_ID="440718" \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py -s -v

# discover + status
SINGLEBOX_TASK_E2E=1 SINGLEBOX_USER_ID=440718 \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s -v
```