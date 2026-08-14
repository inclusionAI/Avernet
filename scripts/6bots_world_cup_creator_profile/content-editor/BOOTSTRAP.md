# BOOTSTRAP.md

启动顺序：
1. 读取 IDENTITY.md 和 SOUL.md，确认当前角色。
2. 读取 AGENTS.md，确认五人小队分工。
3. 读取 RULES.md 和 SAFETY.md，确认事实、受众和版权边界。
4. 读取 KNOWLEDGE.md、TOOLS.md 和 OUTPUT.md，确认节点输入输出。
5. 若群上下文含 `DEMO_PHASE: WORKFLOW_DESIGN_ONLY`，这是设计任务而不是节点执行任务；直接按高层需求和可调维度设计可复用 workflow，不要求具体参数值或上游产物。
6. 进入自定义协作执行节点后，只读取本次 `[Input]` 和直接 `[Upstream Outputs]`；不要寻找或生成共享 JSON 合同。
