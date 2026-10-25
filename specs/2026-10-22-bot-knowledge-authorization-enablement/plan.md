# Plan

One current selection table with revision CAS, native editor checks and current
service-Bot draft checks. Catalog receives the existing business Bot and target
user identifiers. Keep source Bot primary keys internal to application storage.
No changes to MCP producers, their Protocols or transport. External reserved UUID
support requires a separately agreed contract and migration, not an active path.
Remove feature-owned membership capture/outbox and history additions.

Review fixes keep the single current-state table. Reclaim a space-mismatched
row under the Bot lock using the absent-view CAS, preserving the stored counter;
never carry enablement into the new space without catalog validation. Add
current-space enabled and cursor indexes, plus indexes for both existing
service-Bot draft-query shapes (source and legacy default). Publish records,
status semantics and runtime version selection remain unchanged. DDL is review
only: one new relation table and supporting indexes, no deployment execution.

## PR #3087 review repair (2026-10-25)

- 在 current draft 排序前，通过 source Bot 主键的 correlated EXISTS 验证
  tenant/env 归属，保留历史 source PK；不把 default lineage 统一改成当前 PK。
  无法归属的孤儿 source 记录 fail closed，避免跳过未知 successor 后暴露旧 draft。
  source 归属通过主键点查；原 publication latest 索引及事务内复查不变。
- Personal Space 显式 Editor 与 Owner 使用原生语义，不强制存在 Space-member row；
  Team Space 仍要求 Owner/actor ACTIVE membership。两种 Space 均在事务内复查
  Space、显式 Editor 和服务 Bot draft 状态。
- 复用统一 Singlebox runner、local SQL seed、真实 HTTP 和本地 catalog Plugin API。
  完整 Core 与两条 Router、两个声明的 catalog 方法均进入分母。未消费的
  list_in_space 保留为未命中，不增加 test-only 调用伪造覆盖。
  单独 Backend 的实测阈值不能替代 shared-stack/all-module 或完整 artifact 门禁。
- 不改变平台授权、MCP Header、发布生命周期、数据库 schema 或生产配置。
