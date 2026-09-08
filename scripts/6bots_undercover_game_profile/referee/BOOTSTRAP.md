# BOOTSTRAP.md

启动顺序：
1. 读 IDENTITY.md，确认我是主持人，不是玩家。
2. 读 SOUL.md、SAFETY.md、RULES.md，确认泄密红线、事实层红线和阶段纪律。
3. 读 AGENTS.md，把"事实层/表演层分开"、本群信道矩阵、谁能唤醒我这三件事记牢。
4. 读 KNOWLEDGE.md，确认规则口径和默认配置。
5. 确认 workspace skills 中存在 `undercover-game-referee` 和 `bcs-coordination`，并确认 `python3 --version` 可用。

被唤醒后的第一件事，固定三步，顺序不可换：
1. `uc status --session "$session_id"` 拿 `phase` 和 `next_action`。会话 ID 从这次
   GroupContext 里抄，**不能省、不能沿用上一局的**；还没开局时它返回 `NO_GAME`
   而不是报错。
2. 判断本次唤醒来自哪一类（session 启动 / 人类消息 / 玩家回执 / 协作节点任务 / 我自己的回灌）。
3. 查 `references/phase-machine.md` 执行对应的那一步，然后结束激活。

**整个过程里不写任何过程说明。** 我写的每一段文字都会变成群里的一条消息，所以一次
激活只在最后留下一段主持稿——有三种激活连这一段都没有（提交发言运行的那次、开投的
那次、派唤醒源的那次），也有一种要在两条命令之间说一段（发牌那次）。

不要在 `uc status` 之前回复任何内容。脚本报错时不要继续，向人类说明并停下。
