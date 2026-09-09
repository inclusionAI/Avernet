# TOOLS.md

使用 `python3 skills/undercover-game-referee/scripts/undercover.py`（以下简写 uc）。
工作目录为当前 Bot workspace。所有命令显式使用本次 GroupContext 的 `--session`。
常规动作和参数见游戏 SKILL.md；仅遇到参数问题时读 references/commands.md 对应小节。

`begin`、`open-round`、`open-vote`、`finish` 已封装 BCS 操作，不拆成底层调用。
`open-*` 必须是本次激活的最后一个工具调用，成功后按阶段收尾：open-round 播报 announcement，open-vote 按返回提示立即结束；不追加状态查询或派任务。
`bcs_assign_task(target_bot, message)` 仅用于开票稿回灌后的遗言，原样使用 render-ping 返回。
派单也必须是最后一个工具调用，且身后不能有等待执行的协作节点。

终局 tally 是 state_machine 上下文，完成命令为 `uc finish --session '<当前会话ID>'`。
不寻找 bcs_task_complete、不调用 bcs_route、不路由给自己。先公布 reveal 真相，再完成会话；
完成命令只用于本次 votes-set 刚判胜的会话，失败如实报告，不吞掉退出码。
关闭失败后的重试按 SKILL.md「关闭失败恢复」执行；只有 finish 成功才确认会话已关闭。

认证交给 CLI。不读写 token、不覆盖 BOT_DATA_DIR、不用 curl 绕过 CLI，
不打开含全员词语的 YAML/状态文件，不用 bcs chat 向玩家另开会话。
