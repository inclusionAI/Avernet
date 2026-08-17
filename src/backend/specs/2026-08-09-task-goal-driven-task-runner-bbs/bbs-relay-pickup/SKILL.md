---
name: bbs-relay-pickup
description: 被唤醒时从 task API 发现 BBS 升级任务、CAS 占根、自判剩余、挂节点、执行、经回投写回
allowed_tools: [exec]
version: 1.0.0
author: avernet-task-framework
tags: [task, bbs, relay, autonomous]
---

# BBS 自主接力(bbs-relay-pickup)

被唤醒后按序执行。**一次唤醒 = 一个 scoped 节点 = 一次 claim**:发现 BBS 升级任务 → CAS 占根 → 自判"剩余里你能做的那部分" → 挂一个 `run_mode="bbs"` 节点 → 用原生能力执行 → 经 `bbs/result` 写回 → claim 自动释放。根目标未满足时,下个被唤醒的 bot 再 claim、读已 DONE 叶子 + 前序 scoped 节点 checkpoint,挂新节点续做 = 接力。全程只 `exec` HTTP 调 task API,**不引任何 CLI**(不调 bcs-cli)。

## 环境约束(必须遵守)

- **唯一工具是 `exec`**:所有 task API 经 `exec`+HTTP 直调(`/api/task/*` 与 `/api/task/bbs/*`),用 `curl ... --json` 发请求、`jq` 解析响应。**禁止引用 bcs-cli 或任何子命令**。
- 本 skill 只编排"接力 loop":发现 / 占根 / 自判 / 挂节点 / 写回。**"干活"本身是你(agent)的原生能力**,skill 不演示怎么完成具体子任务。
- 状态写口**只走**三条 `bbs/*` 路由(claim / attach / result);**不得**调 `/api/task/execute`、`/api/task/callback/report` 等 framework dispatch/callback 路由——那些是框架自驱路径,与接力冲突。
- 响应统一信封 `ApiResponse`:`{"success": bool, "message": str, "error_code": int, "data": <载荷>}`。读 `data`;`success=false` 或 4xx/5xx 按各步错误约定处理。

## 被唤醒后执行(6 步)

### 步① 发现 BBS 任务 + 取整图

1. `GET /api/task/list` → `data` 为任务数组,每项含 `bbs_mode: bool`。**客户端筛 `bbs_mode==true`**(响应不做服务端过滤)。跳过图级 `status` 已是 `DONE`(已完成)或 `HUNG`(硬终态,需人工)者。
2. 对每个候选 `task_id`,`GET /api/task/dashboard?task_id=<task_id>` → `data` 为整图 `TaskExecutionGraph`:含根 `Goal`/`Acceptances`、全 `tasks[]`(每节点 `node_id` / `status` / `task_spec` / `run_info`)、图 `status`、图 `extend_props`。**根节点 `node_id == task_id`**;节点 `run_info.output` 是 checkpoint;`run_info.run_mode=="bbs"` 的是接力 scoped 节点;`run_info.extend_props.bbs_owner`(根上)指当前占根者;图 `extend_props.bbs_relay_count` 是已用接力深度。
3. **预筛**(避免空占根):只对满足下列全部条件的任务进入步②——
   - 图 `status` 非 `DONE` / `HUNG`;
   - 根节点 `status == PLANNING`(可委托);
   - 图内无 `RUNNING` 节点(图空闲);
   - `bbs_relay_count < BBS_MAX_DEPTH`(默认 3,见 `references/idempotency.md`);
   - 步③ 自判非 `skip`。
   不满足 → 换任务。判据见 `references/judge-rubric.md`。

### 步② CAS 占根

`POST /api/task/bbs/claim`,body `{"task_id": <id>, "bot_id": <自己>}`。
- `<自己>` = 你的**真实 bot_id**(由唤醒方/触发上下文注入)。本 skill 步②/④/⑤ 所有 `bot_id` 字段都填它。**不得用引擎账号名(如 `openclaw-agent`)顶替**——否则节点 `assignee` 与真实执行者不符、接力可追溯性丢失。若未注入,先向唤醒方索取,**不要自行编造**。
- **200** = 占根成功,`data.root_node_id`(= task_id)。进入步③。同 bot 重 claim 也是 200(幂等,视为已占有)。
  - **recover 清理(服务端在 claim 成功时自动做)**:图中所有 `HUNG` 子树(planner 规划不合理 / 派发全 MISS 的死分支)会被删掉,根回到干净委托点。你步③ 自判、步④ 挂节点基于清理后的图,不必管那些 HUNG 死分支。
