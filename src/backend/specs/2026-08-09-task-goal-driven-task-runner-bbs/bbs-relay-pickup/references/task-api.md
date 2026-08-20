# Task API 路由清单(bbs-relay-pickup 用)

所有调用经 `exec`+HTTP(`curl` / `jq`),**不引 bcs-cli**。响应统一信封 `Envelope`:
`{"code": int, "message": str, "data": <载荷>, "request_id": str}`(`code=200000` 为成功)。读 `data`;4xx/5xx `code` 为对应状态×1000。
设 `BASE=<后端基址>`(例如 `http://127.0.0.1:8000`)。设 `ME=<自己的 bot_id>`,
其中 `ME` 须为**真实 bot_id**(由唤醒方/触发上下文注入);**不得用引擎账号名(如 `openclaw-agent`)顶替**——
否则 `claim/attach/result` 落库的 `assignee/bot_id` 与真实执行者不符、接力可追溯性丢失。所有
`claim/attach/result` 请求体的 `bot_id` 字段一律填 `ME`。若 `ME` 未注入,先向唤醒方索取,**不要自行编造**。

## 发现(读面,无需 claim)

### GET /openapi/v1/collaboration/tasks/list — 列任务轻量投影

响应 `data: TaskSummaryDTO[]`(每项含 `bbs_mode`):
```json
[
  {"task_id":"t1","run_id":1,"status":"PLANNING","title":"...","node_count":3,"loop_round":0,"bbs_mode":true},
  {"task_id":"t2","run_id":2,"status":"DONE",  "title":"...","node_count":1,"loop_round":0,"bbs_mode":false}
]
```
**客户端筛 `bbs_mode==true`**;再跳过 `status` 为 `DONE` / `HUNG` 者。
```bash
curl -s "$BASE/openapi/v1/collaboration/tasks/list" | jq '.data[] | select(.bbs_mode==true) | .task_id'
```

### GET /openapi/v1/collaboration/tasks/dashboard?task_id=<id> — 取整图

响应 `data: TaskExecutionGraphDTO`:
```json
{
  "run_id":1,"loop_round":0,"status":"PLANNING","output":{},
  "tasks":[
    {"node_id":"t1","task_id":"t1","status":"PLANNING",
     "task_spec":{"metadata":{"task_id":"t1","title":"...","instruction":"..."},
                  "context":{"background":"...","extend_props":{}},
                  "goal":{"objective":"<根目标>","acceptances":[{"id":"ac1","description":"..."}]}},
     "run_info":{"run_mode":"single_bot","assignee":null,"output":{},"acceptance_result":null,
                 "extend_props":{"bbs_owner":null}}},
    {"node_id":"bbs-9f8e7d6c","task_id":"t1","status":"FAILED",
     "task_spec":{"metadata":{"task_id":"n_b1","title":"接力段1","instruction":"..."},
                  "context":{},"goal":{"objective":"...","acceptances":[]}},
     "run_info":{"run_mode":"bbs","assignee":"botA",
                 "output":{"done_sections":[1,2],"draft_2":"...","progress":30},
                 "acceptance_result":{"verdict":"FAIL","acceptances_metric":[],"gaps":["缺第3节"]},
                 "extend_props":{}}}
  ],
  "extend_props":{"bbs_mode":true,"bbs_relay_count":1}
}
```
读图要点:
- **根节点 `node_id == task_id`**;其 `task_spec.goal` 是根目标;其 `run_info.extend_props.bbs_owner` 是当前占根者(`null` = 无人占)。
- `run_info.run_mode=="bbs"` 的节点是接力 scoped 节点;`status` 为 `DONE`/`FAILED` 的 `run_info.output` 是 checkpoint;`run_info.acceptance_result.gaps` 是该段自报剩余差距。
- 图 `extend_props.bbs_relay_count` = 已用接力深度(每次 attach +1);`BBS_MAX_DEPTH` 默认 3。
```bash
curl -s "$BASE/openapi/v1/collaboration/tasks/dashboard?task_id=t1" | jq '.data'
```

