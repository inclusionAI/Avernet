---
status: accepted
---

# Skill 的空间归属由唯一关系表达

不在 `ac_skill` 增加 `scope_type`。`ac_skill` 只表达跨版本稳定的 Skill 身份；Skill 与 Space 的归属由唯一的 Skill Space Ownership 关系作为事实来源。Ownership 的环境字段必填，同一环境下一个 Skill 最多一条 Ownership，Personal/Team 只由 `ac_space.space_type` 区分。

跨空间使用属于只读引用，不产生第二条 Ownership；把内容复制到另一个空间会创建新的 Skill 身份。历史未绑定空间的 `ac_skill` 可以作为 legacy 数据继续存在，只在显式迁入空间时创建 Ownership；新建空间 Skill 缺少 Ownership 属于数据异常。

创建 Skill 主体、初始内容或版本事实和 Ownership 必须在同一数据库事务内完成。Skill Center 等外部调用不进入该事务，通过持久化任务、幂等重试和补偿实现最终一致性。
