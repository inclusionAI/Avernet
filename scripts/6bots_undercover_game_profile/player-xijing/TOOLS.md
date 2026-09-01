# TOOLS.md

## 技能

- `skills/undercover-game-player`：我在这局里怎么识别任务类型、怎么发言、怎么投票、什么绝对不能说。收到任务就按它执行。
- `skills/bcs-coordination`：BCS 命令行用法。我基本用不到，只在需要理解协同上下文时参考。

## 我不需要工具

发言、投票、遗言、预备，每一种任务**都只需要把那一句话作为最终回复**，不需要调用任何协同工具，也不需要执行任何命令。

## 工具边界

- 不用 `bcs_assign_task`（那是主持人的工具），也不用 `bcs_send_task_message`。
- 不用 `bcs session chat` / `bcs chat` / `create-group` / `add-member`。
- 不查询群历史、不查询其他玩家、不查询运行状态。
- 不读取、不打印 token，不覆盖 `BOT_DATA_DIR`。
