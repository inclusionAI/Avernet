---
name: undercover-game-referee
description: 主持谁是卧底，按当前节点或唤醒执行事实层命令与主持稿，保持发言、投票、遗言和终局流程。
allowed-tools:
  - exec
---

# 谁是卧底 · 当前动作

`uc` 表示 `python3 skills/undercover-game-referee/scripts/undercover.py`。
`$session_id` 从本次 GroupContext 获取，每局独立，命令显式带 `--session "$session_id"`。
正常执行只需本入口和当前节点正文；详细阶段/异常查 references/phase-machine.md，
参数查 references/commands.md，话术查 references/boards.md，拓扑与故障背景查 references/runs.md。
不预先通读这些参考文件。

## 节点唤醒优先

收到 `[State Machine Task]` 就是 NODE_TASK，直到本次激活结束都不是 ECHO。

| node_id | 本次动作 |
| --- | --- |
| vote_open | 按节点正文播报开投，不调用工具，结束 |
| collect | 全座位原话交 `uc speeches-set --session "$session_id" --json '<JSON>'`；按返回 label/text 念遮蔽后的汇总，结束；不在这里 open-vote |
| tally | 全座位原始票面交 `uc votes-set --session "$session_id" --json '<JSON>'`；结构化 human 票面保留为文本。continue 念开票稿后结束；finished 按下述终局步骤收尾 |

脚本校验当前阶段。节点命令报阶段不符时停止，不把迟到任务当作新阶段执行。

### tally 刚判胜

1. `uc reveal --session "$session_id"`，仅本次 votes-set 返回 finished 时执行。
2. 按 reveal 结果公布词对、全员身份与词、胜负及关键转折；这是终局保密例外。
3. `bcs-cli session complete "$session_id"` 结束本会话，然后结束激活。失败如实报告。

该节点是 state_machine，不能使用 bcs_task_complete。禁止 bcs_route、路由给自己或寻找工具。
终局收尾就在当前 tally 内，不等回灌。只要不是本次刚判胜，FINISHED 不授权重复 reveal 或完成会话。

## 普通消息与回灌

非节点唤醒时先 `uc status --session "$session_id"`，按下面处理。
新会话必须为 NO_GAME；新会话读到 FINISHED 时停止核对会话 ID，不公布上一局。

| 状态 / 消息 | 本次动作 |
| --- | --- |
| NO_GAME | `uc begin --session "$session_id"`；human 未 Present 时提示加入；已加入则说开场规则，等待“开始” |
| AWAIT_START + 人类“开始” | 执行 begin 返回的 init_command；在工具调用前的消息中告诉 human 座位和 human_word；最后 `uc open-round --session "$session_id"` |
| AWAIT_VOTE_START + 汇总稿回灌/人类消息 | `uc open-vote --session "$session_id"`，结束 |
| AWAIT_NEXT_ROUND + 开票稿回灌 | pending_ping 非空：`uc render-ping --session "$session_id"`，将 message 原样 bcs_assign_task 给 target_bot，结束；为空：最后 `uc open-round --session "$session_id"` |
| AWAIT_NEXT_ROUND + 遗言回执 | `uc mask --session "$session_id" --seat N --text '<原话>'`；只念返回 text，不附和；最后 `uc open-round --session "$session_id"` |
| AWAIT_NEXT_ROUND + 人类“继续” | 按既有规则跳过等待，最后 `uc open-round --session "$session_id"` |
| FINISHED | 不重复 reveal/结束会话；ECHO 静默结束，人类询问才提示新建会话 |
| 运行中 + 迟到 WORKER_MSG | 不推进、不调用脚本 |

若 NO_GAME 收到的消息已明确要求开始，begin 后继续发牌；否则保留人类确认开始的步骤。

开场按 begin 返回的配置说明人数与卧底数、只给词不告知身份、轮流发言后同时投票、
平票不重投及胜负规则（默认 6 人 1 卧底，卧底全出局平民胜，剩两人或轮数用完卧底仍在则卧底胜）。
还须说明：human 先加入当前会话；每轮在副屏发言、投票；不点他人节点看词；
超过五分钟无进展回复“卡住了”。话术用中文、名字带号数，不输出命令或内部状态。

## 末节点与恢复

collect/tally 不启动下一个运行、不派任务，下一步由最终产物回灌触发。
open-* 和 bcs_assign_task 都是本次最后一个工具调用；提交后无状态查询、sleep 或轮询。
open-round 成功后播报 announcement；open-vote 提交后按返回提示立即结束激活，开投稿由 vote_open 主持人节点播报。
发言从玩家直接开始；投票必须先由主持人 vote_open 开场，再全员并行投票。
发牌告知在 open-round 调用前发出。发言重开说明在 announcement；投票重开时按返回提示告知旧票作废。
提交失败如实报告，不播报成功开场；submitted=true 也不代表任何玩家已完成。
派遗言时身后不能有等待执行的节点。遗言回执公开，词与身份不可泄露。

“卡住了”：查 status，SPEAK_RUNNING 用 open-round --retry；VOTE_RUNNING 用 open-vote --retry；
AWAIT_VOTE_START 用 open-vote（不加 --retry）。都带当前 --session。
IN_COLLECT_NODE / IN_TALLY_NODE 立即结束，不重试；RUN_SLOT_BUSY 说明仍在等待，结束。
重开最多两次，按对应阶段的开场告知旧票/发言作废；仍失败请新建会话，不猜测推进。细节见阶段机 SX。
