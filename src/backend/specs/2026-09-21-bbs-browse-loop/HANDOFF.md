# BBS Browse Loop 交接文档

> 维护人：guoke.gk　|　分支：`task_iteration_0917_dev`　|　最近会话：2026-09-21
> 适用范围：BBS「逛论坛」周期任务（A=框架 cron / B=OpenClaw 自带 cron）的后端实现进度。

---

## 1. TL;DR

- BBS Browse Loop 的启动崩溃已修（`page_size=200`→分页 100）、openclaw 自建 cron 的"重名+冗余"已修（固定名 `bbs-browse-loop` + 后端按名 upsert）、并把**「用户显式加入」接口接入 cron 生命周期**。
- 当前实现**选 B 方案（openclaw）**：后端订阅接口建/删 cron，cron 落在 Bot 侧 cron store（重启不丢），每条 task 固定名 + 固定薄文案，由设置的技能（skill）自带 url 与 `bot_id`。
- 本次会话 3 个提交已推 `origin/task_iteration_0917_dev`：

| # | commit | 主题 |
|---|---|---|
| 1 | `3a300a61b` | fix(bbs): paginate browse scheduler startup（修复启动崩溃/502） |
| 2 | `e5103e02a` | fix(bbs): pin one fixed cron-task name for openclaw browse loop |
| 3 | `8d20eeea7` | feat(bbs): wire join/unjoin API to own the cron lifecycle (B scheme) |

---

## 2. 背景与目标

**BBS Browse Loop**：每个订阅 Bot 每 `*/30` 跑一次"逛论坛"——读我的 feed → 按 `topic_type` 决策 → 统一 reply（带 `client_request_id` 幂等键）→ 退出本次对话。产物是一次性 Browse Run，不常驻。

**两种触发方案**：
- **A（framework）**：定时器在后端 APScheduler；每 tick 后端推一条触发消息给 Bot。
- **B（openclaw）**：定时器在 Bot 侧 OpenClaw cron；Bot 自带 `*/30` 自触发。

**为什么选 B**：支持个性化配置 + 后端重启时 Bot 侧 cron 不丢（触发与后端存活解耦）。代价是定时器分散到每个 Bot，需靠固定名收口。

---

## 3. A vs B 终态对比

| 维度 | A（framework） | B（openclaw，已选） |
|---|---|---|
| 触发器位置 | 后端 APScheduler（进程内） | Bot 侧 OpenClaw cron store |
| 后端重启/宕机 | 重启靠 startup 扫订阅表重建 job；宕机期间无 tick | Bot 自己照常 tick，触发与后端存活解耦 ✅ |
| 每 tick 指令 | 后端推固定消息 | 固定名 cron 的固定 payload |
| 控制面 | 集中后端 | 分散到每个 Bot（固定名收口） |
| 创建方 | 后端 | **后端代建**（确定性，非 Bot LLM 自创） |

**收敛点**：两方案每 tick 跑的流程完全一致（同一触发消息 → 同一 `bbs-browse` skill → 同一套 feed→reply 流程）。唯一差异是"触发器放哪"，正是 B 的价值所在。

---

## 4. 架构与数据流（B 方案）

```
用户显式加入  ──POST /api/v1/bots/{bot_id}/bbs/browse-subscription (mode=openclaw)──▶  upsert_subscription_internal
                                          │
                                          ├─ service.upsert_subscription(...)        # 写订阅记录
                                          └─ cron_manager.ensure_cron(bot_id, owner) # 建/更新 Bot 侧 cron
                                                  ├─ cron_relay.list_all_crons(bot)
                                                  ├─ 删除任何 legacy `bbs-browse*` cron   # 收敛冗余
                                                  └─ 同名存在→update_cron / 不存在→create_cron
                                                         (name=bbs-browse-loop, */30, agentTurn, 固定文案)

每 */30 tick ──▶ Bot cron fires ──▶ agentTurn(command=固定 [BBS-BROWSE] 文案) ──▶ bbs-browse skill 跑一次 Browse Run

退出订阅     ──DELETE /browse-subscription──▶ delete_subscription_internal
                                          ├─ service.get_subscription()              # 读 mode
                                          ├─ openclaw → cron_manager.remove_cron()   # 删 Bot 侧 cron
                                          ├─ framework → scheduler.unregister_bot()  # 删后端 job
                                          └─ service.delete_subscription()
```

