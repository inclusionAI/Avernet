# Plan — Effective MCP Artifact 完备性修复

## Baseline and seams

- Base: 最新 `REL20260828`。
- Domain seam: `RuntimeProjectionResolver` 的 Effective MCP union。
- Query seam: `SkillSetService.collect_bot_active_mcps()`。
- Artifact seam: `ConfigComposerInputCollector.mcps()`。
- Installation、Dispatcher、DeviceSync 和 Teclaw wire 不修改。

## Vertical slices

### 1. Skill dependency 进入 Artifact Effective MCP

Red：真实 Installation 数据中只有 Skill Installation 和 dependency，断言
`collect_bot_active_mcps()` 返回 dependency，同时 MCP Installation 仍为空。

Green：抽取 Resolver 已有的 Effective MCP union，让 Runtime Projection 与查询链路共用；
查询链路从 `BotCapabilityStateReader.active_skill_assets()` 读取已安装 Skill 声明。

### 2. 完整 Artifact 使用 strict policy context

Red：断言 Artifact collector 调用 Effective MCP 查询时声明 strict policy context。

Green：`ConfigComposerInputCollector.mcps()` 固定传入 `strict_policy_context=True`。

## Verification

1. 每个 slice 单文件执行 red -> green。
2. 运行 Runtime resolver、Effective MCP union、Collector 和 Teclaw DeviceSync 测试。
3. 运行受影响 Skill Center、Config Compose 与 Backend 测试套件。
4. 运行 Backend lint/static/type gates；无法运行的门禁精确记录原因。
5. 对 `REL20260828...HEAD` 执行 Standards 与本 Spec 双轴 review，修复阻断发现后提交。
