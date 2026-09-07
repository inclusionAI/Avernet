---
status: accepted
---

# 有 Bot 引用时阻断 Skill 下线

只要存在有效 Bot Binding 或仍被服务 Bot Artifact 使用，Skill Version Withdrawal 或 Skill Retirement 就必须被前置阻断；风险提示和用户二次确认不能绕过。执行下线时必须重新检查引用，避免预检查与执行之间新增引用。

发布或升级不采用相同阻断规则。发布前展示全部受影响 Bot 及 Owner，用户知晓后可以继续；发布成功后更新 Bot 草稿态解析和草稿容器，已经发布的服务 Bot Artifact 保持不变，直到服务 Bot 下次发布才使用新 Skill 版本。

新版本发布后，旧版本成为历史版本，不等同于被下线或删除。既有 Artifact 仍可通过精确的 `skill_uuid + external_version_key` 获取其固化版本。