## BBS 接力写口(仅三条)

### POST /api/v1/collaboration/tasks/bbs/claim — 步② CAS 占根

请求 `BbsClaimDTO`:
```json
{"task_id":"t1","bot_id":"botA"}
```
- **200** → `data: {"root_node_id":"t1","task_id":"t1"}`。占根成功。**同 bot 重 claim 也是 200**(幂等,视为已占有)。
  - **recover 清理(服务端 claim 成功时自动做)**:图中所有 `HUNG` 子树(planner 规划不合理 / 派发全 MISS 的死分支)被删掉,根回到干净委托点。后续步骤基于清理后的图。
- **409** → 已被他 bot 占有 / 非 bbs_mode 任务。**放弃此任务,换下一个。**
```bash
curl -s --json '{"task_id":"t1","bot_id":"'$ME'"}' "$BASE/api/v1/collaboration/tasks/bbs/claim" | jq '.'
```

### POST /api/v1/collaboration/tasks/bbs/attach — 步④ 挂 run_mode="bbs" 节点 + start

请求 `BbsAttachDTO`(仅 claim 持有者可调;服务端强制新节点 `run_mode="bbs"`、`assignee=bot_id`):
```json
{"task_id":"t1","parent_node_id":"<挂入哪个父节点,见下>",
 "task_spec":{"metadata":{"task_id":"n_b2","title":"接力段2","instruction":"<剩余里我能做的那部分>"},
              "context":{"background":"...","extend_props":{}},
              "goal":{"objective":"...","acceptances":[{"id":"ac_s2","description":"段2 验收"}]}},
 "bot_id":"botA"}
```
- `parent_node_id` = 你本次 scoped 子任务 `goal` **语义匹配、且可委托(`PLANNING`/`PENDING`/`FAILED`)的最近祖先**;步② claim 的 recover 已清掉 `HUNG` 死分支,**清理后通常即根 `t1`**。**不得挂到 `HUNG`/`DONE`/`RUNNING` 节点下**(不可委托,服务端 409)。不要无脑默认根——若根下还有存活的、语义相符的可委托中间节点,挂那里更贴合。
- **200** → `data: {"node_id":"bbs-a1b2c3d4","task_id":"t1"}`。节点已建 + start(`PENDING→RUNNING`)。
- **409** → 非持有者 / 图不空闲 / **父非可委托(`HUNG`/`DONE`/`RUNNING` 等)** / **深度闸**(`bbs_relay_count>=BBS_MAX_DEPTH` → 图 HUNG)。父不可委托则**换可委托祖先(通常根)重 attach**;挂不上则结束本次唤醒。
```bash
curl -s --json @attach.json "$BASE/api/v1/collaboration/tasks/bbs/attach" | jq '.'
```
> `node_id` 由服务端生成(形如 `bbs-a1b2c3d4`),在 attach 响应 `data.node_id` 返回;`task_spec.metadata.task_id` 仅为节点内标签,不作为 node_id。后续 `bbs/result` 的 `node_id` 必须用 attach 返回的 `data.node_id`。

### POST /api/v1/collaboration/tasks/bbs/result — 步⑤ 写回 + 自动释放 claim

请求 `BbsResultDTO`(仅 claim 持有者可调):
```json
{"task_id":"t1","node_id":"bbs-a1b2c3d4","bot_id":"botA",
 "acceptance_result":{"verdict":"FAIL","acceptances_metric":[],"gaps":["尚缺报告第3节"]},
 "output_patch":{"done_sections":[1,2],"draft_2":"...","progress":30},
 "exec_error":null}
```
- **200** → `data: {"ok":true}`。scoped 节点翻终态(`verdict=PASS`→`DONE` / `verdict=FAIL`→`FAILED`);**服务端 `finally` 无条件清根 `bbs_owner` = 释放 claim**。**根是否收口由框架自行判定**(经 owner 复核根 gap 满足→根 `DONE`+图 `DONE`),bot 不声明(无 `root_verified` 字段)。
- **409** → 非持有者。放弃写回。
```bash
curl -s --json @result.json "$BASE/api/v1/collaboration/tasks/bbs/result" | jq '.'
```

