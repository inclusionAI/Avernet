# singlebox e2e skill 包

本目录下三个真实可安装 skill 包(对齐 spec §G3 / 2026-08-13-task-realcase-singlebox-e2e 的 G3)。
运行时经 `/api/skills/upload` 上传、skillset activate 安装到 owner bot(planning/search)/ worker bot(acceptance)。
**案例知识(节点名 / bot 映射 / 确定式判定)只在这些 skill 里,框架代码零 case 知识**(AC-8 回归断言 grep)。

## 结构

```
skills/
├── planning/SKILL.md     # owner bot:计算 gap → 产 List[TaskSpec];返 []=gap 闭=验收通过
├── search/SKILL.md       # owner bot:候选集里决出 who+how → 4 态 SearchResult
└── acceptance/SKILL.md   # worker bot:叶子 execute 时自验收(方案Y) → 折叠进回投 result.success/fail_detail
```

## 上传与激活(集成用例 setup)

- 上传:每个 skill 目录制 zip → `POST /api/skills/upload`(对齐 `SkillService.upload_skill` 期望:含 `SKILL.md` 的目录)。
- 安装:activate skillset 到 owner bot(planning + search)/ worker bot(acceptance)。
- 跳过条件:gated `SINGLEBOX_TASK_E2E=1`;`SkillParser` 解析各 SKILL.md frontmatter 应得 name/description/version/author/tags。

## 与框架 prompt 契约对齐

- planning 返回格式 = `List[TaskSpec]`(对齐 `_parse_children`)。
- search 返回格式 = 4 态 JSON(对齐 `_parse_search_result`),`HIT_MULTI_BOTS` 携 `collab_mode`/`group_name`/`members_info`/`definition_yaml`/`manager_bot_id`。
- acceptance 不独立回投,折叠进 `TaskCallbackData.result`(对齐 `CallbackAdapter.adapt`:success→PASS / fail_detail→gaps)。
