# BBS 主动触发执行链路设计(优化版:bid→select)

- Date: 2026-08-22
- Status: Approved(待 writing-plans)

## 1. 目标

升 BBS 落到可恢复态(`miss_depth_exhausted` → 根留 PLANNING + `bbs_mode`)后,不再被动等 relay 轮询,改为:

1. **广播 bid**:`send_and_wait_async`(一发一收) 向所有 dream-mode bot 发"评估消息"——让每个 bot 自评能完成多少剩余事项,回复 JSON(含 completion_rate + task_spec)。等待 ≤ 3 分钟。
2. **选胜**:从回复的 bot 里选 completion_rate 最高的。
3. **claim + 派发**:引擎为选中的 bot 占根(`claim_bbs_owner`),然后给它发"任务消息"(含 task_id + backend_url + 它自己的 bot_id + 它在 bid 里提的 task_spec)。
4. **接力执行**:选中 bot 装新 skill,跳过 claim(②) 和自判(③),直接 attach(④) + execute(⑤) + result(⑥)。

## 2. 关键决策

| 决策 | 选择 | 说明 |
|---|---|---|
| 触发条件 | 可恢复态(`miss_depth_exhausted` → 根留 PLANNING) | HUNG 根 attach 9 → 不触发 |
| 触发方式 | `asyncio.create_task` fire-and-forget | 拦截在 `_maybe_propagate_hung` 锁内;run_bbs 异步 IO 不持锁 |
| 评估方式 | `send_and_wait_async`(一发一收) | 每个 Dream bot 收到评估消息后自评并回复 JSON(含 completion_rate + task_spec) |
| 评估等待 | ≤ 3 分钟(`asyncio.wait_for(gather, timeout=180)`) | 超时后取已回复的;全无回复则放弃(留可恢复态) |
| 选胜 | completion_rate 最高的一个 | 平局取首个(roster 顺序稳定) |
| 占根 | 引擎服务端 `claim_bbs_owner(task_id, winner)` | 选中 Bot 的 attach 通过 owner 校验,skill 不需要 claim |
| task_spec 来源 | skill 自己从 dashboard 派生 | bid 回复只含 `{completion_rate}`;skill 读剩余事项(goal − 已 DONE 叶子)→ 组织 task_spec |
| roster 获取 | `list_bots_by_task_modes(dream=True, match="any")` | |
| 根再规划 | 无(守卫已核对) | §8 详述 |

## 3. 背景速览

- `_hung_and_escalate`(engine.py:659) 置 `bbs_mode=True`;`_maybe_propagate_hung` 可恢复拦截(engine.py:706-707 / 724-725,根留 PLANNING)。
- `OpenApiBotPort.send_and_wait_async(bot_id, message, timeout, poll_interval)` → 返 `{status, result{content}, error}`(ports.py:31;实现 singlebox_engine_adapter.py:132)。与 `send_message`(fire-and-forget)不同,这个等待 bot 最终回复(一发一收)。
- `claim_bbs_owner`(task_graph_service.py:351):CAS 占根,返 `NodeOpResult`;服务端可直调(不经 HTTP 路由)。
- `bbs/attach` 路由 → `attach_bbs_node`(task_graph_service.py:447):owner 校验(`root.bbs_owner==bot_id`)→ 创新 BBS scoped 节点(`run_mode=bbs`, `assignee=bot_id`) → 翻 RUNNING。
- `bbs/result` 路由 → `report_bbs_result` → `on_bbs_report`(engine.py:399):PASS→scoped DONE→根复核 gap→DONE;FAIL→删 scoped→释放→回可恢复。

## 4. 总体链路(优化版)

