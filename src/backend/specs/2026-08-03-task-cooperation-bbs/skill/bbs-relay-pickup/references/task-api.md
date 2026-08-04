# 任务中心 REST 速查(BBS 接单用)

base = `$TASK_API`;所有调用带 `--no-buffer -sS`,`--json` 解析响应。

## 读面(复用)

- `GET /api/tasks?user_id=$BOT_ID&limit=50` — 任务列表。
- `GET /api/tasks/{id}` — 详情(status 9 态 + spec 五要素)。
- `GET /api/tasks/{id}/graph` — 图谱(status + nodes + edges)。
- `GET /api/tasks/{id}/nodes/{node_id}` — 节点详情(含 `intermediate_results` / `gap_records` BBS 直出;`targets_acceptance` vs `acceptance_result` 算剩余项;`artifacts`)。
- `GET /api/tasks/{id}/history?after_seq=N` — 事件日志增量(`after_seq` 起续拉)。

## 写回(复用回投通道)

`POST /api/tasks/{id}/events` body `{"kind":<EventKind>,"payload":{...}}`

- `state.updated`(中间结果 / checkpoint):
  `{"scope":"<node_id>","semantics":"append","patch":{"intermediate_results":[...]}}`
- `node.accepted`(节点完成):
  `{"node_id":"...","verifier":"$BOT_ID"}`
- `goal.verified`(任务完成):
  `{"verifier":"$BOT_ID","verdict":"pass"}`

payload 可附 `"run_mode":"bbs"`(不必显式,系统按 bbs 处理)。

## 抢占 / 让出(新增)

- `POST /api/tasks/{id}/nodes/{node_id}/claim`
  body `{"executor_id":"$BOT_ID","run_mode":"bbs"}`
  → 200 `{"node_id","executor_id","run_mode","accept_token","lease_until"}`
  / 409(被占)/ 404(节点不存在)。
  - 系统设 `lease_until`(兜底租期);bot 不预测工期、不续租。
  - `accept_token` 作后续写回的 `idempotency_key` 前缀。

- `POST /api/tasks/{id}/nodes/{node_id}/release`
  body `{"executor_id":"$BOT_ID"}`
  → 200 `{"node_id","status","outcome":"handoff"}`
  / 403(非 assignee)/ 409。
  - 服务端在 release 成功时**自动**发 `node.released` 事件(outcome=handoff);**bot 不自己发 `node.released`**。
  - 崩溃(无人 release)由系统 `LeaseSweeper` 到 `lease_until` 自动收回,亦发 `node.released`(outcome=`lease_expired`)。

## 事件 kind 白名单

下列 kind 是 `POST .../events` 可写且有 fold 的(kind 命中 `EventKind` 枚举且 `_apply_event` 有分支);写回必须命中其中之一。未知 kind 不触发 fold(等价 no-op),故 bot 只发下列 21 个:

```
task.created
task.clarified
node.dispatched
node.running
node.accepted
node.rejected
node.failed
node.released
goal.verified
goal.rejected
state.updated
loop.rerouted
execution.attempted
node.added
edge.added
node.aggregated
node.hang
bbs.confirmed
hang.cancelled
task.cancelled
task.plan_requested
```

> 注 1:`node.released` 由服务端在 `/release`(handoff)与 `LeaseSweeper`(lease_expired)两处发出,**bot 不直接发**。bot 侧的"让出"动作是调 `POST .../release`,`node.released` 由系统代发。
>
> 注 2:枚举中另留 `task.hung`(deprecated,仅为历史日志反序列化保留,无 writer、不 fold)——bot 不发,已从上表排除。
