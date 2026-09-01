# BCN 下行 chat.abort 语义变更（bot 维度过滤 & 仅终止 RUNNING）

- 日期：2026-09-01
- 范围：baas 模块，仅 `chat.abort` 下行单链路

## 行为契约变更（Breaking / P1）

`chat.abort` 下行处理由"按 `session_id` 取消该 session 下所有 PENDING/RUNNING run",
收窄为"按 `(bot_id=to_bot.provider_bot_ref, session_id)` 维度仅取消该 bot 在该
session 下 RUNNING 的 run"。BCN 调用方需知悉该行为变更。

### 变更前后对照

| 场景 | 变更前 | 变更后 |
|---|---|---|
| 群聊：abort 指定 bot，该 bot 有 RUNNING run | 取消该 session 下所有 bot 的 PENDING+RUNNING | 仅取消该 bot 的 RUNNING run（FAILED+force_done+本机 cancel+engine 通知） |
| 群聊：abort 指定 bot，该 bot 无 RUNNING 但其他 bot 有 | 误杀其他 bot 的 run | 不影响任何 run；按该 bot 维度判 410/200 |
| 目标 bot 仅有 PENDING run | 标 FAILED+force_done | 不动；aborted=false；PENDING 由 `chat.abort` 超时扫描兜底 |
| 重复 abort 同一终态 run（该 bot 维度） | 410 run_terminated | 410 run_terminated（幂等不变，判定维度收窄到该 bot） |
| session 无任何 run 记录 | 200 aborted=false | 200 aborted=false（不变） |

## 请求体契约

不变。复用现有 `to_bot.provider_bot_ref` 作为 bot 维度过滤键，无新增字段。
`bot_id`/`session_id` 字段早已存在于 `baas_bot_run_queue`（`BotRunQueueRecord.bot_id` / `.session_id`），
无 DDL / 数据迁移。

## 响应码

- 200 `{ok: true, aborted: true, aborted_run_ids: [...]}`：目标 bot 在该 session 下存在被取消的 RUNNING run。
- 200 `{ok: true, aborted: false, aborted_run_ids: []}`：该 bot 在该 session 无任何 run 记录，或仅有 PENDING（不命中 cancel）。
- 410 `run_terminated`（不重试）：该 bot 在该 session 下无 RUNNING run 但存在已终结（DONE）记录。

## 回滚

单次提交即可回滚：移除新增的 `find_running_by_bot_session` / `find_terminal_by_bot_session`
与 worker/service 的 `bot_id` 传参，恢复 `abort_runs_by_session(session_id)` 旧签名与旧 repo 调用。
请求体契约不变 + 无 DDL，回滚无需数据修复或配置回退，直接 revert 该 PR 即可恢复原 session 维度全量取消行为。