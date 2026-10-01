# TOOLS.md

- BCS 协作流程、允许的命令和 one-shot 门禁以 `AGENTS.md` 及当前安装的 `bcs-coordination` Skill 为准。
- 只依据结构化工具结果判断成功，并保留真实的 ID、状态和错误。
- 工具失败时停止相关动作并报告原因，不拼接结果、不伪造回执、不静默降级。
- 外发参数先做隐私检查；`collaborate run` 成功后不再调用其他工具。
