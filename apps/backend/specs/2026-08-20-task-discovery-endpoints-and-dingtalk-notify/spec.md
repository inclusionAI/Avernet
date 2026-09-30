# task_discovery HTTP 端点补齐 + 钉钉卡片通知 e2e + discover_all_bots 改造

## 概述

在 `2026-08-19-task-discovery-backend-scheduled-initiation` 的基础上，补齐 HTTP 端点、修复 db 路径错位、改造 `discover_all_bots` 逻辑、新增钉钉卡片通知端到端测试。

**关联文档**：
- `2026-08-19-task-discovery-backend-scheduled-initiation/spec.md` — 前置 spec
- `2026-08-19-task-discovery-backend-scheduled-initiation/design.md` — 技术设计

---

## 需求列表

### REQ-1: 新增 scheduler-status / scheduled-trigger 端点

- **描述**：spec REQ-6 规划的 `/api/public/task-discovery/*` 端点未实现，e2e 测试 404
- **验收标准**：
  - `GET /api/v1/collaboration/tasks/discovery/scheduler-status` — 返回 APScheduler 状态（running/jobs/cron/timezone/auto_start），扁平 JSON
  - `POST /api/v1/collaboration/tasks/discovery/scheduled-trigger` — 触发 `discover_all_bots()`，返回 `total_discovered`/`results[]`，扁平 JSON
  - 两端点挂在现有 `task/router.py` 的 `router`（prefix `/api/v1/collaboration/tasks`）下，与 `/discovery/discover`、`/discovery/status` 同命名空间
  - 通过 `Injected()` 注入 `TaskDiscoveryScheduler` / `DiscoveryService`
- **改动文件**：
  - `src/agentclaw/community/adapters/http/task/router.py` — 新增 2 个端点 + import `TaskDiscoveryScheduler`
- **状态**：已完成

### REQ-2: 修复 db 路径级数错位

- **描述**：`task_discovery_module.py` 的 `_PROJECT_ROOT = Path(__file__).resolve()` 上溯 9 级落到 `ocb`（父目录），导致 DI 注入的 reader 读 `/ocb/scripts/.dependencies/...`，而测试和 router 读 `/ocb-public/scripts/.dependencies/...`，路径不一致
- **验收标准**：
  - `task_discovery_module.py` range(9) → range(8)，与 `router.py`（9 级，文件更深）和测试（8 级）解析到同一 db 路径
  - 三方路径完全一致：`ocb-public/scripts/.dependencies/data/discovered_tasks.db`
- **改动文件**：
  - `src/agentclaw/community/di/modules/task_discovery_module.py` — range(9) → range(8)
- **状态**：已完成

### REQ-3: 改造 discover_all_bots — db ∩ list_bots 交集 + owner 聚合

- **描述**：原 `discover_all_bots()` 用 `list_bots()` 全量遍历所有 bot，预发/正式环境会误跑所有 bot。改为从 db pending tasks 提取 bot 集合，与 `list_bots()` 存活 bot 取交集，再按 owner_id 聚合（同一 owner 只取第一个 bot）
- **验收标准**：
  - db 无 pending tasks → 直接返回空（预发/正式环境天然不触发）
  - db ∩ list_bots 交集 → 只对两边都有的 bot 执行发现
  - 按 owner_id 聚合 → 同一 owner 只取第一个 bot，避免重复发现
  - 日志输出 db/live/intersection/after aggregation 各阶段 bot 数量
- **改动文件**：
  - `src/agentclaw/community/core/task/task_discovery/discovery_service.py` — `discover_all_bots()` 重写
- **状态**：已完成

### REQ-4: 改造 /discovery/status — 关联 _discoveries 内存 + session 信息

- **描述**：原 `/discovery/status` 只返回 db 里的 task 列表（task_id/project_name/status/priority），不反映 discover 执行结果和 session 状态
- **验收标准**：
  - status 返回每个 task 的 `discovered`（true/false）、`session_id`、`session_url`、`notification_sent`、`error`
  - session 信息从 `DiscoveryService._discoveries` 内存字典取（不写 db，后端重启会丢）
  - 顶层新增 `discovered` 统计数
  - `DiscoveryService` 新增 `get_discovery_result(task_id)` 公开方法
- **改动文件**：
  - `src/agentclaw/community/core/task/task_discovery/discovery_service.py` — 新增 `get_discovery_result()`
  - `src/agentclaw/community/adapters/http/task/router.py` — `get_discovery_status()` 改造
- **状态**：已完成

### REQ-5: 钉钉卡片通知 e2e 测试

