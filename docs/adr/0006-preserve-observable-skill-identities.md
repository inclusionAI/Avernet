---
status: accepted
---

# 对外形成历史事实后保留 Skill 身份

只有从未提交发布，且不存在 Publication Attempt、External Skill Mapping、Published Skill Version、Bot Binding、Service Bot Artifact 或其他历史引用的纯草稿 Skill，才允许作为“取消创建”物理移除主体。

删除后继草稿只放弃本次升级，不删除稳定 Skill 身份或已有历史。Skill 一旦形成需要保留的历史事实，普通删除只能转为 Skill Retirement，并保留最小 Skill Identity Tombstone。`skill_id`、`skill_uuid`、历史版本、外部映射和审计不能随最后一个草稿一起删除，身份永不复用。

该边界保留了纯草稿取消创建的简单性，同时保证已经进入发布、外部分发或下游消费链路的身份仍能支持审计、Artifact 精确寻址和历史关系解释。
