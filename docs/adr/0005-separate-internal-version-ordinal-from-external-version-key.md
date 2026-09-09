---
status: accepted
---

# 内部版本序号与外部版本键分离

OCB/TC 使用不可变 `skill_version_id` 标识一条已发布技能版本记录，并在同一 Skill 内使用从 1 开始单调递增的正整数 `version_ordinal` 排序。产品将序号 `n` 展示为 `Vn`。发布尝试失败或重试不产生新的版本序号。

Skill Center 或 Skill Repo 使用的精确版本字符串属于 External Skill Mapping，例如 `1.0.0`。它不参与 OCB 内部排序，也不能充当 OCB 版本身份。服务 Bot Artifact 固化 `skill_uuid + external_version_key`，确保同一发布物的扩容、重启与回滚使用相同内容。

不采用 `DECIMAL(10,1)`：它无法自然表达第十次以上的普通升级，也不能无损表达 semver。内部也不直接采用 semver，因为当前产品只有线性升级序列，没有 major/minor/patch 的用户决策语义。