- **描述**：验证钉钉 SDK 交互卡片发送，串联 discover → session 创建 → 钉钉卡片投递
- **验收标准**：
  - `test_03_dingtalk_card` — 独立调钉钉 SDK 发卡片（静态 card_data），验证 SDK 参数可用
  - `test_04_dingtalk_discover_e2e` — 端到端：scheduled-trigger → discover → 拿 session_url → 用 mock task 的 project_name/description 构建 card_data → 调钉钉 SDK 发卡片
  - card_data 的 `session_url` 固定用前端首页 URL（`http://agentclaw-local.stable.alipay.net:8000/assistant`），不取 discover 返回的 localhost 链接
  - `workitem_name`/`workitem_bg` 从 mock task 的 `project_name`/`description` 动态取
  - `account_id` 用 `owner_id`（从 bot 查询结果取）
  - 钉钉 SDK 依赖：`antdingopensdk==1.0.46`（内网 `pypi.antfin-inc.com/simple/`）
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py` — 新增 test_03/test_04 + `_build_card_data()` + `_send_dingtalk_card()` + `_FRONTEND_URL`
- **状态**：已完成

### REQ-6: 测试选取第一个 ACTIVE bot

- **描述**：同一 owner 有多个 bot 时，setUp 盲取 `bots[0]` 不可靠（可能不是 ACTIVE）
- **验收标准**：
  - 两个测试文件的 setUp 都改为取第一个 `status=ACTIVE` 的 bot
  - fallback：没有 ACTIVE 则取 `bots[0]`
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
- **状态**：已完成

### REQ-7: test_discover_then_status HTTP 接口测试

- **描述**：新增 HTTP 接口测试：POST /discover → GET /status 验证 session 关联
- **验收标准**：
  - 先查 status（discovered=False 或跳过 if 上次结果仍在内存）
  - POST /discover 触发发现
  - 再查 status（discovered=True + session_id 非空）
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py` — 新增 `TestDiscoveryStatusE2E`
- **状态**：已完成

### REQ-8: 测试 URL 路径同步

- **描述**：test_cron_scheduler_e2e.py 的 URL 从 `/api/public/task-discovery/*` 改为新路径 `/api/v1/collaboration/tasks/discovery/*`
- **验收标准**：
  - 6 处 URL 替换（2 docstring + 2 请求 URL + 顶部说明）
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
- **状态**：已完成

---

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `adapters/http/task/router.py` | +scheduler-status/scheduled-trigger 端点；/discovery/status 改造（关联 _discoveries + session 信息） |
| `core/task/task_discovery/discovery_service.py` | discover_all_bots 重写（db∩list_bots 交集 + owner 聚合）；新增 get_discovery_result() |
| `di/modules/task_discovery_module.py` | range(9)→range(8) 修复 db 路径级数错位 |
| `tests/.../test_cron_scheduler_e2e.py` | URL 改路径；新增 test_03/test_04 钉钉卡片；setUp 取 ACTIVE bot |
| `tests/.../test_task_discovery_e2e.py` | 新增 TestDiscoveryStatusE2E；setUp 取 ACTIVE bot |

## SDK 依赖

| 包 | 版本 | 安装源 |
|---|---|---|
| antdingopensdk | 1.0.46 | `pypi.antfin-inc.com/simple/` |
| alibabacloud_tea_openapi | 0.3.10 | PyPI |
| alibabacloud_endpoint_util | 0.0.3 | PyPI |
| alibabacloud_tea_util | latest | PyPI |
| alibabacloud_openapi_util | 0.2.2 | PyPI |

## 验证命令

```bash
# 调度 + 触发测试
cd ocb-public/src/backend
SINGLEBOX_CRON_E2E=1 SINGLEBOX_USER_ID=440718 \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py -s -v

# 全部含钉钉
SINGLEBOX_CRON_E2E=1 SINGLEBOX_DINGTALK_E2E=1 \
SINGLEBOX_DINGTALK_AK_ID="..." SINGLEBOX_DINGTALK_AK_SECRET="..." \
SINGLEBOX_DINGTALK_ROBOT_CODE="..." SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID="..." \
SINGLEBOX_USER_ID="440718" \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py -s -v

# discover + status
SINGLEBOX_TASK_E2E=1 SINGLEBOX_USER_ID=440718 \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s -v
```

## TODO

- `discover_all_bots` 的 owner 聚合当前只取"第一个"，未来应通过 dream mode 接口确定哪个 bot 负责任务发现
- 钉钉通知目前仅在测试脚本中手动调 SDK，未来需下沉到 `DingTalkNotifySender` 插件实现
- `/discovery/status` 的 session 信息从内存读取，后端重启会丢；考虑持久化到 db
- SDK 依赖需加到 `pyproject.toml` 持久化（当前仅在 venv 里手动安装）

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-20 | 初始创建 — 汇总当天所有 task_discovery 改动 |