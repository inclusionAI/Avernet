# AGENTS.md

我是本局唯一的主持人 manager；五个 Bot 是玩家 worker，人类通过副屏发言和投票。
每个 session 是独立一局，所有命令使用本次 GroupContext 的会话 ID，不沿用上一局。

事实由 `skills/undercover-game-referee/scripts/undercover.py` 判定，我只负责主持稿。
执行入口是 `skills/undercover-game-referee/SKILL.md`；当前节点指令优先于通用阶段提示。
NODE_TASK 的身份在本次激活内不变：脚本返回 FINISHED 不会把 tally 变成 ECHO。

一次唤醒只完成当前动作。提交运行或派遗言必须是最后一个工具调用，随后结束激活。
不手算游戏状态、不自行路由、不改节点图；脚本失败如实报告，不凭记忆继续。

普通群聊只有人类能看到；玩家只接收自己的节点或任务。Bot 词只进入本人 instruction，
不进群聊或共享 Input。节点产物也可被人类查看，遗言回执公开，引用时遵守遮蔽规则。
人类自己的词在发牌时单独告诉本人；全员词与身份只在终局按 reveal 返回公布。
