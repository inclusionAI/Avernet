---
status: accepted
---

# Skill 身份跨版本保持稳定

Skill 是跨版本稳定的主体，升级只创建新的 Skill Version，不创建新的 Skill；因此 Skill 级空间归属、权限和外部映射引用稳定的 Skill 身份，具体内容、发布和版本生命周期引用 Skill Version。选择这一模型是为了避免把版本记录身份误作长期 Skill 身份，同时允许现有 `ac_skill.id` 在没有主体合并需求的前提下继续作为稳定 `skill_id`。
