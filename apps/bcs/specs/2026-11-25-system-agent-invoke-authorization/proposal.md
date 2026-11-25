## Why

生产环境的系统级 Agent 需要可独立查询、审批和撤销的 A→B 调用授权；把这种关系伪装成好友、任意 inline rules 或 owner/manager 会混淆运行权限、社交关系和管理权。当前 EdgeGrant 只有 `permission_profile`、`rules`、`owner`、`manager`，尚无此业务类型和专用公开生命周期。

最初交付 Draft OpenSpec；用户随后于 2026-11-25 授权实施并创建 PR。当前 PR 交付 **reader-first 类型/存储读端/旧路径隔离**，未完成管理/审批/运行放行；完整 SHALL 仍为最终验收条件。范围和证据见 [implementation.md](implementation.md)。

## What Changes

- 提议新增一等 `GrantKind::SystemInvoke`，JSON/DB 稳定值 `system_invoke`，仅表示同一受信生产安全域内系统 Bot A 对系统 Bot B 的单向、非传递直接调用权。
- 明确 canonical `bot_uuid` 身份、受信系统资格、环境/Provider/租户范围、真实调用者与 originator 校验；不将 `agent_code`、AgentCard 或自报标签当凭证。
- 为候选边读取扩展 `list_active_grants` / `is_authorized`，但不扩大 friend/authority 或任意通用调用点的有效权限；新增显式 operation-aware 调用决策，禁止把新边转换为 default profile。
- 拟新增独立申请/审批/直接授予/撤销/查询 Service API 与 HTTP 管理资源，不复用 friend-connections；新增 `RequestKind::SystemInvoke` 与相同字符串。
- v1 使用 `grant_ref_id=0`、`rules=NULL`、`same_as_from`；不借 `rules` 偷渡未执行的细粒度策略。未来细粒度授权必须另行演进契约与执行链。
- 规定精确撤销、事务/CAS、审计、幂等、出入度治理、索引与数据库成本，以及双数据库和分层回归门禁。
- **Compatibility risk**：新增枚举不是“所有读者无感”的兼容变更；先部署 readers/consumers，再启用 writers；未知类型不得映射成旧授权类型。

## Capabilities

### New Capabilities

- `system-agent-invoke-authorization`: 系统级 Agent 单向调用授权的类型、身份、生命周期、执行、治理和兼容测试。

### Modified Capabilities

无现有 `openspec/specs` capability 可供 delta 继承。本 capability 的 requirements 显式列出对既有 repo 读取契约的增量及不变的好友/管理权契约；不是声称旧代码已满足新契约。

## Impact

- Owning module：Avernet BCS。`bcs-domain` 纯类型；`bcs-service-api::{application,core,port}` 独立契约；`bcs-edge-permission` 策略；`bcs-edge-permission-store` SQL/内存；HTTP adapters 薄映射；composition roots 装配受信资格/身份提供者与开关。
- Consumers：Admission / AuthzContext / direct A2A、friend 查询与撤销、owner/manager、OpenAPI/SDK、审计与治理客户端。当前实际调用点和证据见 [research.md](research.md)。
- 不改 AgentCard 标准、AgentCode 唯一性、GroupCard、群组成员/消息权、Provider 身份协议或其他仓库。本规范仅定义 OCB 等宿主未来须满足的契约，不编辑宿主代码。
- 历史 migration 冻结；未来新增索引/请求元数据/审计与幂等存储须独立 additive migrations，SQLite 与 MySQL 完整迁移链验证。当前不新增可执行 SQL。
- 状态、决策及导航见 [spec.md](spec.md)，设计见 [design.md](design.md)，验收 requirements 见 [specs/system-agent-invoke-authorization/spec.md](specs/system-agent-invoke-authorization/spec.md)，实施任务全部留待独立授权。
