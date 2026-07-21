# KNOWLEDGE.md

优先知识源：
- 当前任务上下文和用户的明确指令。
- 团队成员给出的专业结论和证据。
- 已确认的项目文档、架构约束、运行日志和测试结果。
- 历史决策记录、风险清单和复盘结论。

使用原则：
- 当前事实优先于历史印象。
- 证据优先于权威感。
- 最小验证路径优先于复杂规划。

世界杯自媒体 Demo：
- 工作流需要内容主编、赛事数据核查、战术解说、短视频编导和增长运营五个专业 Bot。
- 代表 Bot 是世界杯内容主编。
- 世界杯运营总监发现阶段交付的是高层需求简报：业务目标、期望交付物、可调参数维度和硬约束，不是已经填值的执行任务简报。
- 世界杯自定义协作的候选可调维度包括比赛选题、内容模式、文风、目标受众、目标时长、发布平台列表、事实截止时间和联网失败兜底；执行时按每秒约 4.3 个中文字符推导口播目标字数及上下百分之十范围。具体值由未来的自定义协作执行请求提供，世界杯运营总监不在发现或设计会话里追问。
- 工作流 YAML 定义参数读取、演示默认值、节点产物和协作关系，不固化本次世界杯运营总监会话中的比赛、文风、受众或字数值。
- 同一 service invocation 每次只生成一种用户指定文风，可通过新的 invocation 改变比赛、文风、受众、目标时长、平台和事实边界，无需重新设计 YAML。
- 文风只改变表达，不改变事实；女性球迷定位不得推断知识水平或兴趣动机。

自定义协作知识边界：
- 详细 schema 只以 bcs-coordination Skill 的 `references/custom-collaboration-schema.md` 为准，不在 profile 中维护第二份 schema。
- YAML 只声明逻辑 participant binding，不包含 Bot UUID 或 participant role。
- Demo 使用 acyclic、bot_task、bot_binding 和 complete transitions，且恰好一个 final_output。
- MVP Demo 不使用 variables、events、initial_node、actions、output_contract、runtime_actor、guard 或 judge。

BCS Session 边界：
- `bcs_route` 只能选择当前 session 的参与者，不能从用户原会话路由到刚创建的设计群。
- `bcs chat --bot-uuid` 创建或复用一对一会话，不能用来向已经创建的自由聊天群发言。
- 向指定设计群发言只能使用 `bcs session chat --session <session_id> --message <message>`。
- 设计群完整消息必须使用 `bcs-cli -j ... session messages` 读取；普通预览会截断长回复。
- OpenClaw 可能无法在内容主编回复后再次唤起设计群中的世界杯运营总监。用户原会话中的 controller 必须主动读取群历史，不能依赖该回调来继续主流程。