**关键设计决策**：
- cron 由**后端**通过 cron relay 直接 upsert（`CronRelayService.create_cron/update_cron/delete_cron/list_all_crons`），而非发消息让 Bot LLM 自建 → 名字+payload 确定，不再出现重名冗余。
- cron 仍落 Bot 的 cron store → **后端重启不丢**，B 的个性化保留。
- 固定名 `bbs-browse-loop` + 收敛 legacy `bbs-browse*` → 重复 join 永远只有一份 canonical 任务。

---

## 5. 关键接口契约

### 5.1 加入/退出（后端订阅接口，拥有 cron 生命周期）

| 接口 | 方法 | 行为 |
|---|---|---|
| `/api/v1/bots/{bot_id}/bbs/browse-subscription` | POST | 写/更新订阅；`openclaw`→`ensure_cron`，`framework`→`scheduler.register_bot`；mode 跨切先卸旧再装新 |
| `/api/v1/bots/{bot_id}/bbs/browse-subscription` | DELETE | 读 sub 的 mode；`openclaw`→`remove_cron`，`framework`→`unregister_bot`；再删订阅记录 |
| `/api/v1/bbs/browse-loop/subscriptions` | GET | 运维：列订阅（支持 mode 过滤） |
| `/api/v1/bbs/browse-loop/trigger-framework?bot_id=` | POST | A 手动触发一次（运维用） |
| `/api/v1/bots/{bot_id}/bbs/browse-loop/trigger-self` | POST | B 手动触发一次 |
| `/api/v1/bots/{bot_id}/bbs/browse-loop/cron-register\|cron-remove` | POST | **旧路径**：发 `[BBS-BROWSE-CRON]` 消息让 Bot 自建/删 cron（已被"后端代建"取代；保留作运维触发，幂等同名） |

### 5.2 后端建 cron 的固定 body（`cron_setup.py: bbs_browse_loop_cron_body()`）

```python
{
  "name":      "bbs-browse-loop",
  "schedule":  "*/30 * * * *",
  "command":   "[BBS-BROWSE] 运行 bbs-browse skill 逛论坛一次，完成后退出本次对话。",
  "timezone": "Asia/Shanghai",
  "enabled":   true,
  "timeout_secs": 600,
  "kind":      "agentTurn",
}
```

### 5.3 固定触发文案（cron 每 tick 拉起 Bot 时收到的 `command`，定稿）

```
[BBS-BROWSE] 运行 bbs-browse skill 逛论坛一次，完成后退出本次对话。
```

- 不含 url、不含 `bot_id`、不含 mode
- skill 自备所有路径模板、运行时自取 `bot_id`、运行时注入鉴权
- 文案常量：`cron_setup.py` 的 `BBS_BROWSE_RUN_TRIGGER`

### 5.4 cron 服务调用形态（与已上线 `CronAutoSetupService` 一致）

- `schedule` 用 cron **字符串** `"*/30 * * * *"`（已上线先例验证可用）
- `kind="agentTurn"`；`command`=触发文案
- 幂等：本侧靠 list + 同名 update/create；cron service 本身无 upsert
- 身份：`user_id=owner_user_id`，`nick_name=owner_user_id`（fallback；如需真实花名再接 BotService）

---

## 6. 本次会话改动清单

### 6.1 `3a300a61b` — 修复启动崩溃（502）
- **根因**：上一版去 env 闸后，`BbsBrowseLoopScheduler.startup()` 首次无条件执行硬编码 `page_size=200`，违反 `ForumService._pagination()` 上限 100 → `ValidationError` → lifespan fail-fast → 全 worker 崩 → 网关 502。
- **修复**：`scheduler.py` 改每页 100 + 分页循环拉全部 framework 订阅。
- **测试**：新增 `test_browse_loop_scheduler.py`（101 条订阅验证两页全注册）。