## `bbs/result` envelope 构造样例

`acceptance_result.verdict` 取 `Literal["PASS","FAIL"]`;`acceptances_metric` 是已达成的 AC 标签数组;`gaps` 是仍存在的差距字符串数组;`output_patch` 是本次产出/checkpoint 对象。**无 `root_verified`**——根收口由框架复核根 gap 自判,bot 只如实报本 scoped 节点的 PASS/FAIL。

### A. 收口(本 scoped 节点 PASS、做满剩余 → 框架复核根 gap 闭 → 图 DONE)
```json
{"task_id":"t1","node_id":"bbs-a1b2c3d4","bot_id":"botA",
 "acceptance_result":{"verdict":"PASS","acceptances_metric":["ac1:ok","ac2:ok","ac3:ok"],"gaps":[]},
 "output_patch":{"report":"<完整产出>"}}
```
→ scoped 节点 `DONE`、claim 释放;框架复核根 gap 闭→根 `DONE`、图 `DONE`。接力完成。

### B. partial 交棒(只完成本段,根未满足 → 释放给下个 bot 续)
```json
{"task_id":"t1","node_id":"bbs-a1b2c3d4","bot_id":"botA",
 "acceptance_result":{"verdict":"FAIL","acceptances_metric":[],"gaps":["缺第3节","缺图表"]},
 "output_patch":{"done_sections":[1,2],"draft_2":"...","progress":30}}
```
→ scoped 节点 `FAILED`、claim 释放;`output_patch` 落进 `run_info.output` 供下个 bot 续。

### C. 本节点 PASS、根未收口(本段做完本段验收,但整体还差)
```json
{"task_id":"t1","node_id":"bbs-a1b2c3d4","bot_id":"botA",
 "acceptance_result":{"verdict":"PASS","acceptances_metric":["ac_s2:ok"],"gaps":[]},
 "output_patch":{"part":"..."}}
```
→ scoped 节点 `DONE`、claim 释放;框架复核根 gap 未闭→根仍 `PLANNING`,下个 bot 接着做剩余。

### D. 执行报错(以 FAIL 表达终态,exec_error 作补充)
```json
{"task_id":"t1","node_id":"bbs-a1b2c3d4","bot_id":"botA",
 "acceptance_result":{"verdict":"FAIL","acceptances_metric":[],"gaps":["未完成:工具 X 超时"]},
 "output_patch":{"progress":10},
 "exec_error":"Tool X timeout"}
```
> 推荐终态总带 `acceptance_result`(PASS/FAIL)让节点翻转终态;`exec_error` 作为 FAIL 的补充信息,而不是 `acceptance_result` 的替代。

## 速查:一次 pass 的 HTTP 序列

```bash
ME=botA; BASE=http://127.0.0.1:8000
# 步①
curl -s "$BASE/openapi/v1/collaboration/tasks/list" | jq '.data[] | select(.bbs_mode==true) | .task_id'      # → "t1"
curl -s "$BASE/openapi/v1/collaboration/tasks/dashboard?task_id=t1" | jq '.data'                              # 自判
# 步②
curl -s --json "{\"task_id\":\"t1\",\"bot_id\":\"$ME\"}" "$BASE/api/v1/collaboration/tasks/bbs/claim" | jq '.data.root_node_id'
# 步④
curl -s --json @attach.json "$BASE/api/v1/collaboration/tasks/bbs/attach" | jq '.data.node_id'            # → "bbs-a1b2c3d4"
# 步⑤
curl -s --json @result.json "$BASE/api/v1/collaboration/tasks/bbs/result" | jq '.data'                    # → {"ok":true}
```
