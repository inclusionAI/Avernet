# 门店「经营目标→经营方案」场景设计包

收拢门店剧本的人读与角色资料(与运行模板分离)。

- **运行模板(YAML,框架加载)**: `task_plan/plans/merchant-operations-goal-to-plan.yaml`
- **BCS 协同模板种子(不可移)**: `src/bcs/seeds/collaboration-templates/zh-CN/merchant-operations-goal-to-plan.yaml`,
  登记于 `src/bcs/seeds/collaboration-templates/registry.yaml`(优先级 32, tags:[serial,branching,risk-review])。
  BCS 按 `collaboration-templates/` 目录扫描加载种子,移走会让门店协同模板注册消失 — 故留原处,仅在此交叉引用。
- **文档**: `template.zh-CN.md`(模板说明)、`relay-story.zh-CN.md`(串行接力故事)
- **角色人设**: `bot-profiles.7bots/`(7 个门店 bot 的 IDENTITY/KNOWLEDGE,内嵌 20260901_* 真 bot id)
