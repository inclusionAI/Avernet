# Spec: Isolate Space knowledge membership persistence

## Status

2026-10-26：用户已确认隔离要求及有限收口方向；本次补充是待实现规格，不表示当前 PR 已满足。
审查基线：PR #3091 `7b73c4f73e0e672a8852d3e0594f252f93eac030`，目标 `dev@980bc488395c7e626eafad704432b4237dd4f5b1`。

## Problem

当前成员管理、审批及 Space 创建路径直接调用知识同步持久化 helper。即使默认 Sink 关闭且 Space 从未激活，源业务仍查询同步表；缺表会使原有功能失败。SQLite 还会引入同步专用写保留。后台 pending 聚合虽然只返回 32 个 Space，但单个 Space 的积压版本扫描量无界。

## Mandatory Requirement

**从未启用知识功能的部署，即使不建 `ac_space_knowledge_sync`，原有功能也必须正常工作。此要求是合并验收条件，不是后续优化项。**

“正常工作”包括已有认证、权限、响应、持久化及失败回滚语义，不允许仅返回成功却没有保存业务数据，也不允许通过吞掉数据库错误满足要求。自动 schema bootstrap 建表不能作为缺表验收的替代。

## Ownership and References

- Space 与 Membership 事实仍归 TC/Backend；知识资产与最终知识 ACL 仍归 Provider。
- 遵循 `docs/arch/arch.rules.md` Rules 5、10、12、14、16、21、24；不改变现有架构宪法。
- 保留 `docs/adr/0007-separate-space-governance-from-skill-grants.md` 与 `0010-teamclaw-is-authority-for-managed-skills.md` 的权限及事实所有权。
- 原知识库 HTTP、Provider/Sink 协议继续由 `contract.md` 定义；本规格只收口持久化扩展和调度成本。

## User Stories

1. 未部署知识能力的维护者不执行知识同步 DDL，也能使用已有 Space、成员、审批与 Skill 功能。
2. 已启用能力的维护者可暂停远端投递，但不会丢失暂停期间的成员移除、降权或 Space 删除事件。
3. 既有模块维护者通过一个窄的 Space 事务扩展接入，不需要理解知识接收端、同步表或队列协议。

## Deployment States

| 状态 | 必须具备的行为 |
| --- | --- |
| 从未启用记录扩展、无历史 | 默认空扩展；缺同步表可启动并执行原有写入；不查询该表，不启动同步扫描 |
| 记录扩展启用、Space 未激活 | 可以检查激活状态；不生成快照、不施加接收端人数/字节限制 |
| Space 已激活且投递正常 | 源数据与完整不可变快照同事务；按序确认 |
| Space 已激活但投递暂停/异常 | 记录扩展保留，继续记账和身份保护；只暂停远端发送 |
| 请求关闭记录扩展但存在当前作用域历史 | 不允许静默降级为空扩展；启动校验明确拒绝这种不安全装配 |

最后一种校验只在同步表存在时读取当前环境/应用的历史存在性；表不存在且未启用时，不执行该表的 SELECT/写入。未启用不等于数据库错误可忽略，不能把权限错误、连接故障或损坏表当作“没有历史”。

## Acceptance Criteria

- **AC1 — Missing-table availability:** operator-managed SQLite、默认完整 DI/HTTP 装配、自动建表关闭；仅有原功能所需 schema，显式断言同步表不存在。团队/个人 Space 创建、Skill Space 创建 seam、单个/批量成员增删、角色变更、改名、删除、人工与 AUTO Space 加入审批均保持原结果与实际持久化。
- **AC2 — Inactive isolation:** AC1 的请求/事务不访问同步表、不执行知识专用 SQLite no-op UPDATE、不触发 Provider、Sink、队列或同步定时扫描。通用 Space 权限及源数据锁仍可保留，不承诺所有 SQL 与旧版本逐条相同。
- **AC3 — Explicit assembly:** 默认为空扩展；记录能力由经严格校验的部署配置在 composition root 选择，不按 Sink 可用性、请求字段、异常或业务模块内环境变量动态选择。配置错误不能被 lifecycle discovery 吞掉。
- **AC4 — Atomic enabled behavior:** 已启用时保留原子快照、首次激活竞态保护、2000 人/1 MiB 准入、no-op 不增版本、FIFO、精确 hash 确认与删除历史。所有源写入入口使用同一事务扩展。
- **AC5 — Pause and restart:** 有历史时不能关闭记录扩展；保持记录启用而关闭 Sink 后，新增、移除、角色变化和删除仍按序保存。重启后继续投递；retired scope 的 SQLite ID 不可复用。
- **AC6 — Bounded dispatch:** 同一 Space 的 1/1000/100000 个 pending 版本，不导致单轮候选定位线性枚举全部版本；最多返回 32 个不同 Space，并保留继续游标。调度元数据查询不读取完整 payload。
- **AC7 — Existing boundary parity:** Skill 草稿、发布、授权、安装、审批权限与原 HTTP DTO 不变；源代码依赖守卫防止既有 writer 再直接导入知识快照实现。
- **AC8 — Honest validation:** 先运行缺表 RED，再做 pilot 和 GREEN；相关模块、架构与真实 file-SQLite 并发回归通过。MySQL/OceanBase、企业装配、统一 Singlebox、部署及迁移未验证时必须单独报告，不复用旧 Head 结果。

## In Scope

窄的持久化内部事务扩展、默认空实现、知识记录实现及显式装配；所有已受影响 writer 的接入收口；安全关闭检查；候选查找与标量读取优化；针对上述 AC 的回归与文档更新。

## Out of Scope

新事件总线、通用 UoW 平台、第二张进度表、源表/共享队列表 schema 变更、外部 ECB adapter、知识库产品 API 扩展、Skill 业务重构、线上 DDL、推送、合并或部署。后台吞吐/后继任务加速不在本轮必做范围，保留现有投递语义。

## Open Questions

无待用户决定的产品问题。实现中若发现企业 composition root 需要适配，应报告准确位置；公共库实现不等于企业部署已完成切换。