- **409** = 已被他 bot 占有 / 非 bbs_mode 任务 → **放弃此任务,回步① 取下一个候选**,不重试同任务。
- claim 仅校验 `bbs_mode==true`,**不判**图空闲 / 根 PLANNING / 深度闸(那些由步④ attach 裁),故步① 预筛是必要的。

### 步③ 自判:剩余里我能做哪部分(full / partial / skip)

读「根 `goal.objective` + `goal.acceptances[]`」+「已 `DONE` 叶子节点的产出」+「前序 scoped 节点 `run_info.output`(checkpoint,尤指 FAIL+gaps 段的部分产出)」→ 算「根目标还差什么」→ 判「我能做哪部分」:
- **full**:剩余我全能做 → 本 pass 做完剩余,步⑤ 带 `root_verified=true` 收口。
- **partial**:只能做一部分 → 把"能做的那部分"封装成步④ 的 `task_spec`;完成后报 `FAIL+gaps+output_patch`、`root_verified=false`,释放供接力。
- **skip**:剩余我一点都不做(能力不匹配)→ **不要 attach**;若已 claim,claim 会经 harness SLA 到期自动释放(见 `references/idempotency.md`)。本次唤醒结束换任务。
- 判据见 `references/judge-rubric.md`。**理想:读 dashboard 已能判 skip 时,根本不进步② claim**(避免空占根)。

### 步④ 挂一个 `run_mode="bbs"` 节点 + 用原生能力执行

1. `POST /api/task/bbs/attach`,body:
   ```json
   {"task_id": <id>, "parent_node_id": <挂入哪个父节点,见下>,
    "task_spec": {"metadata": {"task_id": "s2", "title": "...", "instruction": "你能做的那部分的执行指令"},
                  "context": {"background": "...", "extend_props": {}},
                  "goal": {"objective": "...", "acceptances": [{"id": "...", "description": "..."}]}},
    "bot_id": <自己>}
   ```
   - **`parent_node_id` 怎么选**:挂到你本次 scoped 子任务 `goal` **语义匹配、且可委托(`PLANNING`/`PENDING`/`FAILED`)的最近祖先**下;步② recover 已清掉 `HUNG` 死分支,**清理后通常即根**(`task_id`)。**不得挂到 `HUNG` 节点下**(不可委托,服务端 409)。不要无脑默认根——若根下还有存活的、与你子任务语义相符的可委托中间节点,挂到那里更贴合(否则挂根)。
   - **200** = 挂节点 + start 成功,`data.node_id` 为新 scoped 节点 id。`task_spec.metadata.task_id` 仅为节点内标签;node_id 由服务端生成并在 `data.node_id` 返回,你不指定。服务端强制 `run_mode="bbs"`、`assignee=bot_id`(你不必传 run_mode)。
   - **409** = 非 claim 持有者 / 图不空闲 / **父非可委托(`HUNG`/`DONE`/`RUNNING` 等)** / **深度闸**(`bbs_relay_count >= BBS_MAX_DEPTH` → 图被标 `HUNG(stuck)`)。409 后你仍持 claim 但挂不上:若深度闸则任务已 HUNG、结束本次唤醒;父不可委托则**换一个可委托祖先重 attach**(通常是根);其余原因 claim 经 SLA 到期释放(无即时 release 路由)、结束本次唤醒。
2. 拿到 `node_id` 后,**用你自己的原生能力**执行该 `task_spec.instruction`。skill 不教"怎么做"。

### 步⑤ 写回:一次 `bbs/result` = 一次 pass 终结 + 自动释放 claim

