# discover session_url 修复 + 钉钉通知增强 + title [DreamMode] 标记

## 概述

修复任务发现流程中 session_url 格式不兼容前端路由的问题，增强钉钉卡片通知（session_url 直达 session），并给 discover session 的 title 加上 `[DreamMode]` 前缀。新增端到端测试脚本支持多 session 创建和命令行参数。

**关联文档**：
- `2026-08-20-task-discovery-endpoints-and-dingtalk-notify/spec.md` — 前置 spec
- `2026-08-19-task-discovery-backend-scheduled-initiation/spec.md` — 调度器 spec

---

## 需求列表

### REQ-1: 修复 session_url 格式 — 对齐前端路由

- **描述**：`session_initiator.py` 的 `_build_session_url` 生成的 URL 格式为 `/bcn/chat/session?bot_uuid=xxx&id=xxx&session=xxx`，前端实际路由是 `/assistant?botId=xxx&sessionId=xxx`，导致钉钉卡片点击跳转 404
- **验收标准**：
  - URL 改为 `{frontend_url}/assistant?botId={agent_id}&sessionId={session_id}`
  - `sessionId` 参数需 URL encode（session id 含冒号，如 `agent:main:cron_001`）
  - `frontend_url` 通过 `FRONTEND_URL` 环境变量配置，默认 `http://localhost:8000`
  - singlebox 启动时设置 `FRONTEND_URL=http://agentclaw-local.stable.alipay.net:8000`
- **改动文件**：
  - `src/agentclaw/community/core/task/task_discovery/session_initiator.py` — `_build_session_url` 方法
- **状态**：已完成

### REQ-2: discover session title 加 [DreamMode] 前缀

- **描述**：任务发现创建的 session title 需要带 `[DreamMode]` 前缀以区分普通工作 session
- **验收标准**：
  - 单任务时 title = `[DreamMode] {project_name}`（如 `[DreamMode] 存储行业尽调报告`）
  - 多任务时 title = `[DreamMode] 发现 N 件可能有意义的事情`
  - engine `POST /api/sessions` 创建时 title 不生效，需创建后通过 `POST /api/sessions/{id}/update?title=xxx` 单独更新
  - title 更新失败不阻断主流程（non-fatal，仅 log warning）
- **改动文件**：
  - `src/agentclaw/community/core/task/task_discovery/session_initiator.py` — title 构造 + Step 2.5 update 调用
- **状态**：已完成

### REQ-3: 钉钉卡片通知带 session_url 直达

- **描述**：钉钉卡片的 `session_url` 字段应传 discover 创建的 session 链接，用户点击直接跳转到该 session，而非前端首页
- **验收标准**：
  - `card_data` 的 `session_url` 字段使用 discover 返回的 session_url
  - 钉钉通知用户 ID 可通过参数指定（`--notify-user`），默认用 bot owner_id
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py`（新增）
- **状态**：已完成

### REQ-4: 新增端到端测试脚本 test_discover_and_notify_e2e.py

- **描述**：新增独立脚本，完整验证 discover → session 创建 → 钉钉通知 → 额外 session 创建的端到端流程
- **验收标准**：
  - 支持 `python test_discover_and_notify_e2e.py` 直接运行
  - 支持 `pytest test_discover_and_notify_e2e.py -s -v` 运行
  - 命令行参数：
    - `--bot-id=xxx` — 指定目标 bot_id，不传取第一个 ACTIVE bot
    - `--notify-user=xxx` — 钉钉通知用户 ID，默认用 bot owner_id
  - 流程：
    1. 查 bot → 写 mock 任务数据
    2. `POST /api/v1/collaboration/tasks/discovery/scheduled-trigger` → discover
    3. 拿到 session_id + session_url
    4. 构建带 session_url 的 card_data → 发钉钉卡片
    5. 每隔 5 秒创建 1 个新 session（共 5 个，模拟正常工作 session）
  - 所有 session_id 显示为 `agent:main:xxx` 格式
  - 额外 session 使用具体工作标题（存储行业最新动态跟踪、竞品技术方案对比分析等）
- **改动文件**：
  - `tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py`（新增）
- **状态**：已完成

---

## 环境配置

| 项 | 值 |
|----|-----|
| `FRONTEND_URL` | `http://agentclaw-local.stable.alipay.net:8000`（singlebox 启动时 export） |
| `SINGLEBOX_CRON_E2E` | `1` |
| `SINGLEBOX_DINGTALK_E2E` | `1` |
| `SINGLEBOX_DINGTALK_AK_ID` | 钉钉 AK ID |
| `SINGLEBOX_DINGTALK_AK_SECRET` | 钉钉 AK Secret |
| `SINGLEBOX_DINGTALK_ROBOT_CODE` | 钉钉机器人 code |
| `SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID` | 钉钉卡片模板 ID |
| `SINGLEBOX_USER_ID` | 操作用户 ID |

---

## 技术决策

### 为什么 title 需要单独 update？

engine（OpenClaw）的 `POST /api/sessions` 接口在接收 body 中的 `title` 字段后，实际返回的 title 是 session_key（如 `agent:main:cron_001`），忽略了 body 中的 title。这是 engine 端的历史行为，无法从 backend 侧修改。

解决方案：session 创建成功后，通过 engine 的 `POST /api/sessions/{session_id}/update?title=xxx` 接口单独更新 title。注意此调用不能用 `cron_relay.forward_request`（relay 只处理 `/api/cron*` 路径），需直接用 httpx 调 engine target。

### 为什么 sessionId 需要 URL encode？

session id 格式为 `agent:main:cron_001`，含冒号。在 URL query string 中冒号需要 encode 为 `%3A`，否则部分前端路由解析器会出错。使用 `urllib.parse.quote(session_id, safe="")` 编码。