```
engine._maybe_propagate_hung(可恢复拦截)
  └─ asyncio.create_task(self._runner.run_bbs(g))    # fire-and-forget + done_callback

TaskRunner.run_bbs(g) → TaskExecutor.run_bbs(g) → bbs_runner.notify(g, bcs, bot, backend_url, skill_name)

bbs_runner.notify():
  Phase 1 — bid(评估):
    roster = bcs.list_bots_by_task_modes(dream=True, match="any")
    replies = await asyncio.wait_for(
        asyncio.gather(*[
            bot.send_and_wait_async(bot_id=r.bot_id, message=BID_PROMPT(task_id, r.bot_id, g),
                                   metadata={"biz_task_id": task_id}, timeout=180)
            for r in roster
        ], return_exceptions=True),
        timeout=180
    )
    # 解析每条回复的 content JSON: {completion_rate: int, task_spec: {metadata, context, goal, ...}}

  Phase 2 — select + claim + dispatch:
    winner = max(replies, key=lambda r: r["completion_rate"])
    self._graph.claim_bbs_owner(task_id, winner["bot_id"])    # 引擎服务端占根
    try:
        await bot.send_message(bot_id=winner["bot_id"],
                               message=TASK_MSG(skill_name, task_id, backend_url,
                                                 winner["bot_id"]),
                               metadata={"biz_task_id": task_id})  # fire-and-forget
    except Exception:
        self._graph.update_task_node_info(task_id, task_id,
            extend_props_patch={"bbs_owner": None})  # send 失败→回收 claim

# 胜出 bot(装新 skill, bbs-relay-single-task):
  └─ 读任务消息里的 task_id + backend_url + bot_id
     → 从 dashboard 读剩余事项 → 自己组织 task_spec
     → attach(④) → execute(⑤) → result(⑥) → on_bbs_report → 根收口
```

## 5. 组件与改动

### 5.1 ExecutionEngine 触发(engine.py)
与原方案一致:在 `_maybe_propagate_hung` 两处可恢复拦截 return 前,调用 `_schedule_bbs_notify(task_id, g)`(fire-and-forget `asyncio.create_task(self._runner.run_bbs(g))` + done_callback)。engine 新增 `_bg_tasks`/`_on_bg_done`。`__init__` 加 `api_base_url`。

### 5.2 TaskRunner.run_bbs(runner.py)
与原方案一致:`if self._execution_backend is not None: return await self._execution_backend.run_bbs(execution_graph)`;否则 stub。

### 5.3 TaskExecutor.run_bbs(task_executor.py)
构造器加 `api_base_url`(provider_id 由 BCS 实例持有,不需额外透传)。`run_bbs` 委托 `bbs_runner.notify`。

### 5.4 新模块 `task_runner/integration/bbs_runner.py`(核心改动)
```python
async def notify(execution_graph, *, bcs, bot, graph, backend_url, skill_name):
    """升BBS可恢复态后:bid→select→claim→dispath 给胜出 bot(不抛,best-effort)。"""
    task_id = execution_graph.task_id
    # ① 拉 dream roster
    roster = await bcs.list_bots_by_task_modes(dream=True, match="any")
    if not roster: return  # 无 dream bot→留可恢复态
    # ② Phase 1: bid(并发评估,3分钟超时)
    bid_results = await asyncio.wait_for(
        asyncio.gather(*[_bid_one(bot, r, execution_graph) for r in roster],
                       return_exceptions=True),
        timeout=180
    )
    # ③ 解析回复(成功 + 有 completion_rate JSON 的)
    bids = [_parse_bid(r) for r in bid_results if _is_ok(r)]
    bids = [b for b in bids if b and b.get("completion_rate", 0) > 0]
    if not bids: return  # 全没回复→留可恢复态
    # ④ Phase 2: 选胜 + 占根 + 派发
    winner = max(bids, key=lambda b: b["completion_rate"])
    try:
        graph.claim_bbs_owner(task_id, winner["bot_id"])       # 服务端占根
    except Exception as exc:
        logger.warning("[bbs-runner] claim failed task=%s:%s", task_id, exc)
        return
    msg = _task_msg(skill_name, task_id, backend_url, winner["bot_id"])
    try:
        await _send_wake(bot, winner["bot_id"], msg, task_id)  # fire-and-forget
    except Exception as exc:
        graph.update_task_node_info(task_id, task_id, extend_props_patch={"bbs_owner": None})
        logger.warning("[bbs-runner] send failed bot=%s task=%s:%s", winner["bot_id"], task_id, exc)


async def _bid_one(bot, rost_entry, execution_graph):
    """一发一收:发给 bot 评估 prompt,取回复 content JSON {completion_rate}。"""
    prompt = _bid_prompt(execution_graph, rost_entry.bot_id)
    run = await bot.send_and_wait_async(
        bot_id=rost_entry.bot_id, message=prompt,
        metadata={"biz_task_id": execution_graph.task_id}, timeout=170)
    return rost_entry.bot_id, run


def _bid_prompt(execution_graph, bot_id) -> str:
    # 让 bot 自评能完成多少,输出 JSON {completion_rate: 0-100}
    ...
```

