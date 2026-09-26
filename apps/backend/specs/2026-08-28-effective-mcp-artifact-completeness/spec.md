# Effective MCP Artifact 完备性修复

## Status

本文件记录 2026-08-28 owner review 后批准的独立 follow-up。它不改变
`2026-08-27-mcp-effective-retained-projection` 已交付的 mutation scope 语义，只补齐完整
Artifact 消费者读取 Effective MCP 时遗漏的 Skill dependency，并收紧完整 Artifact 的
policy context 失败行为。

## Problem

`RuntimeProjectionResolver` 已将显式 MCP Installation、System Default MCP 和已安装 Skill
声明的 `mcp_dependencies` 合并为 Effective MCP。但
`SkillSetService.collect_bot_active_mcps()` 只返回 Default policy 与显式 MCP
Installation，导致 `ConfigComposer` 生成的 Artifact 遗漏 Skill dependency。

`ConfigComposerInputCollector.mcps()` 同时使用宽松的 policy context 查询。完整 Artifact
是覆盖式输出；若模板或 Default policy 上下文查询失败并降级为空，继续投递会把暂时无法
读取误解释为删除。

## Required semantics

```text
Effective MCP
  = Explicit MCP Installation
  ∪ Dependencies(Installed Skills)
  ∪ Effective Default MCP
```

- `BotMCPInstallation` 只保存显式安装事实；Skill dependency 是从
  `BotSkillInstallation -> ac_skill.mcp_dependencies` 派生的供应，不物化为 MCP
  Installation。
- `RuntimeProjectionResolver` 与 `collect_bot_active_mcps()` 必须共用同一 Effective MCP
  union 算法。
- Default exclusion 只移除 Default 供应；同 code 仍由显式 Installation 或 Skill
  dependency 供应时继续 Effective。
- `ConfigComposer` 收集完整 Artifact 时必须使用 strict policy context。必要 policy
  上下文读取失败时 compose 失败，不产生或投递缩水 Artifact。
- 同一 code 来自多个供应时只输出一次，并保留现有 metadata precedence。

## Compatibility

- 不新增或修改数据库表、字段、迁移和 Installation provenance。
- 不修改 HTTP、Service API、Plugin API、Dispatcher 或 Teclaw `/api/v1/bot/apply` wire。
- 不增加 provider/engine 业务分叉；strict policy context 适用于所有完整 Artifact compose。
- 现有 Default row、Default policy、ordinary SkillSet metadata precedence 保持不变。

## Explicitly out of scope

- Local Skill 和 Center/Phase 2 Skill 投影。
- Teclaw 一次 mutation 的重复 whole-artifact delivery；由独立工作处理。
- Retained MCP 持久化模型、inactive cleanup、DeviceActivated 扩展。
- 并发、性能、task queue 和完整协作者权限模型。

## Acceptance criteria

1. 已安装 Skill 的 MCP dependency 出现在 `collect_bot_active_mcps()` 和最终 Artifact 输入中。
2. dependency 不创建 `BotMCPInstallation` 行。
3. dependency 与显式 Installation 或 Default 重合时去重，任一剩余供应可保持其 Effective。
4. Runtime Projection 与 Artifact collection 使用同一个 dependency decoder 和 union 算法。
5. 完整 Artifact policy context 查询失败时 compose 失败，不继续投递部分结果。