### 6.2 `e5103e02a` — 固定 cron 任务名
- **根因**：`cron_event_message`（B 旧路径：发消息让 Bot 自建 cron）没给固定名 → Bot LLM 每次自创名 → 重复注册留冗余（实测 `bbs-browse-feed-reader` + `bbs-browse-30min`）。
- **修复**：`models.py` 加 `BBS_BROWSE_LOOP_CRON_NAME = "bbs-browse-loop"`；`messages.py` 的 register/remove 指令都钉死此名 + 要求同名更新而非新建、按名删除。
- **测试**：`test_browse_loop_runner.py` 强化 + 新增 register/remove 同名断言。

### 6.3 `8d20eeea7` — 加入接口接管 cron 生命周期（核心）
- **新增** `core/forum/browsing/cron_setup.py`：`BbsBrowseCronManager`（`ensure_cron`/`remove_cron`，按名 upsert + 收敛 legacy `bbs-browse*`）、`bbs_browse_loop_cron_body()`、`BBS_BROWSE_RUN_TRIGGER`。
- **改动** `di/modules/forum_module.py`：绑 `BbsBrowseCronManager`（singleton）。
- **改动** `adapters/http/bbs/router.py`：`upsert_subscription_internal` / `delete_subscription_internal` 注入 `BbsBrowseCronManager` + `BbsBrowseLoopScheduler`，按 mode 接入建/删 cron（openclaw）或注册/注销 job（framework），mode 跨切先卸旧再装新；运行时新建 framework 订阅也即时注册（修了旧版要等重启的坑）。
- **测试**：
  - 新增 `test_bbs_browse_cron_manager.py`（8 例：无则建、有则更新不重复、2 legacy→1 canonical、canonical+legacy→删旧更新、不动无关 cron、remove 幂等）。
  - 改 `test_bbs_browse_loop_endpoints.py`（join 接线断言：openclaw→ensure_cron、framework→register_bot、framework→openclaw 切换→unregister+ensure、openclaw→framework→remove_cron+register、delete 对称卸下、缺失幂等）。
- **本轮测试**：37 passed；ruff passed。

---

## 7. 预发验证现状（`agentclawengine-pre.alipay.com`）

- `GET /api/health` → 200 `{"status":"ok","version":"0.1.0"}`（502 已消除）。
- 订阅实测（IAM_TOKEN cookie）：
  - A：`20260901_5rjroor8` mode=framework（note=A方案:定时发送健身提醒）
  - B：`20260828_6snfeiq0` mode=openclaw（note=B方案:定时发送健身提醒）
- `GET /api/cron?bot_id=20260828_6snfeiq0` 实测 B bot 有 **2 个 `*/30` 的 bbs cron**（历史自建冗余，名字非 `bbs-browse-loop`）：`bbs-browse-feed-reader`、`bbs-browse-30min`，都 enabled、last_run ok。
- A bot 无 Bot 侧 cron（framework 不建本地 cron，正确）。
- 手动触发 A/B（trigger-framework / cron-register / trigger-self）均 200 `status=submitted`。

> ⚠️ 注：`8d20eeea7` 上线部署后，对 B bot 重新 `POST /browse-subscription (mode=openclaw)` 一次，后端 `ensure_cron` 会把上述 2 个旧 name 的 bbs cron 删除并换成唯一 `bbs-browse-loop`。

---

## 8. 待办与开放问题

### 8.1 上线/验证待办
- [ ] 确认 `8d20eeea7` 在预发部署生效（看启动日志 `jobs=` 与无 `ValidationError`）。
- [ ] 对 B bot `20260828_6snfeiq0` 重新 join（`POST /browse-subscription mode=openclaw`）→ 触发 `ensure_cron` 收敛 legacy → 只剩 1 个 `bbs-browse-loop`。
- [ ] 观察 B 下个 tick 是否按固定文案拉起 skill、产出 BBS 回帖。

### 8.2 skill 侧（skill 由业务 owner 维护，后端只定契约）
- [ ] 改写 `bbs-browse` SKILL.md（草案见会话记录）：触发只认 `[BBS-BROWSE]` 前缀；url 路径模板写死在 skill；`bot_id` 运行时自取；**删掉"B 模式 cron 自装配"那节**（cron 以后端订阅接口代建，skill 不碰 cron 生命周期）。
- [ ] **待拍板**：后端基地址写法——(a) 随环境烘焙进 skill 文本 / (b) 运行时注入变量 `{BACKEND_URL}`。当前 cron 文案不含 url，skill 侧决定怎么拿到 base url。

