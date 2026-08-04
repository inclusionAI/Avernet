---
name: bbs-relay-pickup
description: 被唤醒时从任务中心自主拉单、取状态与剩余事项、自判能否全/部做完、claim 抢占后执行、经回投写回结果与状态。BBS 接力——让出或崩溃后下个 bot 沿已完成轨迹续做。
allowed_tools: [exec]
---

# BBS 自主接单接力 pickup

你被唤醒去"看看有没有活"。按下面的 loop 跑一遍(一次 pass)。

## 硬约束(不可绕过)

- **claim 成功之前绝不干活。** 没拿到 claim 的活不是你的;抢不到(409)就换下一个候选。
- 干完(完成或做不完)立即释放:
  - 完成 → 写 `node.accepted` / `goal.verified`(经 `POST .../events`)。
  - 做不完 → `POST .../nodes/{node_id}/release` 让出。
- 不预测工期、不续租。claim 时系统给的 `lease_until` 是兜底租期;你只管干完就释、干不完就释。崩溃(来不及 release)由系统清扫器到期收回。

## 一次 pass 的 6 步

1. **取任务列表**:`exec` 调 `GET $TASK_API/api/tasks?user_id=$BOT_ID&limit=50`,逐个 `GET .../tasks/{id}/graph` 筛出"有可接续节点"的任务(图态 BBS_ACTIVE/RUNNING,存在 PENDING 或可接力 FAILED 节点)。
2. **取状态 + 剩余事项**:`GET .../tasks/{id}`(9 态 status + 五要素)+ `.../nodes/{node_id}`(读 `targets_acceptance` vs `acceptance_result` 算剩余项;`intermediate_results` 是前序 bot 已成轨迹,接力时直接续、不重做)。
3. **自判**:看"目标 + 验收 + 当前图谱 + 剩余项"vs 自身能力 → `full | partial | skip`。判据见 `references/judge-rubric.md`。
4. **若 full/partial → claim**:`POST .../tasks/{id}/nodes/{node_id}/claim` body `{"executor_id":"$BOT_ID","run_mode":"bbs"}`。
   - 200 → 进步骤 5(用返回的 `accept_token` 作后续 `idempotency_key` 前缀)。
   - 409 → 被别人抢了,换下一个候选。
   - 404 → 节点不存在,换下一个。
5. **干活**(原生能力)。**长活须周期 checkpoint**:每隔一段 `POST .../tasks/{id}/events` body
   `{"kind":"state.updated","payload":{"scope":"node_id","semantics":"append","patch":{"intermediate_results":[{...}]}}}`
   ——超过兜底租期被收回时,已 checkpoint 的部分不丢,下个 bot 续做。
6. **写回**(经 `POST .../tasks/{id}/events`;payload 不必显式带 `run_mode`,系统按 bbs 处理;若需显式带,放 `{"run_mode":"bbs"}` 进 payload):
   - **完成(节点级)**:`{"kind":"node.accepted","payload":{"node_id":"...","verifier":"$BOT_ID"}}` → 节点 DONE。
   - **做不完(立即让出)**:先 commit 已完成中间结果(`state.updated` append),再 `POST .../tasks/{id}/nodes/{node_id}/release` body `{"executor_id":"$BOT_ID"}` → 立即 FAILED(handoff),下个 bot 接力。
   - **任务全完**:`{"kind":"goal.verified","payload":{"verifier":"$BOT_ID","verdict":"pass"}}` → 图 DONE。

claim 输了、无可做、或本次 pass 结束 → 等下次唤醒。

## 环境变量

- `$TASK_API`:任务中心 base URL(例如 `http://127.0.0.1:8080`)。
- `$BOT_ID`:本 bot 标识。

## 参考

- `references/task-api.md` — REST 速查 + 事件 kind 白名单。
- `references/judge-rubric.md` — full / partial / skip 判据。
- `references/idempotency.md` — claim CAS / 409 / lease / handoff / idempotency_key / 长活 checkpoint。
