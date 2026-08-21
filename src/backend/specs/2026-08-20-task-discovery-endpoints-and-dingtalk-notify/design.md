# Design — task_discovery HTTP 端点补齐 + 钉钉卡片通知 e2e + discover_all_bots 改造

## 背景

`2026-08-19` spec 的 Slice 1-7 完成了底层模块（scheduler / discovery_service / session_initiator / DI），但 Slice 8（HTTP 端点）未实现，导致 e2e 测试 404。同时 `discover_all_bots()` 全量遍历所有 bot 的逻辑不适合预发/正式环境。

本文档记录 2026-08-20 的设计决策。

---

## D1: 端点路径选择

### 问题

spec REQ-6 规划的路径是 `/api/public/task-discovery/*`（独立 router），但现有 discovery 端点在 `/api/v1/collaboration/tasks/discovery/*`（task router）下。

### 决策

端点并入现有 `task/router.py` 的 `router`（prefix `/api/v1/collaboration/tasks`），不新建独立 router。

```
GET  /api/v1/collaboration/tasks/discovery/scheduler-status
POST /api/v1/collaboration/tasks/discovery/scheduled-trigger
```

**理由**：
- 与 `/discovery/discover`、`/discovery/status` 同命名空间，统一管理
- 省去 `app.py` 的 import + include_router
- 用户明确要求"去掉 /api/public，直接 /discovery/..."

### 响应格式

两端点返回**扁平 JSON**（不套 Envelope），因为 e2e 测试直接读顶层字段：
```json
{"success": true, "running": true, "jobs": [...]}
{"success": true, "total_discovered": 1, "results": [...]}
```

而 `/discovery/discover`、`/discovery/status` 保持 Envelope 格式（`{code, message, data}`）——它们是原有端点，不改格式。

---

## D2: db 路径级数错位

### 问题

三个文件用 `Path(__file__).resolve()` 上溯定位 `discovered_tasks.db`，但级数不同：

| 文件 | 层级 | 上溯级数 | 落到 |
|---|---|---|---|
| `router.py` | `adapters/http/task/` | 9 | `ocb-public/` ✓ |
| `task_discovery_module.py` | `di/modules/` | 9 (bug) | `ocb/` ✗ |
| `test_cron_scheduler_e2e.py` | `tests/.../singlebox_e2e/` | 8 | `ocb-public/` ✓ |

`task_discovery_module.py` 在 `di/modules/` 下，比 `router.py` 浅一级，9 级上溯跑到了 `ocb-public` 的父目录 `ocb`。

### 决策

`task_discovery_module.py` range(9) → range(8)，三方路径统一到 `ocb-public/scripts/.dependencies/data/discovered_tasks.db`。

### 曾考虑但放弃的方案

- **env var `TASK_DISCOVERY_DATA_FILE`**（镜像 `DATABASE_URL` 模式）：启动脚本设 env var，代码只读 env。放弃了——启动脚本（singlebox.sh / backend.sh / local_setup.sh 三处）改动多、且 singlebox restart 的 inline env 与测试 shell 的 export 不在同一进程，需手动对齐。
- **系统 temp 目录**：`tempfile.gettempdir()/ocb_task_discovery.db`。放弃了——用户要求"保持原方案放 dependences"。

---

## D3: discover_all_bots 改造

### 问题

原实现 `list_bots(page=1, page_size=100)` 遍历所有 bot，预发/正式环境会误跑所有 bot。

### 决策

三步过滤：

```
Step 1: db pending tasks → 提取唯一 (bot_id, owner_id)
Step 2: list_bots() → 存活 bot 集合
Step 3: 取交集 → 只对 db 和 live 都有的 bot 执行
Step 4: 按 owner_id 聚合 → 同一 owner 只取第一个 bot
```

**Step 1-3**：db 无数据 → 空交集 → 不触发。天然避免预发/正式环境误跑。

**Step 4**：同一 owner 有多个 bot 时，只取第一个执行发现——因为 task_discovery 的场景是"一个助理 bot 帮用户发现待办"，不需要每个 bot 都跑。

### 未来演进

```
当前: db ∩ list_bots → owner 聚合（取第一个）
未来: db ∩ list_bots → dream mode 过滤 → per-bot 调度
```

dream mode 接口将标记哪些 bot 开启了任务发现（`TaskDiscoveryScheduler.enable_for_bot(bot_id, owner_id)`），替代"取第一个"的临时策略。

---

## D4: /discovery/status 改造

### 问题

原 status 只返回 db task 列表（task_id/project_name/status/priority），不反映 discover 执行结果。

### 决策

关联 `DiscoveryService._discoveries` 内存字典（`task_id → DiscoveryResult`），不写 db：

```python
result = service.get_discovery_result(task.task_id)
if result:
    entry["discovered"] = True
    entry["session_id"] = result.session.session_id
    entry["session_url"] = result.session.session_url
else:
    entry["discovered"] = False
```

**不写 db 的理由**：
- `DiscoveredTask` 是 frozen dataclass，改 schema 影响面大
- session 信息是 discover 的"运行时结果"，不是"被发现的任务数据"
- 内存够用——status 是"看刚刚跑的 discover 结果"，不是历史档案

### 已知限制

后端重启后 `_discoveries` 清空，status 里所有 task 的 `discovered` 回到 False。TODO: 考虑持久化。

---

## D5: 钉钉卡片通知

### 架构定位

```
DiscoveryService._send_notification()
  → NotifyMessage(extra={channel:"tc_card", card_template_id, card_biz_id, card_data})
  → NotifySenderPlugin.send(message)

当前: CommunityNotifySender → 只写日志
未来: DingTalkNotifySender → 调钉钉 SDK 发卡片
```

当前在测试脚本中手动调 SDK，不下沉到插件实现——先把链路验证通，正实化是下一步。

### card_data 构建策略

| 字段 | 来源 | 说明 |
|---|---|---|
| `session_url` | 固定前端首页 URL | 不取 discover 返回的 localhost 链接，用户点击进工作台首页 |
| `workitem_name` | mock task 的 `project_name` | 从 discover 发现的任务数据动态取 |
| `workitem_bg` | mock task 的 `description` | 同上 |
| `card_biz_id` | `discover_e2e_{bot_id}_{timestamp}` | 唯一标识，用于幂等去重 |

### 钉钉 SDK 版本

`antdingopensdk==1.0.46`（内网 `pypi.antfin-inc.com/simple/`），不是 PyPI 上的 `0.0.9`——后者缺 `SendRobotInteractiveCardRequest`。

---

## D6: 测试选 bot 策略

同一 owner 5 个 bot 都是 ACTIVE，setUp 需要确定选哪个：

```python
active_bots = [b for b in bots if b.get("status") == "ACTIVE"]
bot = active_bots[0] if active_bots else bots[0]
```

**理由**：取第一个 ACTIVE 的，不假设 `bots[0]` 永远是 ACTIVE（fresh start 后可能 PENDING）。

---

## 文件改动矩阵

| 文件 | D1 端点 | D2 路径 | D3 all_bots | D4 status | D5 钉钉 | D6 选bot | D7 status测试 |
|---|---|---|---|---|---|---|---|
| `router.py` | ✓ | | | ✓ | | | |
| `discovery_service.py` | | | ✓ | ✓ | | | |
| `task_discovery_module.py` | | ✓ | | | | | |
| `test_cron_scheduler_e2e.py` | ✓ | | | | ✓ | ✓ | |
| `test_task_discovery_e2e.py` | | | | | | ✓ | ✓ |