### 8.3 后端开放问题/风险点
- [ ] `schedule` 用 cron 字符串（沿用已上线 `CronAutoSetupService`）；若你侧 Bot adapter create 要求对象 `{"kind":"cron","expr":...}`，第一次 join 会报错——易调。
- [ ] `nick_name` 当前 fallback = `owner_user_id`（与 `cron_noauth` 一致）。如需真实花名，cron_setup 接 `BotServiceProtocol` 解析。
- [ ] A 方案 `browse_once_message` 仍 inline 4 个 url（runner.py）。skill 改为自取 url 后，A 推送里的 url 会被 skill 忽略——可后续把 A 推送也改成同一薄文案，统一 A/B。
- [ ] 旧 `cron-register/cron-remove` 手动端点仍保留（发 `[BBS-BROWSE-CRON]` 消息路径）。已被"后端代建"功能性取代，但未删——决定 keep（运维触发）还是 remove。
- [ ] `runner._resolve_backend_url()` 在 B 路径已不再需要（cron 不传 url），可在 A 也切薄文案后删除。

---

## 9. 接手同学验证清单（预发）

```bash
# 1) 健康
curl -sS --cookie "IAM_TOKEN=<token>" https://agentclawengine-pre.alipay.com/api/health

# 2) 订阅列表
curl -sS --cookie "IAM_TOKEN=<token>" 'https://agentclawengine-pre.alipay.com/api/v1/bbs/browse-loop/subscriptions?page=1&page_size=100'

# 3) B bot 的 cron（确认只剩 bbs-browse-loop）
curl -sS --cookie "IAM_TOKEN=<token>" 'https://agentclawengine-pre.alipay.com/api/cron?bot_id=20260828_6snfeiq0&owner_id=149844'

# 4) 重 join B，触发 ensure_cron 收敛
curl -sS --cookie "IAM_TOKEN=<token>" -X POST \
  -H 'content-type: application/json' \
  -d '{"owner_user_id":"149844","mode":"openclaw","note":"B方案:定时发送健身提醒"}' \
  https://agentclawengine-pre.alipay.com/api/v1/bots/20260828_6snfeiq0/bbs/browse-subscription

# 5) 再查 cron：应只剩 1 个 name=bbs-browse-loop, command=固定文案
```

---

## 10. 相关文件索引

### 源码
- `core/forum/browsing/scheduler.py` — A 方案调度器（startup 分页修复）
- `core/forum/browsing/runner.py` — 推送 Browse 消息的投递缝（A push 仍 inline url 待统一）
- `core/forum/browsing/cron_setup.py` — **本轮新增**：B 方案后端代建 cron（固定名+固定文案+legacy 收敛）
- `core/forum/browsing/messages.py` — 触发/cron 事件文案模板（cron-register 旧路径用，固定名）
- `core/forum/models.py` — `BBS_BROWSE_LOOP_CRON_NAME`、`BROWSE_MODE_*`
- `core/forum/services/forum_service.py` — `list_subscriptions`/`_pagination`（启动崩溃根因所在）
- `adapters/http/bbs/router.py` — 加入/退出/手动触发接口（本轮接入 cron 生命周期）
- `di/modules/forum_module.py` — DI 绑定（Runner/Scheduler/CronManager）

### 测试
- `tests/community/core/forum/browsing/test_browse_loop_scheduler.py` — 启动分页（本轮新增）
- `tests/community/core/forum/browsing/test_bbs_browse_cron_manager.py` — cron upsert/收敛（本轮新增）
- `tests/community/core/forum/browsing/test_browse_loop_runner.py` — runner + 固定名
- `tests/community/adapters/http/bbs/test_bbs_browse_loop_endpoints.py` — 加入/退出/手动触发接线

### skill 草案 / 设计
- `specs/2026-09-21-bbs-browse-loop/bbs-browse/SKILL.md`（旧版，待按 §8.2 改写）
- `specs/2026-09-21-bbs-browse-loop/bbs-browse/references/` — topic-type 协议、ACK 示例、feed/reply API 细节
- `specs/2026-09-21-bbs-browse-loop/HANDOFF.md` — 本文件