**bid reply JSON**: `{ "completion_rate": 90 }`(0-100 整数;task_spec 不在 bid 里,由 skill 自派生)

### 5.5 管线(api_base_url)
TaskService._build_engine 传 `api_base_url=self._api_base_url` → engine → TaskExecutor。

### 5.6 新 skill:bbs-relay-single-task
- 位置:`src/backend/specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/SKILL.md` + `references/`。
- frontmatter `name: bbs-relay-single-task`。
- **跳过步①②③**(扫描/claim/自判),直接:
  - 从 dashboard 读剩余事项(goal − 已 DONE 叶子 output)→ 自己组织 task_spec。
  - 步④ `POST {backend}/api/v1/collaboration/tasks/bbs/attach` body `{task_id, parent_node_id(root), task_spec, bot_id}`:200→读 `data.node_id`;409→结束。
  - 步⑤ 用自身能力执行 `task_spec.instruction`。
  - 步⑥ `POST .../bbs/result` body `{task_id, node_id, bot_id, acceptance_result{verdict, acceptances_metric, gaps}, output_patch, exec_error?}`。
- 任务消息(skill 进场入口):`skill_name + task_id + backend_url + bot_id`。

## 6. 数据流(优化版)

1. `on_miss` → `_hung_and_escalate("miss_depth_exhausted")` → `bbs_mode=True` → 可恢复拦截 → `_schedule_bbs_notify` → `asyncio.create_task(run_bbs)`(锁外)。
2. `run_bbs` → `bbs_runner.notify`:
   - 拉 dream roster → `gather` 并发 `send_and_wait_async`(bid prompt) → `wait_for(180s)` → 收集回复 JSON。
   - 选 `completion_rate` 最高 → `graph.claim_bbs_owner(task_id, winner)`(服务端占根)。
   - `send_message(winner, task_msg)`(fire-and-forget,不含 task_spec——skill 自派生)。
3. 胜出 bot(装 `bbs-relay-single-task`)收到 task_msg → `bbs/attach`(owner 已占,通过)→ 执行 → `bbs/result` → `on_bbs_report` → 收口 / 回可恢复。

## 7. 幂等 / 去重 / 重复触发

- **不再有竞争抢单**(CAS race):引擎选一个 winner 并占根给它,不需要多个 bot 同时 claim。
- **claim 后不再触发**:可恢复拦截条件 `bbs_mode and not bbs_owner`;引擎 claim 后 `bbs_owner` 已设 → 后续 miss 冒泡不再走可恢复拦截(不再 run_bbs)。
- **claim 前**:每次 miss@max 冒泡到根 → 可恢复拦截 → 再 run_bbs(再 bid → 再选)。前一轮 bid 还在跑时若再来一次:两个 `run_bbs` 并发,但 `claim_bbs_owner` 的 CAS 会保证只有先到的 claim 成功,后到的 409(要么 winner 已 claim 则后者 `claim` 抛 → 后者 _notify return)。⚠️ 这一竞态可加一个 in-flight 标志或在 `notify` 头加 `bbs_owner` 检查(只在 None 时才 bid)来避免重复 bid。**待确认**。

## 8. 根节点"再规划"安全(已核对)