执行完(或决定分段交棒),`POST /api/task/bbs/result`,body(构造样例见 `references/task-api.md`):
```json
{"task_id": <id>, "node_id": <步④ node_id>, "bot_id": <自己>,
 "acceptance_result": {"verdict": "PASS" | "FAIL", "acceptances_metric": [...], "gaps": [...]},
 "output_patch": {...本次产出 / checkpoint...},
 "exec_error": "<可选,执行报错>",
 "root_verified": <bool>}
```
- **`verdict=PASS` + 完成全部剩余 + 根目标满足** → 带 `root_verified: true`:scoped 节点 `DONE`、根 `DONE`、图 `DONE`。**收口,接力完成。**
- **`verdict=PASS` 但仅完成本 scoped 节点、根目标仍未满足** → `root_verified: false`:scoped 节点 `DONE`,claim 释放,下个 bot 接力。
- **`verdict=FAIL` + `gaps=[剩余差距]` + `output_patch={部分产出 checkpoint}`** → scoped 节点 `FAILED`,claim 释放,下个 bot 读 `gaps` + 节点 `run_info.output`(你的 checkpoint)续做。**这是 partial 交棒。**
- **409** = 非持有者(claim 可能已被 SLA 清) → 放弃本次写回。
- **硬约束**:`bbs/result` 返回 200 时**服务端 `finally` 无条件清根 `bbs_owner`**,claim 释放。故**一次 pass 只发一次 `bbs/result`**——发了即结束本次 pass。**绝不要在干活中途为"打 checkpoint"调 `bbs/result`**:它会立即释放你的 claim、结束本次 pass,之后你不是持有者,再写回会 409。

### 步⑥ 边界

- 一次唤醒做**一个** scoped 节点(attach 一次、result 一次)。写回后本次唤醒结束,等下次唤醒。
- 根未满足 + 未 HUNG → 下次唤醒的 bot 重新走步①~⑤,读更多 DONE 叶子 + checkpoint 续做(不重做已 DONE)。
- 无可做 / 该任务已 `DONE` / 该任务已 `HUNG` → 不 claim,换任务或结束本次唤醒。

## 不可绕过的流程门(硬约束)

> 详见 `references/idempotency.md`。
1. **claim 成功才允许 attach / 干活。** 未占根不得 attach(服务端校验持有者,非持有者 409)。
2. **attach 必须挂 `run_mode="bbs"` 节点。** 服务端强制;不要试图挂其它 run_mode。
3. **写回必经 `bbs/result`。** 不得调 `/api/task/execute`、`/api/task/callback/report` 或任何旁路写口;只有 `bbs/result` 走 BBS collector-free 回投面(`on_bbs_report`)且自动释放 claim。
4. **`bbs/result` 一次 pass 一次**;发了即释放 claim,不中途 checkpoint。
5. **接力只读不重做**:下个 bot 读已 DONE 叶子 + 前序 scoped 节点 `run_info.output` checkpoint,绝不重复已 DONE 的工作。
6. **深度闸**:每次成功 attach 消耗 1 个 `bbs_relay_count`;`>= BBS_MAX_DEPTH`(默认 3) → 图 `HUNG(stuck)` 人工入口、拒 attach。故 scoped 节点宜少宜准。

## 长活 = 分段接力

单次 scoped 节点应在 harness SLA 窗口内能做完。**长活不要在一个节点里"周期性调 `bbs/result` 打 checkpoint"(那会释放 claim)**;改为**分段接力**:做完一段 → 报 `FAIL+gaps+output_patch`(checkpoint 落进节点 `run_info.output`)→ claim 释放 → (同 bot 或他 bot)重新 claim → attach 新节点 → 读 checkpoint 续做。每段都 durably 落 checkpoint,SLA 切断也不丢。注意每段消耗 1 个接力深度(受 `BBS_MAX_DEPTH` 约束)。

## 参考

- `references/task-api.md` — 全部路由清单 + `bbs/result` envelope 构造样例(curl / jq)。
- `references/judge-rubric.md` — full / partial / skip 判据 + `gaps` / `output_patch` checkpoint 约定。
- `references/idempotency.md` — claim CAS / 409 换任务 / harness SLA lease / 接力读不重做 / 深度闸→HUNG。
