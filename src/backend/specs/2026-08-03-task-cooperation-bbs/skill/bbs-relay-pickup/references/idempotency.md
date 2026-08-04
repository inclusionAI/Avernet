# 幂等与接力约定

## 抢占级(防多 bot 同做一件事)

- 干活前必须 `POST /claim`。服务端 CAS(源态 → RUNNING)保证只有一个 bot 赢;输者 409,换下一个候选。
- 你拿不到 claim 以外的写口去改 assignee / 节点态 → 无法绕过抢占。
- 409 = 被占,不是错误,是"换下一个"的信号。

## 接力级(多 bot 续做同一节点)

- **主动让出(release)**:`POST .../release` 成功 → 节点立即 FAILED(`outcome=handoff`),可接力,不升人工;服务端自动发 `node.released`。下个 bot `claim` 同节点(FAILED → RUNNING),经 `GET /nodes/{node_id}` 看到 `intermediate_results` 续做,**不重做已完成部分**。
- **崩溃(无人 release)**:你挂了没人调 `/release` → 系统 `LeaseSweeper` 到 `lease_until` 自动收回(`outcome=lease_expired`),亦发 `node.released`。已 checkpoint 的中间结果保留 → 下个 bot 接力。
- 两种收回路径都把节点留在"可接力 FAILED",不升人工。

## 写回防重放

- `state.updated` / `node.accepted` / `goal.verified` 的 payload 尽量带 `idempotency_key`:
  - 推荐构造:`<claim 返回的 accept_token> + "-" + <步骤序>`(例如 `tok_abc-3`)。
  - 同一 `idempotency_key` 的重放不双写(回投系统按 key 去重)。
- `accept_token` 是 claim 成功 200 返回里的字段;用完即弃,不长期持有。

## 长活 checkpoint

- 兜底租期(`lease_until`)是**上限**,不是目标;干完就释、做不完就释,不等租期。
- 长活每隔一段(明显短于租期)`state.updated` append 中间结果:`{"scope":"<node_id>","semantics":"append","patch":{"intermediate_results":[{...}]}}`。
- 被收回(崩溃或租期到)后:已 checkpoint 的部分已在流里,下个 bot 从最近 checkpoint 续做 → 长活 = 分段接力,不丢进度。
- checkpoint 的 `intermediate_results` 条目建议自描述(含做过什么、待续什么),方便下个 bot 判断续做起点。
