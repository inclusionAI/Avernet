# Plan — MCP mutation scope 最小修复

## Baseline and seams

- Base: 最新 `REL20260828`。
- Service seam: `SkillSetManagementService` 与 `DirectActivationService` 的公开命令。
- Repository seam: `CapabilityDesiredStateRepository` 返回的 `DesiredStateMutation`。
- Runtime seam: `BotRuntimeProjectorProtocol.project(..., scope=...)`。
- Adapter、DeviceActivated、Dispatcher 与 Plugin 不改动，只做回归验证。

## Vertical slices

### 1. SkillSet deactivate 不释放 MCP 配置

Red：repository result 带 MCP code，断言 projector 收到空 claimed/released。

Green：deactivate 改为固定 `ProjectionScope(skills=True, mcp=True)`。

### 2. SkillSet MCP no-op 不产生 delta

Red：add/remove 返回 `changed=False, mcp_codes=empty`，断言 scope 为空。

Green：add/remove 由 `scope_from_result` 构造 scope。

### 3. Repository 只返回实际 MCP delta

Red：真实 UoW 覆盖 ordinary Set、Default exclusion 和 Direct MCP 的 changed/no-op 结果。

Green：changed mutation 返回对应 code，no-op 保持空集合；不做全局引用查询。

### 4. Direct MCP no-op 不产生 delta

Red：Direct activate/deactivate no-op 时断言 scope 为空。

Green：Direct commands 使用与 SkillSet MCP 相同的 result-to-scope helper。

## Verification

1. 每个 slice 执行对应单测 red→green。
2. 运行两个 Service 测试文件和 repository UoW 测试文件。
3. 运行 Python format/lint/type/static checks。
4. 运行受影响 Backend 测试套件。
5. 对 `REL20260828...HEAD` 执行 Standards 与本 Spec 双轴 review。
6. 修复阻断发现、复验、提交并更新现有 PR。

## Deliberate non-work

不实现全局 Retained 查询、跨 SkillSet release guard、inactive cleanup、async delete、
DeviceActivated inactive hydration、provider/engine 分叉或额外运行时协议。
