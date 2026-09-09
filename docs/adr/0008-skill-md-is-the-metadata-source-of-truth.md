---
status: accepted
---

# SKILL.md 是名称和描述的唯一事实来源

当前 Skill Draft 的 `SKILL.md` frontmatter 是名称和描述的唯一事实来源。不存在独立于内容文件的“基本信息”写入口；如果提供表单式编辑，它也必须修改同一份草稿 `SKILL.md`。

`ac_skill.name/description`、列表缓存和搜索索引只能作为派生投影，不能反向覆盖内容文件。投影写入失败必须暴露并修复，不能让多个字段各自演变成事实来源。

发布成功时，从冻结的 `SKILL.md` 形成 Published Skill Version 的名称和描述快照。后继草稿修改名称或描述不会改变历史版本；不同页面在同时存在已发布版本和后继草稿时选择哪个快照展示，属于显式 UI 投影合同。
