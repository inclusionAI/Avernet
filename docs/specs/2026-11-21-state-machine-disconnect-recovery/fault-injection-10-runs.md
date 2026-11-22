# StateMachine 断连故障注入验收

- 日期：2026-11-21
- 场景：连续 10 次在发言 StateMachine Run 中杀掉一个玩家 Bot
- SLA：60 秒内重新打开同一阶段
- 结果：10/10 recovered，stuck=0，late=0
- Terminal result checkpoint：10/10 delivered
- 结构化结果：10/10 含 `reason`，10/10 未出现 `disconnected` 或 `not connected` 原始错误文本
- 恢复耗时中位数：23.6 秒
- 恢复耗时最大值：52.5 秒
- 运行脚本：`singlebox/agents/6bots_undercover_game_profile/tools/fault_inject.py`

每次注入均记录原 Run 失败、Bot Gateway 自动恢复、主持人创建新 Run，以及原 Run 的 terminal result checkpoint 最终为 `delivered`。
