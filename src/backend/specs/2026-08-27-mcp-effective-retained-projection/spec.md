# MCP mutation scope 最小修复

## Status

本文件是 2026-08-28 owner review 后的权威范围，取代此前“建立完整 Effective / Retained
双投影”的宽版方案。实现以 `REL20260828` 为基线，只修正 mutation scope 的声明与透传。

## Problem

`SkillSetManagementService.deactivate()` 将 mutation result 中的 MCP code 当作
`released_mcp` 传给 runtime projector。Set 变为 inactive 时 membership 仍然存在，因此
这些 code 只应退出 callable/Effective 状态，不应被当作物理配置删除候选。

MCP add/remove、Default exclusion/unexclusion 和 Direct activate/deactivate 还存在另一类
scope 问题：它们从请求参数直接构造 delta。即使 repository 返回 `changed=False`，请求中的
code 仍会被错误声明为 claimed/released。

## Required semantics

| Command | Runtime projection scope |
| --- | --- |
| SkillSet activate | `skills=True, mcp=True, claimed_mcp=result.mcp_codes` |
| SkillSet deactivate | `skills=True, mcp=True`，claimed/released 均为空 |
| MCP add / Default unexclude / Direct activate | 从 mutation result 构造 claimed delta |
| MCP remove / Default exclude / Direct deactivate | 从 mutation result 构造 released delta |
| MCP mutation `changed=False` | 空 MCP delta |
| inactive ordinary Set membership add/remove | 保持 REL 行为：只写 DB，不访问 runtime |
| inactive Set delete | 保持 REL 行为：同步 Service API，只写 DB |
| DeviceActivated | 保持 REL 行为，不扩大到 inactive configuration references |

Repository 只在 mutation 实际改变 MCP desired state 时，将对应 code 放入
`DesiredStateMutation.mcp_codes`；no-op 返回空集合。`scope_from_result` 只负责把该结果翻译为
`ProjectionScope`，不重新查询数据库，也不引入新的持久化状态。

## Compatibility

- HTTP 路由、状态码和响应结构不变。
- `SkillSetManagementService.delete_set()` 保持同步，不向 adapter 传播 async。
- 现有 mutate-project-compensate 合同不变。
- Service API、Plugin API 和 Dispatcher 边界不变。
- 不增加表、字段、迁移、provider/engine 分叉或 task queue。

## Explicitly out of scope

- 同一个 MCP 被多个 SkillSet 引用后的全局 release 计算。当前 ownership policy 已阻止同一
  MCP 加入不同 SkillSet；本次不修复历史异常数据或 Skill dependency 交叉引用。
- 全局 Retained resolver/read projection。
- `McpConfigurationQueries` 或任何 `DesiredStateDeletion`/Retained 持久化模型。
- inactive remove/delete 的 best-effort runtime cleanup。
- DeviceActivated 对 inactive MCP configuration 的额外恢复。
- Teclaw whole-artifact、并发、性能、队列及完整协作者权限模型扩展。

## Acceptance criteria

1. deactivate mutation 即使返回 MCP codes，projector 收到的 claimed/released 仍为空。
2. changed MCP add/remove、Default exclusion/unexclusion、Direct activate/deactivate 只声明实际
   changed code。
3. 上述 MCP mutation no-op 时，projector 收到空 MCP delta。
4. inactive membership mutation 与 delete 保持 `REL20260828` 的 runtime 行为。
5. DeviceActivated 和所有 delivery adapter 相对 `REL20260828` 无行为变化。
