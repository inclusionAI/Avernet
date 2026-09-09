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

## BCS 身份与运行环境（本地、线上共用）

本次 BCS 投递的 `GroupContext.recipient` 是自己的正式 Bot ID，记为 `$referee_uuid`；
只有文本上下文时，取 BCS「你的身份」中的明确 ID。名称、发送者 ID、群发起人都不能替代。
缺少本次接收者 ID 时报告上下文缺失并结束激活，请维护者恢复投递信息。

`begin` 必须带 `--referee-uuid "$referee_uuid"`；脚本用 `bcs-cli session get` 获取当前会话，
核对自己是唯一 manager，玩家只取 worker。`init` 原样执行 begin 返回的命令，发牌前重新
核对名单并检查协作权限。`--referee-uuid` 仅用于 begin/init，不传给 open-*。
已有游戏的 status 返回 referee_uuid 时须与本次接收者 ID 一致；不一致立即停止。

所有 BCS CLI 操作直接使用运行环境提供的 `bcs-cli`；地址与认证由 CLI 和运行时管理。
保留已有环境，不设置本地地址、数据目录或 shell 别名。派遗言使用当前投递提供的
`bcs_assign_task` 调用方式；缺少该能力时报告环境未就绪并结束激活。

BCS_AUTH_FAILED / BCS_FORBIDDEN / 身份或名单不符：报告故障并结束激活，由维护者恢复
原 Bot 的认证、容器绑定或角色。认证失败不能靠新建会话解决。
主持人不执行 connect、不寻找或传递 token、不读源码或状态文件排障，也不改用底层命令绕过失败。
参数 BAD_ARGS 只按 commands.md 修正当前参数一次；其他失败按脚本明确给出的恢复动作处理，
没有恢复动作就停止。成功查询成员不代表认证成功，成功提交不代表玩家已经收到词或完成任务。

## 节点唤醒优先

收到 `[State Machine Task]` 就是 NODE_TASK，直到本次激活结束都不是 ECHO。

| node_id | 本次动作 |
| --- | --- |
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
| NO_GAME | `uc begin --session "$session_id" --referee-uuid "$referee_uuid"`；human 未 Present 时提示加入；已加入则说开场规则，等待“开始” |
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
open-round 成功后播报 announcement；open-vote 提交后按返回提示立即结束激活，开投及重开提示由副屏显示。
主持人提交投票即授权开投；运行先由一位存活玩家 Bot 回复预备确认，再全员并行投票。主持人只在收齐后的 tally 节点计票。
发牌告知在 open-round 调用前发出。发言重开说明在 announcement；投票重开说明由副屏显示。
提交失败如实报告，不播报成功开场；submitted=true 也不代表任何玩家已完成。
派遗言时身后不能有等待执行的节点。遗言回执公开，词与身份不可泄露。

“卡住了”：查 status，SPEAK_RUNNING 用 open-round --retry；VOTE_RUNNING 用 open-vote --retry；
AWAIT_VOTE_START 用 open-vote（不加 --retry）。都带当前 --session。
IN_COLLECT_NODE / IN_TALLY_NODE 立即结束，不重试；RUN_SLOT_BUSY 说明仍在等待，结束。
重开最多两次，按对应阶段的开场告知旧票/发言作废；仍失败请新建会话，不猜测推进。细节见阶段机 SX。