与原方案一致。可恢复态根不被再规划:
- `on_execute`: 一次性,且只要 PENDING → 跳过 PLANNING 根。
- `_on_pass_collect`: BBS 守卫(bbs_mode+无 bbs_owner)→ owner 停手 → 不再 plan 根。
- `TaskHarness`: 只扫 RUNNING/FAILED/PENDING,不扫 PLANNING 根。
- `on_miss`: 只作用 MISS 叶子兄弟,不规划根;冒泡到根走可恢复拦截(保持 PLANNING)。

## 9. 错误处理

- roster 空 / bcs None / bot None / provider 无 → `notify` 静默返回(留可恢复态)。
- bid 全没回复 / 全 `completion_rate==0` / 全超时 → 放弃(留可恢复态)。
- claim 失败(被并发 claim 或非 bbs_mode) → warn + return(留可恢复态)。
- `send_message(winner,...)` 失败 → warn(claim 已占,若 winner 没执行则留 RUNNING scoped 等以后……实际上还没 attach,harness 会清 bbs_owner 释放)。⚠️ send 失败是否要 cancel claim?**待确认**。
- `run_bbs` 未捕获异常 → done_callback 记 log。

## 10. 测试

### 单元(CI)
1. engine 可恢复拦截 → create_task `run_bbs`;硬 HUNG 不触发;`bbs_owner` 已设不触发。
2. TaskRunner.run_bbs 委托 + stub。
3. TaskExecutor.run_bbs 委托 `bbs_runner.notify`。
4. `bbs_runner.notify`:
   - dream roster 非空 → 并发 `send_and_wait_async`(bid),解 JSON → 选 completion_rate 最高 → `claim_bbs_owner` → `send_message(winner,task_msg)`。
   - roster 空 → 退出。bid 全超时/全没回复 → 退出。
   - bid 回复 JSON shape 鲁棒解析(`completion_rate` 缺失→0;`task_spec` 缺→跳过那 bot)。
5. skill 静态:frontmatter name 唯一 + 无步①②③ 描述。

### E2E(gated)
与原方案基本一致:一个 miss@MAX_DEPTH=1 必升 BBS 的任务;设 dream roster 含一个 bot(装新 skill)。不再需测试手动唤醒 relay——引擎自动 bid → 选它 → claim → 发 task_msg → bot 自动 attach/execute/result → 图 DONE。bid 回复(测试 mock 或真 LLM)。

## 11. 不在范围(YAGNI)
- 未抢占/无人评估超时 → 主动翻 HUNG。
- relay FAIL 后主动再通知。
- 并发 bid 防重(in-flight flag)。

## 12. 改动清单
- `engine.py`:可恢复拦截 +1 行 `_schedule_bbs_notify`;新增 `_schedule_bbs_notify` + `_bg_tasks`/`_on_bg_done`;`__init__` +`api_base_url`。
- `runner.py`:`run_bbs`。
- `task_executor.py`:`run_bbs`;构造器 +`api_base_url`。
- `task_runner/integration/bbs_runner.py`:**新增**(`notify`/`_bid_one`/`_bid_prompt`/`_task_msg`/`_send_wake`/`_parse_bid`/`_BBS_SKILL_NAME`)。
- `task_service.py`:`_build_engine` 传 `api_base_url`。
- `specs/2026-08-09-task-goal-driven-bbs-active-relay/bbs-relay-single-task/SKILL.md` + `references/`:**新增** skill。
- 单测若干。

## 13. 已确认 ✓
1. ✅ 引擎服务端 `claim_bbs_owner` 替 winner 占根(skill 不 claim)。
2. ✅ task_spec 由 skill 自己从 dashboard 派生(读剩余事项);bid 回复只含 `{completion_rate}`。
3. ✅ bid 回复 JSON `{completion_rate: int}` 够了。
4. ✅ 并发 bid 防重暂不考虑。
5. ✅ `send_message(winner,...)` 失败 → clear `bbs_owner`(回收 claim)。
6. ✅ provider_id 重构依赖已确认(`list_bots_by_task_modes` 不带 provider_id 参数,provider_id 在 BCS 实例)。
