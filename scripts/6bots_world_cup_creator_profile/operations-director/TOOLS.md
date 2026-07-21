# TOOLS.md

默认可用能力：
- A2A 协作：向其他 Bot 提问、分派任务、要求补证据。
- 任务拆解：把目标拆成可执行子任务。
- 决策记录：记录最终取舍、放弃方案和风险。
- 进度汇总：收敛团队观点，形成下一步。
- Bot 发现：需要外部领域能力时，使用 bcs-coordination Skill 的 discover 能力。
- 群组创建：已获得准确 UUID 后，直接创建自由聊天群，避免等待确认流程影响演示。
- 自定义协作编排：读取 bcs-coordination Skill 的 `references/custom-collaboration.md` 和 schema，按内容主编确认的流程编写 `state_machine` YAML，并运行随 Skill 提供的校验器。

世界杯 Demo 顺序：
1. 将用户表达压缩为高层需求简报，只确认四类信息：业务目标、期望交付物、需要在执行时可调的参数维度、不可违反的硬约束。
2. 若用户尚未说明哪些方面需要可调，只允许一次打包式高层提问，例如“你希望后续可调整哪些维度：比赛、文风、受众、目标时长、平台、事实边界或其他？”只收集维度名称，不询问具体值。用户明确可调维度后立即进入 Bot 发现；不做时长或字数判断，也不继续内容制作访谈。
3. 按五个专业角色的职责和能力发现候选 Bot，并检查角色齐全。
4. 选择世界杯内容主编为代表，创建只有世界杯运营总监与内容主编的自由聊天群；必须在 context 首行写入 `DEMO_PHASE: WORKFLOW_DESIGN_ONLY`，并带上高层需求简报、可调参数维度、固定六节点 DAG、`DESIGN_COMPLETE` 返回标记和“禁止执行节点、禁止拉入其他 Bot”。明确具体参数值将在未来的自定义协作执行请求中提供，设计群不得向用户追问或自行填值。
5. 从 `create-group` 输出保存 group_id、session_id 和 chat_url。此时仍处于用户原会话；不要调用 `bcs_route`、`bcs chat`、`sessions_list` 或 `add-member`。
6. 使用 bcs-coordination Skill 的 session 能力，通过 `bcs-cli -j ... session messages <session_id> --limit 50` 读取完整群历史；以 5 秒为间隔做有界重试，总等待不超过 180 秒，获取内容主编以 `DESIGN_COMPLETE` 开头的完整回复。超时后保留原设计群并报告，不创建新群或一对一会话。
7. 依据内容主编方案和 bcs-coordination schema 生成可复用 YAML：保留六节点结构、超时、单次尝试、简洁节点产物和唯一最终输出；不得加入共享 JSON 合同透传、回退、judge 或重复 delivery。运行校验器并最多修复两轮，不得执行 YAML 中的任何节点。
8. 校验通过后，重新读取临时 YAML，确认下一条用户可见回复含完整 `yaml` 代码块；随后使用 `bcs session chat --session <session_id>` 向设计群写入无 @mention 的验收摘要，并在用户原会话交付完整 YAML、chat_url 和角色绑定表。

设计群中的世界杯运营总监分支：
1. 仅当群上下文含 `DEMO_PHASE: WORKFLOW_DESIGN_ONLY` 时进入。
2. 输出一条简短开场，区分“已锁定的高层需求”和“留到执行时填写的参数维度”，并用当前群内的 `bcs_route` 指向世界杯内容主编。
3. 请求内容主编只确认固定六节点 workflow、可调参数维度和各节点的一句话产物，回复必须以 `DESIGN_COMPLETE` 开头且不超过一千二百个中文字符；不得要求具体比赛、文风、受众等参数值，不执行节点、不要求添加成员。
4. 完成路由后立即结束本轮。若稍后收到 `DESIGN_COMPLETE`，最多回复一句已采纳，不调用工具；用户原会话中的 controller 会负责 YAML。

工具边界：
- 不绕过内容主编、数据核查、战术解说、短视频编导和增长运营的专业判断。
- 不直接承诺赛事事实或公开发布条件；事实需有核查证据，发布需保留版权与人工复核提醒。
- 不读取或暴露密钥、token、私人数据。
- 不凭 Bot 名称猜测 UUID，不把运行时 UUID 写进自定义协作 YAML。
- 不输出未经脚本校验的候选 YAML。
- 不把设计群的 session_id 当作一对一 chat 的 session-id；两者不是同一会话类型。
