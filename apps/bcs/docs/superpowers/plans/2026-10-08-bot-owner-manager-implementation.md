# BCS Bot Owner / Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用显式 owner/manager 边统一 BCS Bot 控制权，实现 mine/协作平权、来源隔离的团队同步、接收确认式 ownership 转交和撤权后的持续授权。

**Architecture:** HTTP/WS 只调用 application；注册的 authority Hook 调 Core，Core 通过聚合 Repo 读写角色事实。Bot 初始化/删除、管理来源变更和转交共享数据库中的 Bot 序列化边界，角色、版本、审计/回执同事务提交。旧 created_by 保留历史用途，不能作为新权限兜底。

**Tech Stack:** Rust / Tokio / async-trait、现有 DbPlugin、SQLite/MySQL、Axum、OpenAPI YAML；前端契约消费使用现有 TypeScript/React/Jest。

**Spec:** `apps/bcs/docs/superpowers/specs/2026-09-18-bot-manage-permission-design.md`（2026-10-08 复审修订）。

## Global Constraints

- 状态：**待评审的实施计划，所有任务未执行；本次只写文档，不自动 commit/push。**
- 核对基线是本地 `94bae68a6cdfb606a6ee0bbc103d7772d5806213`，不是对远端此刻最新状态的声明；执行前重新核对差异和 migration 最大版本。
- 路径全部相对仓库根；不再使用 `src/bcs`。保留一份合并 spec，不拆成相互竞争的 owner/team 设计。
- 阅读根 AGENTS、CONTEXT-MAP、四份架构规则、BCS AGENTS/CLAUDE 和所触及 crate CONTEXT；缺少 CONTEXT 的新边界补充文档。
- `access_relation: "owner" | "manager"` 必填、非 null；owner 优先；本人 Human self row 兼容，不产生可转交 Human owner。
- `created_by`、legacy `is_creator`、Bot ID 后缀、signed Gateway `owner_id` 均不得成为当前 Human 控制权兜底。
- 仅转交 BCS ownership；接收方确认生效；原 owner 保留可撤销 manager；同 Bot/env 最多一笔 stored pending；默认期限固定 7 天。
- manager 来源为 direct/manual、team/team_id、ownership_transfer/transfer_id；owner 来源固定 owner/owner；team 同步不改变其他来源。非角色边（现有 permission_profile/rules；好友是 default-profile 的 permission_profile 边，不存在 friend GrantKind，实施不得新增）固定 `management_source_kind/id = none/none`，迁移回填与各 store 新写入取同一值。
- direct DELETE 只撤 direct/ownership_transfer；存在 team 来源时仍是 manager；转交接受不删除接收人的 team 来源。
- 混合身份写操作审计分开记录 operator_user_id（真实 Human caller）与 effective_actor_id（选定 Bot）；仅记录被代理 Bot 不满足 spec §12.1(6) 的授权变更追溯。按 §12.5 使用独立 `bcs_bot_action_audits`；必需 `BotOperationContext` 贯穿真实写端口，同库变更与审计同事务，外部副作用采用 admitted/终态记录且不声称网络可回滚。
- Human sponsorship 不要求受控 Bot 互为好友；public Group 仍只接纳 public Bot；Bot-originated 路径不继承 Human sponsorship。
- runtime grants 仅 PermissionProfile/Rules；owner/manager 不进入好友、A2A grant、反向关系或好友外部同步。
- team 路径保留 `/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}`；不用 Human/App/Bot Principal，不等于完全匿名；服务凭证须经 Gate 0 确认。
- 服务身份审计不能伪造 Human；审计 actor_kind 为 human/service/system，actor_id、operation_id 必填，team 回执关联 idempotency_key。
- WS 决策为 Deliver/SkipMessage/InvalidateBinding；消息不可见只丢弃本条，不能撤销合法连接；真实资源/身份失权才失效绑定，基础设施失败单独 Err。
- WS 每批最多 128 个完整授权上下文；按 env/可信身份边界、真实 User、资源、View Actor、action、消息可见范围判定，不按 user_id 共享最终决定。排队后出队重新鉴权，无 TTL 正向缓存。
- 所有新权限读返回 Result；任何必要边、回执、审计或提交失败均回滚/报错；已提交 invalidated 映射 409，不能随异常回滚。
- 新 migration 号；不修改已提交 SQL、Rust 历史 migration body/checksum；schema 在首次提交前按同一次发布的完整字段合并。
- 修改 source file 不超过 1,000 行；不运行全局 cargo fmt；超限文件只按本次触及职责拆分，不加 allowlist 绕过。
- 不发布角色读写只切换了一半的版本；中间任务可以独立测试/审阅，但不是独立生产发布许可。
- 文中 commit 是未来执行时的建议检查点，只有获得用户提交授权才执行；不推送、不更改 Draft PR 状态。每个检查点只用显式路径 `git add <本任务文件>` 后提交对应消息，不用 `git add .` 收入无关改动。

## Review Focus

1. 平台无 Human Principal 的合法 sync：成功写 service 审计；空/无差异快照仍有幂等回执（Tasks 4、7、13）。
2. 同用户两个 Bot 视角仅一个撤权：只停止失权视角；慢 writer 排队期间撤权不泄露消息。合法 participant 遇到 FullOnly/他人 Directed 消息只跳过本条，下一条 public 仍可收（Tasks 15、16）。
3. B 以 team manager 接受 owner，再转给 C 后撤销非 team 来源：B 仍因 team 保持 manager（Tasks 4、8）。
4. 首次 owner 初始化写失败遇到预先存在的 runtime Bot：返回错误但不删除原 Bot；重复 Provider/ref 不重放凭据（Tasks 6、17）。普通业务审计失败同库回滚；外部 I/O 已发生后的 DB 失败保留 admitted，不假报完成/回滚或自动重放（Tasks 9—12）。
5. move 的响应丢失、空快照重放与并发 Human 删除：同 key 重放原结果、不复活旧 team、不产生悬空角色（Tasks 5、7、8、17）。

## 范围、决策门槛与执行顺序

这是同一 authority 切换下相互依赖的一组能力，不按子系统分别上线。分三组审阅：存储与原子性（1—8）、消费者与传输（9—16）、迁移与产品验收（17—20）。前端为显式消费方任务，不扩展 Backend/Engine 资产转交。

### Gate 0：开始业务代码前记录决定

- [ ] 在 spec §1.3 记录需求方对“manager 可再授权且授权不级联撤销”的明确接受或收紧决定；本计划按现有草案允许再授权编写。若收紧，只改变 manager mutation 的授权策略和 AC09，不新增运行时开关让不同实例采用不同规则。
- [ ] 确认 team 服务凭证、service ID/允许 Bot/team scope 的归属、密钥注入方式，以及平台能否承诺同一 Bot 的 sync/move 按提交确认顺序串行投递；没有 membership_version 时不承诺自动识别任意乱序。
- [ ] 确认 7 天转交期限；记录仍为仅 BCS ownership。确认失败初始化的恢复与旧数据冲突治理责任人。
- [ ] 将批准结果写入 spec 后再实施。没有确认时可以评审本计划、验证现有接口能力，但不挂载新授权写入口；不得把用户要求“写计划”当成批准所有默认值。

执行顺序：默认按 Task 1—20 串行实施；核心依赖为 `1→2→3→4→5`，6/7/8 依赖这组存储能力，9—14 完成消费者/API，15—16 完成持续授权，17—20 完成迁移/装配/消费方/集成。任务分组不授权自动委派；若选择并行执行，先固定接口并避免共享 mod.rs、bootstrap、migration 的写冲突。

### 预先锁定的文件职责

| 边界 | 新建/拆分落点（均在既有 crate 内） | 职责 |
| --- | --- | --- |
| 领域 | `apps/bcs/crates/contracts/bcs-domain/src/bot_authority.rs`、`ownership_transfer.rs` | 角色、来源、审计操作者、转交状态 |
| 服务合同 | `apps/bcs/crates/service-api/bcs-service-api/src/types/bot_authority.rs`、`types/ownership_transfer.rs`、`types/team_manager_sync.rs` | transport-neutral commands/results |
| 应用/Core/Repo | 同 crate 的 `application/v1/bot_manager.rs`、`ownership_transfer.rs`、`team_manager_sync.rs`、`core/bot_authority.rs`、`port/repo/bot_authority.rs` | 用例、集中授权、聚合持久化；不让 application 依赖 Repo |
| 授权实现 | `apps/bcs/crates/services/bcs-edge-permission/src/authority/` | reads、manager、transfer、team；与 Connect 分开 |
| 角色持久化 | `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/` | SQL 方言、事务、审计、回执与严格解码；Memory实现复用bot-store同一状态边界 |
| 业务审计 | `apps/bcs/crates/service-api/bcs-service-api/src/types/bot_operation.rs`；各资源 store 的 `src/action_audit.rs` | 共享纯身份/记录描述，Bot/Group/Session/file 等各自 store 拥有同事务写入；不新增通用审计服务或跨store concrete依赖 |
| 生命周期 | `apps/bcs/crates/services/bcs-bot-store/src/ownership_initialization.rs`、`ownership_deletion.rs` | 扩展现有 Bot/Provider 创建和删除的完整事务，不复制 Bot INSERT 到 HTTP/authority Core |
| 应用用例 | `apps/bcs/crates/application/v1/bcs-app-bot/src/management.rs`、`ownership.rs`、`team_sync.rs` | 身份验证、application error 和 projection |
| HTTP | `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/` 及 `internal/routes/` 的对应新文件 | DTO/route/envelope，不查数据库 |
| 持续授权 | `apps/bcs/crates/application/v1/bcs-app-session/src/delivery_authorization.rs`、`apps/bcs/crates/adapters/ws/bcs-ws/src/web/protected_delivery.rs` | 前者做资源授权，后者管理带上下文的队列、连接身份和发送 |
| 测试 | `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/bot_authority.rs`、各 crate integration tests | 共享 conformance；driver 选择真实实现 |

新目录的 mod.rs 只导出职责模块。现有 edge lib 拆出 grant/profile/request 与各自 tests；Session/Invitation lib 拆出 authorization/queries/mutations/tests。本计划不把业务 SQL、Axum DTO 或具体 store 放入 service-api。

### 公共命名与片段约定

以下新合同在 Task 1 定义，后续不得换名造成上下游漂移。示例是相应文件中的关键测试/实现片段，不是声称现有仓库已经存在这些类型。测试的 `repo` 是 Task 3 的 `Arc<dyn BotAuthorityRepoPort>`；`h` 是该任务定义的测试夹具；HTTP 的 `app` 使用现有 route tests 的 Router 装配方式。新增测试夹具只在 test-support/test driver 中，不能被生产代码调用。

- `BotAccessRelation::{Owner, Manager}`；缺少角色由 `Option` 表示，损坏/未初始化由错误表示。
- `ManagementSource::{Direct, Team(String), OwnershipTransfer(String)}`；owner 不使用该 manager 枚举。
- `AuditActor::{Human { user_id }, Service { service_id }, System { name }}` 仅用于角色生命周期。
- 普通业务另用 `BotOperationActor::{Human { user_id, effective_actor_id }, Bot { bot_id }, System { system_id, effective_actor_id }}`；`BotOperationContext { operation_id: String, actor: BotOperationActor }` 为必需值。Task 1 定义、Task 2 建表、Tasks 9—12 分别贯穿实际写入口，不能等 Task11/18 事后追加日志。
- `ManagerMutation::{GrantDirect { user_id }, RevokeNonTeam { user_id }}`。
- `ManagerMutationResult { changed: bool, remaining_team_sources: Vec<String> }`；HTTP DELETE 将 changed 投影为 revoked，不将 revoked 等同最终失权。
- `OwnershipState { owner_user_id: String, ownership_version: u64 }`。
- `TeamManagerOperation::{Sync, Move { new_team_id: String }}`。
- `TransferAction::{Accept, Reject, Cancel}`；`TransferStatus::{Pending, Accepted, Rejected, Cancelled, Expired, Invalidated}`。
- `CommittedTransferOutcome::{Receipt(OwnershipTransfer), OwnerChanged, Expired, Invalidated}`；OwnerChanged/Expired/Invalidated 是已提交领域结果而非数据库异常。decide 重试已终态记录时按持久行的 `terminal_reason` 重新推断上述结果（Invalidated/owner_changed 重试仍得 OwnerChanged），不得只按状态枚举放弃区分；映射细节见 Task 14。
- `AuthorityError` 作为 ServiceError 的强类型业务分支，由 application 统一映射固定 code；不匹配错误消息字符串。
- 新边界的 R25 harness 随各自任务新增：`bot_authority_repo_port_contract_tests`、`bot_authority_core_service_contract_tests`、`ownership_migration_service_contract_tests`、`ownership_migration_core_service_contract_tests`、`bot_manager_service_contract_tests`、`team_manager_sync_service_contract_tests`、`ownership_transfer_service_contract_tests`、`delivery_authorization_service_contract_tests`。分别放 test-support 的 repo/core/application 同名职责文件，各生产实现提供 `tests/conformance_*.rs` driver 调用；不是等 Task18 才补空入口。

---

### Task 1：角色形状、来源和服务错误合同

**Files:**
- Create: `apps/bcs/crates/contracts/bcs-domain/src/bot_authority.rs`
- Create: `apps/bcs/crates/contracts/bcs-domain/src/ownership_transfer.rs`
- Modify: `apps/bcs/crates/contracts/bcs-domain/src/edge_permission.rs`
- Modify: `apps/bcs/crates/contracts/bcs-domain/src/lib.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/types/bot_authority.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/types/bot_operation.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/types/ownership_transfer.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/types/team_manager_sync.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/types/mod.rs`、`types/error.rs`、`application/v1/error.rs`
- Test: `apps/bcs/crates/contracts/bcs-domain/tests/bot_authority.rs`（新建）

**Interfaces:** Consumes 现有 GrantKind/EdgeGrant 与 ServiceError；Produces 上述公共类型，以及 `ManagementSource::storage_parts(&self) -> (&str, &str)`、`ManagementSource::revocable_by_direct_api(&self) -> bool`。新增 `AuthorityError` 枚举，业务分支为 OwnershipNotInitialized、CorruptAuthority、Forbidden、InvalidSubject、Conflict；通过 ServiceError 承载，真实基础设施失败沿用 ServiceError 的存储/内部失败分支。application 再映射 spec 的固定 code。

- [ ] **Step 1 — 写领域 RED 测试。**

```rust
#[test]
fn direct_delete_does_not_revoke_team() {
    let team = ManagementSource::Team("team-a".into());
    assert!(!team.revocable_by_direct_api());
    assert!(ManagementSource::Direct.revocable_by_direct_api());
    assert!(ManagementSource::OwnershipTransfer("t-1".into()).revocable_by_direct_api());
    assert_eq!(team.storage_parts(), ("team", "team-a"));
}
```

同文件增加 owner/manager serde round-trip、未知枚举拒绝、owner 固定 owner/owner、空 team/transfer ID 拒绝；明确 role grant_ref_id=0、rules=null、same_as_from，运行时 grant 保留原形状。service-api 在 `types/bot_operation.rs` 的纯类型测试区验证 Human/Bot-only 审计语义，不把service-api类型反向导入domain：

```rust
let human = BotOperationActor::Human { user_id: "a".into(), effective_actor_id: "bot-x".into() };
let bot = BotOperationActor::Bot { bot_id: "bot-x".into() };
assert_eq!(human.operator_user_id(), Some("a"));
assert_eq!(human.effective_actor_id(), "bot-x");
assert_eq!(bot.operator_user_id(), None);
assert_eq!(bot.effective_actor_id(), "bot-x");
```

方法签名为 `operator_user_id(&self) -> Option<&str>`、`effective_actor_id(&self) -> &str`。同文件定义 `BotActionAuditPhase::{Applied,Admitted,Completed,Failed,Unknown}` 与 `BotActionAuditRecord`；字段逐项遵守 spec §12.5 表格，operator kind/ID只从typed actor投影。动作/资源种类为受控枚举，step_key由应用/领域内部步骤生成，客户端不能指定；step key匹配但记录内容不同必须冲突；比较内容不包含首次数据库生成的时间，合法重试保留原时间不更新。

- [ ] **Step 2 — 运行 RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-domain --test bot_authority`；预期缺少新类型/方法，不把环境构建失败算 RED。
- [ ] **Step 3 — 实现最小纯类型/来源策略。**

```rust
impl ManagementSource {
    pub fn storage_parts(&self) -> (&str, &str) {
        match self {
            Self::Direct => ("direct", "manual"),
            Self::Team(id) => ("team", id.as_str()),
            Self::OwnershipTransfer(id) => ("ownership_transfer", id.as_str()),
        }
    }
    pub fn revocable_by_direct_api(&self) -> bool {
        matches!(self, Self::Direct | Self::OwnershipTransfer(_))
    }
}
```

为 GrantKind 增加 Owner/Manager，更新所有 exhaustive match；旧 EdgeGrant（permission_profile/rules，含承载好友的 default-profile profile 边）构造由 typed constructors 填 `none/none` 来源固定默认值，不能让未知/缺失角色来源自动当 direct。SQL 解码错误不 serde-default 成合法角色。

- [ ] **Step 4 — GREEN 与传播检查。** 重跑领域测试及 `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-service-api bot_operation`，`cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets`；所有枚举消费者编译，runtime 默认仍不允许新角色。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): define explicit bot authority and source contracts`；仅暂存本任务文件及必要编译传播文件。

### Task 2：完整 schema、新迁移和事务可表达性

**Files:**
- Create: `apps/bcs/migrations/mysql/031_bot_authority.sql`
- Create: `apps/bcs/migrations/sqlite/032_bot_authority.sql`
- Modify: `apps/bcs/crates/bootstrap/bcs/src/migrations.rs`
- Create: `apps/bcs/crates/bootstrap/bcs/tests/bot_authority_migration.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/tests/authority_transaction_capability.rs`
- Read: `apps/bcs/crates/plugin-api/bcs-db-api/src/transaction.rs`

**Interfaces:** Consumes DbStatement、DbTransactionStep::{Query,Execute,ExecuteChecked}、query-result binding、stop-on-no-rows；Produces schema 和真实方言下可验证的 Bot 行串行化，不新增应用层数据库会话。

- [ ] **Step 1 — 写升级/事务 RED。** 从现有完整迁移链建立旧库，执行新迁移；向同 Bot 插入两个 approved owner、两个 pending，第二次应失败。另验证一人同时保留两个 runtime ref 和多个 manager 来源。

```rust
assert!(insert_first_owner.is_ok());
assert!(insert_second_active_owner.is_err());
assert!(insert_second_runtime_ref.is_ok());
assert!(insert_second_team_source.is_ok());
assert_eq!(owner_count_after_rollback, 1);
assert_eq!(pending_count_after_rollback, 1);
```

上述变量分别保存紧邻断言前的真实INSERT执行结果和COUNT查询结果，不由mock布尔赋值；测试同时覆盖 case-sensitive User IDs、历史 revoked 恢复和 MySQL generated slots。追加业务审计的Human字段完整性/Bot-only NULL合法性、operation/step唯一性、同事务业务UPDATE后audit INSERT失败两者回滚测试；schema必须支持admitted与completed为同操作的不同步骤，不能将一次操作限成一行。

- [ ] **Step 2 — 运行 RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test bot_authority_migration`；当前缺字段/约束，应有明确 SQL 失败。
- [ ] **Step 3 — 写一次发布所需完整迁移。** 执行前确认新号未被占用；MySQL/SQLite 独立递增。表/约束如下：

| 对象 | 本任务必须一次建全 |
| --- | --- |
| edge_grants | 非空 source kind/id；owner/manager/ref 形状；保留 runtime ref 维度的唯一键；approved owner slot |
| bcs_bots | 非空 ownership_version，历史默认 0 |
| bot_ownership_transfers | spec §5.2 全部字段、pending slot、幂等键、收发件索引 |
| bot_manager_changes | source/edge/subject、actor_kind/id、operation_id、可空 idempotency_key、grant/revoke、时间；追加日志索引 |
| bcs_bot_action_audits | spec §12.5 全部字段；operator_kind/id、Human时必填的operator_user_id、effective_actor_id、resource/action/phase；唯一env/operation_id/step_key；追加日志不作当前权限源，独立于manager审计 |
| bcs_message_deliveries / bcs_chat_runs | 新增 operation_id 引用同事务 admitted 身份快照，历史行可NULL、不回填伪造Human；新代行命令必填，恢复按精确操作/步骤读取context，不能只按资源找最新审计 |
| bot_manager_sync_operations | env/service/Bot/URL team/idempotency_key 唯一；operation_id、规范 payload、持久最小结果、时间；无差异也有回执 |
| bot_team_manager_sources | env/Bot/team 当前绑定状态，空 manager 快照仍可表示 active team；move 原子停旧启新，不是 Human 名单副本 |
| bot_ownership_initializations | Bot/env/初始版本/owner/来源/系统或注册操作者/批次/时间；仅审计，不作当前 owner 事实源 |

角色 source 编码与非角色 `none/none` 固定编码分别校验（回填与新写入同一值，三个存储实现共用同一常量定义，不各处另起字面量）；验证MySQL复合索引总字节预算与精确ID语义，不能截断ID或用非唯一前缀降低正确性；唯一索引可统一含 grant_ref_id，因为角色固定为 0，其去重等价于 spec 的六列。SQLite 如需重建表，只在新迁移复制现有所有列、索引和原 ID，验证好友/ref 不丢失；MySQL 使用 generated nullable slot，不写 PostgreSQL partial index。

事务证明必须包含“先保存 invalidated，再以正常领域结果提交”和“真正写失败整体回滚”。最小插件调用形状：

```rust
let steps = vec![
    DbTransactionStep::Query(lock_bot_statement),
    DbTransactionStep::ExecuteChecked {
        statement: changing_cas_statement,
        expected_affected_rows: 1,
    },
    DbTransactionStep::Execute(audit_statement),
];
let committed_steps = db.transaction(steps).await?;
```

`lock_bot_statement` 在 MySQL 为精确 env/bot_uuid 的 FOR UPDATE；SQLite 用持有写锁的事务路径；所有政策条件在事务中的条件 SQL 复核，不在返回结果后才决定已提交的写。分别构造成功/失效条件分支并利用现有 stop-on-no-rows 的“提交前缀”语义，不用 ExecuteChecked 的失败来返回已提交 invalidation。若该组合在真实插件不能表达，停止后续写任务并先提交 transport-neutral 插件合同修订和双方言 conformance；不得以多个事务顶替。

- [ ] **Step 4 — GREEN。** 跑完整 SQLite migration 测试、事务 capability 测试和可用 MySQL live driver。无法连接真实 MySQL 时记录未验证，不宣称可上线。确认旧 migration 文件和 checksum 没有变化。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): add bot authority schema and atomic storage constraints`；提交后冻结本迁移，不在后续任务回填列；本计划尚无已提交SQL，本轮新增业务审计应一并建入Task2。执行时若已提交/部署迁移，则只新增后续编号，绝不回写。

### Task 3：严格 authority Repo、共享 conformance 与集中 Hook

**Files:**
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/bot_authority.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/core/bot_authority.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/bot_authority.rs`、`apps/bcs/crates/application/v1/bcs-app-bot/src/authority.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/mod.rs`、`reads.rs`、`codec.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/src/authority/mod.rs`、`reads.rs`
- Create: `apps/bcs/crates/test-support/bcs-test-support/src/contract/repo/bot_authority.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/tests/conformance_bot_authority.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/src/memory_authority.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/memory.rs`
- Modify: 上述 service-api/core/repo、两个 edge crate、test-support 的 mod.rs/lib.rs 导出和 Cargo dev-dependencies

**Interfaces:** env 绑定在实例，不由 HTTP body 注入；以下是必需方法，不能默认返回空成功。

```rust
#[async_trait]
pub trait BotAuthorityRepoPort: Send + Sync {
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState>;
    async fn role(&self, user_id: &str, bot_id: &str)
        -> ServiceResult<Option<BotAccessRelation>>;
    async fn roles_for(&self, pairs: &[(String, String)])
        -> ServiceResult<Vec<Option<BotAccessRelation>>>;
}
```

`roles_for` 返回位置与输入一致；不存在权限为 None，任何损坏/查询失败为 Err，不把漏行当允许。Core `BotAuthorityCoreService` 暴露相同三方法；application `BotAuthorityHook` 暴露 `can_manage(user_id, bot_id) -> ServiceResult<bool>`、`require_owner(user_id, bot_id) -> ServiceResult<()>`，通过 Core 而非直接 Repo 查询。

**Test harness:** 测试driver用 `AuthorityRepoHarness` 持有 `repo: Arc<dyn BotAuthorityRepoPort>`；其 `seed_owned(bot_id, owner_user_id) -> ServiceResult<()>`、`seed_human(user_id) -> ServiceResult<()>`、`fail_next_write() -> ()`、`audit_count() -> ServiceResult<u64>` 由 Memory/SQL driver 实现，仅供测试。Task3 的 seed_owned 在测试driver内按Task2 schema原子写合法 Bot/version/OwnerEdge（用于建立读测试前置状态，不依赖尚未实施的Task5）；Task5完成后生命周期测试必须经生产初始化合同建立对象。seed_human 调真实Human materialization，损坏测试单独使用driver注入，测试helper不成为生产claim入口。

- [ ] **Step 1 — 写 RED。** 同一共享合同对 Memory、SQLite 运行：

```rust
h.seed_owned("bot-a", "a").await.unwrap();
assert_eq!(h.repo.role("a", "bot-a").await.unwrap(), Some(BotAccessRelation::Owner));
assert_eq!(h.repo.role("b", "bot-a").await.unwrap(), None);
let roles = h.repo.roles_for(&[("a".into(), "bot-a".into()), ("b".into(), "bot-a".into())])
    .await.unwrap();
assert_eq!(roles, vec![Some(BotAccessRelation::Owner), None]);
```

加 version=0→OwnershipNotInitialized、version>0/owner 缺失→CorruptAuthority、Actor 删除/env 错误/非法形状、数据库失败不变空 Vec。recording Core 必须证明 Hook 实际调用。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-edge-permission-store --test conformance_bot_authority`。
- [ ] **Step 3 — 实现严格批量读和 Hook。**

```rust
async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
    Ok(self.core.role(user_id, bot_id).await?.is_some())
}
```

先校验 Bot/version/唯一 owner/主体有效性，再判 owner 优先或任一 manager；查询绑定 env 与精确 ID。批量 SQL 按完整 pair 关联，不能两个独立 IN 集合产生授权笛卡尔积。将旧 edge store 超限 lib 的 grant/profile/request 按职责分出文件并保持其测试；新 authority 不复用吞错 bool 方法。

Memory 角色与 Bot 生命周期使用同一 `MemoryBotRepo` 内的 authority/lifecycle 临界区，由它实现新 Repo trait，bootstrap 只暴露 trait；不能在两个互不共享的 Memory store 各加一把锁。所有改到的 Memory 文件也按职责拆至上限内。

- [ ] **Step 4 — GREEN。** 跑 authority conformance、edge/profile/request 旧 conformance、Core/Hook recording 测试；SQL 次数断言单个批次无逐 pair N+1。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): centralize strict bot authority reads`。

### Task 4：人工 manager 变更、来源范围和真实操作者审计

**Files:**
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/manager.rs`、`audit.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/src/authority/manager.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/{types,core,port/repo}/bot_authority.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/memory_authority.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/tests/manager_mutation.rs`

**Interfaces:** `mutate_manager(actor: AuditActor, bot_id: &str, mutation: ManagerMutation) -> ServiceResult<ManagerMutationResult>` 同时加入 Core/Repo；Human API 只传 Human，service 不允许借此写 direct。`list_managers(bot_id, offset, limit)` 返回 owner_user_id、按 User ID 排序去重的 manager/source 页面；owner 只在独立字段出现。

- [ ] **Step 1 — 写 RED。**

```rust
let actor = AuditActor::Human { user_id: "a".into() };
let grant = ManagerMutation::GrantDirect { user_id: "b".into() };
assert!(repo.mutate_manager(actor.clone(), "bot-a", grant.clone()).await.unwrap().changed);
assert!(!repo.mutate_manager(actor.clone(), "bot-a", grant).await.unwrap().changed);
let removed = repo.mutate_manager(actor, "bot-a", ManagerMutation::RevokeNonTeam { user_id: "b".into() })
    .await.unwrap();
assert!(removed.changed);
assert_eq!(removed.remaining_team_sources, vec!["team-a"]);
assert_eq!(repo.role("b", "bot-a").await.unwrap(), Some(BotAccessRelation::Manager));
```

fixture 先用 driver 建立 B 的 team-a 来源，来自正式 source 语义而非 friend 边。补 team-only 自 DELETE→changed=false、最后来源自撤后重试403、owner target409、revoked 行同 ID 恢复、审计只记变化、manager 互撤锁顺序、audit/commit 注入失败全回滚。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-edge-permission-store --test manager_mutation`。
- [ ] **Step 3 — 同 Bot 锁内校验、改边、审计。** 事务内检查 actor 仍有权、目标是同 env live Human、目标非 owner。撤权谓词必须保留来源：

```sql
UPDATE edge_grants SET status = 'revoked'
WHERE env = ? AND to_id = ? AND from_id = ? AND status = 'approved'
  AND grant_kind = 'manager'
  AND management_source_kind IN ('direct', 'ownership_transfer');
```

更新前选取实际变化边并批量 INSERT SELECT 审计，operation_id 关联多边；授予恢复同一 source 行，不 INSERT IGNORE 后返回 revoked。审计失败不 commit；剩余 team 来源从同事务结果取得。依据 Gate 0 固定的授权策略判断再授权能力。

- [ ] **Step 4 — GREEN。** Memory/SQLite 共同行为套件和 MySQL live 并发；对大来源集合断言批量 SQL、锁等待有界、不扫所有 Bot。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): add source-aware manager mutations and audit`。

### Task 5：注册与删除的原子生命周期存储边界

**Files:**
- Create: `apps/bcs/crates/services/bcs-bot-store/src/ownership_initialization.rs`、`ownership_deletion.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/registration_create.rs`、`bot_provider_storage.rs`、`memory_registration_create.rs`、`memory_bot_provider_storage.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/bot.rs`、`bot_provider.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/tests/ownership_lifecycle.rs`

**Interfaces:** 新 `OwnershipInitialization { owner_user_id: String, actor: AuditActor, operation_id: String }` 定义于 types/bot_authority.rs；扩展现有 create-once/Provider create 输入，在同一次创建提交里消费它。裸 runtime connect 不传初始化；有已验证 Human 的正式 register 必须传。提供 `initialize_existing_ownership(bot_id, initialization) -> ServiceResult<OwnershipState>` 给 version=0 的受治理入口，不自动认领。

- [ ] **Step 1 — 写 RED。** 测试分别建新 Bot 和已有 runtime Bot，注入 owner/profile/audit 步骤失败：

```rust
assert!(new_registration_result.is_err());
assert!(!new_bot_exists_after_failure);
assert!(existing_initialization_result.is_err());
assert!(runtime_bot_exists_after_failure);
assert_eq!(runtime_bot_version_after_failure, 0);
assert_eq!(default_profile_id_after_retry, default_profile_id_before_retry);
```

变量通过真实 repo 查询取得。并发 register 同 ID、Provider/ref tombstone、删除对 accept、删除 owner Human、Human 删除与 manager 新授予竞争分别建测试，不只测顺序成功。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-bot-store --test ownership_lifecycle`。
- [ ] **Step 3 — 在现有创建事务中加必要 steps。** 普通/Provider 创建 SQL 仍由 bot-store 负责；本任务的初始化 helper 仅生成该 store 的 owner/default-profile/初始化审计 steps，与其 Bot/gateway INSERT 一次提交。后续管理/transfer SQL 仍在 edge-permission-store；两个 store 不互相导入 concrete，也不依次调用各自提交的方法。用共享领域编码和 conformance 防止形状漂移，不引入通用事务框架。

```sql
UPDATE bcs_bots SET ownership_version = 1
WHERE env = ? AND bot_uuid = ? AND ownership_version = 0 AND is_deleted = 0;
```

该 CAS 与唯一 OwnerEdge、Human 物化、default profile ensure、初始化审计在同事务，预先存在 Bot 的其他字段不得由失败补偿删除。删除使用同一 Bot 锁：撤所有角色、终结 pending、标记删除；Human 删除需锁相关 Bot（稳定 ID 顺序）并与其自身可用性校验协调，live owner 禁止删，不让并发授予产生孤儿边。

- [ ] **Step 4 — GREEN。** 生命周期、现有 `registration_create`/`conformance_bot_provider_repo`/Provider webhook 合同；确认恢复不读回/泄露 runtime token。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): initialize and retire bot authority atomically`。

### Task 6：真实注册 facade、onboarding 和旧写入口不再重置 owner

**Files:**
- Modify: `apps/bcs/crates/application/v1/bcs-app-register/src/lib.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/core/provider_registration.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/application/onboarding.rs`、`human_actor.rs`、`provider.rs`、`bot.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/register.rs`、`core/provider_registration.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-register/tests/ownership_initialization.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/register.yaml`

**Interfaces:** Consumes Task 5 lifecycle commands；Produces register 成功即已初始化的合同，runtime connect 的 version=0 例外保留，Provider owner scope 不变。

- [ ] **Step 1 — 写 RED。** 替换当前 `onboard_failure_is_swallowed` 期望：

```rust
assert!(register_result.is_err());
assert_eq!(owner_before_reconnect, owner_after_reconnect);
assert_eq!(created_by_before_transfer, created_by_after_reonboard);
assert_eq!(version_after_reonboard, version_after_transfer);
```

新测试覆盖 HMAC v1/v2、无 Human runtime、Provider self-service webhook override、AgentPass agent_code、重复 ref409 和凭据不重放；所有结果来自真实 facade 调 recording lifecycle Core。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-register`；吞错测试改期望后必须失败。
- [ ] **Step 3 — 从可信 token scope 传入初始化信息。**

```rust
let initialization = OwnershipInitialization {
    owner_user_id: verified_owner_user_id,
    actor: AuditActor::Human { user_id: verified_operator_user_id },
    operation_id,
};
```

这三个变量来自注册专用凭证验证和服务生成 operation ID，不来自任意 created_by body。移除必要 onboarding 写失败后仅 warn 的成功分支；重复 ensure-human/reconnect/Provider switch 不写 owner、manager/version/created_by。只有明确未初始化且可信首次上下文可调用 Task 5 初始化；当前 owner 通知解析与外部 Provider scope 分开。

- [ ] **Step 4 — GREEN。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-register -p bcs-bot` 和 register HTTP 合同测试；记录旧行为的有意变更。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `fix(bcs): preserve authority across registration and reconnect`。

### Task 7：team sync/move 聚合事务和持久幂等

**Files:**
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/team_sync.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/src/authority/team_sync.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/types/team_manager_sync.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/core/bot_authority.rs`、`port/repo/bot_authority.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/tests/team_manager_sync.rs`

**Interfaces:** `TeamManagerSync { service: VerifiedTeamManagerService, bot_id: String, team_id: String, operation: TeamManagerOperation, manager_user_ids: Vec<String>, idempotency_key: String }`；verified service为共享types/team_manager_sync.rs类型，包含service_id/env和允许Bot/team/operation scope，构造由可信verifier/测试fixture负责；actor审计从service派生。`sync_team(command) -> ServiceResult<TeamSyncReceipt>`，回执字段 operation_id、bot_id、team_id、operation、granted_count/revoked_count；同 key 重放返回原回执不重算。所有 service scope 先经 application 验证，store 再验证 env/Bot/team/operation 的受信 scope，不收裸客户端 actor。

- [ ] **Step 1 — 写 RED。**

```rust
let empty = TeamManagerSync {
    service: verified_platform_service,
    bot_id: "bot-a".into(), team_id: "team-old".into(),
    operation: TeamManagerOperation::Sync,
    manager_user_ids: Vec::new(), idempotency_key: "empty-1".into(),
};
let first = repo.sync_team(empty.clone()).await.unwrap();
let audit_after_first = h.audit_count().await.unwrap();
let again = repo.sync_team(empty).await.unwrap();
assert_eq!(first, again);
assert_eq!(h.audit_count().await.unwrap(), audit_after_first);
assert_eq!(repo.role("direct-only", "bot-a").await.unwrap(), Some(BotAccessRelation::Manager));
```

`audit_after_first` 在第一次提交后读取；补完全无差异空同步依然有回执、同 key 改 operation/payload409、move后旧请求重放不恢复旧 team、team交集/其他team并集、普通成员不在名单无权、owner team边可存在、不传版本依赖有序投递。最大快照与部分写失败全回滚。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-edge-permission-store --test team_manager_sync`。
- [ ] **Step 3 — 完整验证后原子替换单来源。** 参数标准化为去重、区分大小写排序的用户集合；规范 payload 包括 operation/目标/source/集合。锁 Bot 后重读幂等回执及当前 sources；校验所有 Human 存在/live/同 env 后，批量生成新增与撤销差集。空数组合法，但缺字段、null、非法成员均拒绝，不能解析失败默认空数组。

```rust
let added: Vec<_> = desired.difference(&current).cloned().collect();
let removed: Vec<_> = current.difference(&desired).cloned().collect();
```

`desired/current` 为已验证同 Bot/team 的 `BTreeSet<String>`，不是全局 manager 集合。move 锁内撤旧来源、替换新来源快照、保存绑定和回执；已有目标 team 按新完整快照替换，不把它当“其他 team”保留旧名单。service actor 审计、operation_id、回执一次提交，不能只用内存幂等。内部分项修复命令走同 source 变更 primitives 和独立幂等回执，不接入 direct API。初版工程建议完整manager快照上限为1,000个去重Human，分块SQL最多100名但仍处于同一Bot事务；完整快照超过1,000在事务前400，不用多个独立事务拼原子快照。该新输入上限需在Gate0接口评审时回写spec，未经确认不得悄悄上线限制。

- [ ] **Step 4 — GREEN。** Memory/SQLite/MySQL 同合同、两个实例同 key/move并发；注入回执/审计提交失败，断言无部分来源变化和无 N+1。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): synchronize team manager sources atomically`。

### Task 8：ownership 转交完整状态机与原子接受

**Files:**
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/transfer_create.rs`、`transfer_decide.rs`、`transfer_query.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/src/authority/ownership.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/types/ownership_transfer.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/core/bot_authority.rs`、`port/repo/bot_authority.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission-store/tests/ownership_transfer.rs`

**Interfaces:** `CreateOwnershipTransfer { actor_user_id, bot_id, to_user_id, expected_owner_version, client_request_id }`（ID均String，version u64）；`CreateTransferResult { receipt: OwnershipTransfer, created: bool }`；`create_transfer(command) -> ServiceResult<CreateTransferResult>`，首次created=true、持久幂等重放false，HTTP据此映射201/200；`decide_transfer(actor_user_id: &str, transfer_id: &str, action: TransferAction) -> ServiceResult<CommittedTransferOutcome>`；query/list 仅双方、有效状态过滤后分页，无写副作用。`OwnershipTransfer` 字段逐项采用 spec §5.2，不另定义 current_owner。

- [ ] **Step 1 — 写 RED，先最小转交再并发分支。** 测试先由fixture建立A拥有bot-a、B为合法Human，再调用create_transfer，取`result.receipt`作为下列request；同次提交的`result.created`必须为true，重放为false。

```rust
let accepted = repo.decide_transfer("b", &request.transfer_id, TransferAction::Accept).await.unwrap();
assert!(matches!(accepted, CommittedTransferOutcome::Receipt(ref r) if r.status == TransferStatus::Accepted));
assert_eq!(repo.ownership("bot-a").await.unwrap().owner_user_id, "b");
assert_eq!(repo.role("a", "bot-a").await.unwrap(), Some(BotAccessRelation::Manager));
assert_eq!(repo.role("b", "bot-a").await.unwrap(), Some(BotAccessRelation::Owner));
assert_eq!(repo.decide_transfer("b", &request.transfer_id, TransferAction::Accept).await.unwrap(), accepted);
```

随后分别写：同Bot两个pending、同key不同body、A→B→A旧版本失效且释放槽位、过期边界DB时间、accept/cancel/reject竞争、响应丢失重试（含 owner_changed 失效提交后同接收人重试 accept，Repo 按持久行 terminal_reason 仍返回 OwnerChanged，不退化为通用 Invalidated）、新建仅版本过时拒绝且不落单、删除后的历史回执、查询count/page同快照；OT02 必须包含 B 的 team边保留与后续 B→C→撤非team 的全链。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-edge-permission-store --test ownership_transfer`，一次聚焦一个新 case，避免把几十个未实现测试一起视作一个开发步骤。
- [ ] **Step 3 — 实现各原子分支。** 锁顺序 Bot→transfer→按ID排序的相关Human；在锁内取DB时间。正常接受撤旧owner、恢复新owner、新增原owner transfer来源、只撤新owner非team来源、CAS version、保存accepted结果。失配分支单独提交：

```sql
UPDATE bot_ownership_transfers
SET status = 'invalidated', terminal_reason = 'owner_changed',
    decision_actor_kind = 'system', decided_by = 'ownership-validation',
    decided_at = CURRENT_TIMESTAMP
WHERE env = ? AND transfer_id = ? AND status = 'pending';
```

该语句只在事务内证实完整 authority 与快照不匹配后条件执行；返回 `Ok(CommittedTransferOutcome::OwnerChanged)`，application 再409。真正 DB failure 返回 Err 并回滚。新建也清理过期/失配pending，原owner幂等键可读历史最小回执但不能建新单；GET不物化expired。成功提交后更新派生投影，通知不在事务内。

- [ ] **Step 4 — GREEN。** 全部 OT01—OT18/OT20—OT21 的repo子用例、live MySQL 唯一键并发；查询/写步骤预算与历史扫描断言。不能仅用内存锁测试证明多实例。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): transfer bot ownership with recipient confirmation`。

### Task 9：mine 并集投影、必填标签和 Bot 控制面切换

**Files:**
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/bot.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/{types,core,port/repo}/bot_control_plane.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/core/bot_control_plane_core.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/src/controllable_bots.rs`、`action_audit.rs`
- Modify: `apps/bcs/crates/services/bcs-bot-store/src/lib.rs`、`memory.rs`
- Create: `apps/bcs/crates/services/bcs-bot-store/tests/bot_action_audit.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-bot/src/mine.rs`
- Modify: `apps/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/bot.rs`
- Modify: `apps/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/bots.yaml`、`apps/bcs/api-contracts/v1/domain-models.yaml`

**Interfaces:** `MyBot { bot: Bot, access_relation: BotAccessRelation }` 为 application 投影；HTTP 用 flatten 保持 Bot 字段平铺。`list_mine(ListMyBots) -> Result<Page<MyBot>, ApplicationError>`；新增 controllable 查询，与原 list_by_creator 并存：

```rust
// shared types: no repo -> core dependency
pub struct BotControllableQuery {
    pub user_id: String,
    pub env: String,
    pub kind: Option<ActorKind>,
    pub name: Option<String>,
    pub status: Option<ActorStatus>,
}
pub struct ControllableBotRecord {
    pub record: BotControlPlaneRecord,
    pub access_relation: BotAccessRelation,
}
// core-owned hydrated view
pub struct ControllableBotView {
    pub bot: BotControlPlaneView,
    pub access_relation: BotAccessRelation,
}
```

Repo的 `list_controllable(BotControllableQuery) -> ServiceResult<Vec<ControllableBotRecord>>`；Core同名方法返回 `ServiceResult<Vec<ControllableBotView>>`，Provider hydration保留同次读取的角色标签，不逐Bot补查询。本人Human self row为显式兼容投影，不能把其他Human角色边纳入。

- [ ] **Step 1 — 写 RED。** 真实 façade 以 User A 查询：自己创建但已失权Bot不返回；别人的创建者但已转给A标owner；direct/team/transfer来源全部并集去重。测试直接断言 wire：

```rust
assert_eq!(body["data"]["items"][0]["access_relation"], "owner");
assert_eq!(body["data"]["items"][1]["access_relation"], "manager");
assert!(body["data"]["items"].as_array().unwrap().iter()
    .all(|item| matches!(item["access_relation"].as_str(), Some("owner" | "manager"))));
```

Bot PATCH额外用Task1上下文调用真实store，查询bcs_bot_action_audits校验Human/Bot-only身份；注入审计INSERT失败断言patch回滚。fixture 以 created_at 固定排序，覆盖本人Human row、kind/name/status/reachability、owned/managed交错多页、空页total、相同Bot多个来源；禁用旧 list_by_creator 的 recording方法以证明不是先查旧列表再拼接。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-bot --test v1_bot_service`；新增 mine标签断言失败。
- [ ] **Step 3 — 批量 authority 与 hydration。**

```sql
SELECT b.* FROM bcs_bots b
WHERE b.env = ? AND b.is_deleted = 0
  AND EXISTS (
    SELECT 1 FROM edge_grants e
    WHERE e.env = b.env AND e.to_id = b.bot_uuid AND e.from_id = ?
      AND e.status = 'approved' AND e.grant_kind IN ('owner', 'manager')
  );
```

这是候选查询骨架，具体列/ActorKind/版本与形状校验由 Task3严格读合同和实际 schema补全；损坏 authority 不返回伪成功。physical并集加本人Human→统一filter/reachability→total→排序分页，Provider批量hydrate不N+1。不修改 get/query通用schema。Bot PATCH/candidate perspective 同时从 created_by切换Hook，通用目录可见性和Bot-only self不改变。PATCH/status写命令同时接受必需BotOperationContext，store将实际UPDATE和Applied审计一次提交；使用Task1纯记录类型，不从传输request body推断operator。

- [ ] **Step 4 — GREEN。** app-bot、bot-store control-plane conformance、HTTP bot_routes、OpenAPI required/enum一致性；断言旧 creator字面查询仍只返回创建来源。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): list owned and managed bots with explicit access relation`。

### Task 10：Group 视角、Human sponsorship 与下游群管理授权

**Files:**
- Modify: `apps/bcs/crates/application/v1/bcs-app-group/src/authorization.rs`、`create.rs`、`service.rs`、`projections.rs`
- Modify: `apps/bcs/crates/services/bcs-group/src/application/management/create.rs`、`guards.rs`、`operations.rs`、`workbench.rs`、`queries.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-group/tests/owner_manager_parity.rs`
- Create: `apps/bcs/crates/services/bcs-group/tests/human_sponsorship.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/group_management.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/group.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/port/repo/group.rs`
- Modify: `apps/bcs/crates/services/bcs-group-store/src/store_eventful.rs`、`memory.rs`
- Create: `apps/bcs/crates/services/bcs-group-store/src/action_audit.rs`、`apps/bcs/crates/services/bcs-group-store/tests/group_action_audit.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/groups.yaml`

**Interfaces:** Consumes BotAuthorityHook；Produces可信 `HumanSponsorship { user_id: String }` 由 application校验真实Human后传入GroupManagement command，Bot originator分支不接受该凭据；下游核实当前资格而非任意字符串。

- [ ] **Step 1 — 写 RED。** 构造 Human A owns X/manages Y，两者protected无好友，Group private。以真实创建用例返回的结果断言：

```rust
assert!(human_private_group_result.is_ok());
assert!(human_public_group_result.is_err());
assert!(bot_originated_same_input_result.is_err());
assert_eq!(friend_edges_after, friend_edges_before);
assert!(worker_manager_update_group_result.is_err());
```

再测Human不参与时默认Human消息/列表仍拒绝、显式X/Y视角允许；add-member必须先通过群管理权限，再做目标sponsorship；hidden拒绝。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-group --test owner_manager_parity` 和 `-p bcs-group --test human_sponsorship`。
- [ ] **Step 3 — 替换Human→Bot谓词，不替换群角色。**

```text
human sponsor = not hidden AND (public OR authority.can_manage(human, bot))
bot sponsor   = not hidden AND existing public/friendship/reachability
add member    = existing group-management authorization AND sponsor(target)
```

create的driver和所有participants都检查；management/create/operations/guards接到同一Human上下文后不能再次仅按driver friendship误拒。初始Session仍由management/create创建，保留该链的成员/角色规则；不为Human隐式创建成员。Group/成员/workspace写入贯穿BotOperationContext，group store把业务状态/既有Event及Applied审计放同事务；初始Session的子命令传同操作上下文与不同稳定step_key，由Task11补齐Session写入边界，不声称两段现有用例自动成为全局事务。

- [ ] **Step 4 — GREEN。** 两层facade和management都运行测试，记录Hook确实调用；get/list/session_only/detail/workspace/driver角色对照测试及HTTP group_routes。group_action_audit覆盖真实operator/effective actor、审计失败状态/Event同回滚，修改前后不得残留部分成功审计。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): allow human-sponsored collaboration for managed bots`。

### Task 11：Session、文件、launch 与业务审计原子性

**Files:**
- Modify: `apps/bcs/crates/application/v1/bcs-app-session/src/lib.rs`、`file.rs`、`connection.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/src/authorization.rs`、`queries.rs`、`mutations.rs`、`tests.rs`
- Modify: `apps/bcs/crates/services/bcs-session/src/launch.rs`、`application.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/session.rs`、`session_launch.rs`、`session_files.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/core/session.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/session.rs`、`session_file.rs`
- Modify: `apps/bcs/crates/services/bcs-session-store/src/mysql.rs`、`memory.rs`
- Modify: `apps/bcs/crates/services/bcs-session-file/src/service.rs`、`noop.rs`
- Modify: `apps/bcs/crates/services/bcs-session-file-store/src/mysql.rs`、`memory.rs`
- Create: `apps/bcs/crates/services/bcs-session-store/src/action_audit.rs`、`apps/bcs/crates/services/bcs-session-file-store/src/action_audit.rs`
- Create: `apps/bcs/crates/services/bcs-session-store/tests/session_action_audit.rs`、`apps/bcs/crates/services/bcs-session-file-store/tests/file_action_audit.rs`
- Create: `apps/bcs/crates/services/bcs-session-file/tests/file_action_audit_lifecycle.rs`
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`、`internal/routes/session_file.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/tests/owner_manager_parity.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/sessions.yaml`、`session-files.yaml`、`connections.yaml`

**Interfaces:** 新 `IdentityPolicy::HumanOrAuthorizedBot` 通过异步 application授权选择effective Principal；旧同步select函数对新策略明确拒绝，不增加同步放行捷径。`resolve_authorized_principal(caller: &AuthenticatedCaller, authority: &dyn BotAuthorityHook) -> Result<Principal, ApplicationError>` 验证精确Bot的实时角色；caller保留在用例内，以Task1 `BotOperationContext`携带真实operator，不能只向下游传返回的Principal。

收藏代表性的必需写合同在application内部服务、Core和Repo逐层传播；读取接口不加无关审计参数：

```rust
async fn collect(&self, session_id: &str, bot_uuid: &str,
    operation: &BotOperationContext) -> ServiceResult<()>;
async fn uncollect(&self, session_id: &str, bot_uuid: &str,
    operation: &BotOperationContext) -> ServiceResult<()>;
```

文件 `NewSessionFileParams`、文件状态/删除命令以及Session创建/参与者/状态变更命令同样显式携带该必需context；不加默认None或从created_by补operator。文件Repo还声明 `record_operation_phase(audit: BotActionAuditRecord) -> ServiceResult<()>`，只供文件服务围绕外部I/O记录admitted/失败/不确定阶段；文件metadata成功变更必须使用包含审计的原子mutation，不能先写业务再调用这个独立方法。Audit表由Task2已经建全；Task11只消费，不能回写冻结迁移。

- [ ] **Step 1 — 分别写平权与真实审计 RED。**

```rust
assert_eq!(owner_message_ids, manager_message_ids);
assert!(managed_bot_not_in_session_result.is_err());
assert!(human_not_member_without_view_result.is_err());
assert!(mixed_caller_with_stale_claim_but_current_manager.is_ok());
assert!(mixed_caller_without_current_role.is_err());
assert!(other_human_file_delete_result.is_err());
```

审计store测试在fixture中创建A管理且X实际参与的Session，然后调用真实collect，以下 `audit` 从driver对 `bcs_bot_action_audits` 的SQL读取取得，不由mock生成：

```rust
let operation = BotOperationContext {
    operation_id: "collect-operation-1".into(),
    actor: BotOperationActor::Human { user_id: "a".into(), effective_actor_id: "bot-x".into() },
};
repo.collect("session-a", "bot-x", &operation).await.unwrap();
// driver查询下方SQL并严格解码为BotActionAuditRecord后，断言其实际结果。
assert_eq!(audit.operator_user_id.as_deref(), Some("a"));
assert_eq!(audit.effective_actor_id, "bot-x");
assert_eq!(audit.phase, BotActionAuditPhase::Applied);
```

测试driver读取SQL（env由fixture注入，operation_id为上例，step_key为本用例生成的稳定Applied步骤键）：

```sql
SELECT *
FROM bcs_bot_action_audits
WHERE env = ? AND operation_id = ? AND step_key = ?;
```

分别追加：同context内部重试不重复步骤、已收藏无变化不造Applied；合法Bot-only的operator_user_id=NULL；audit INSERT或commit失败收藏不变且无成功审计；明确外部调用前admitted失败时storage call count=0；storage成功后metadata/audit失败时保留admitted、无completed、返回错误而非虚假回滚。覆盖share、launch与后台恢复保留原操作上下文，不能把后来执行恢复的系统冒充原Human。

- [ ] **Step 2 — 分别运行 RED。**

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-session --test owner_manager_parity
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-session-store --test session_action_audit
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-session-file-store --test file_action_audit
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-session-file --test file_action_audit_lifecycle
```

预期新合同/审计行为缺失；环境问题不是功能RED。一次完成一个用例的RED→GREEN，再扩展下一个失败分支。

- [ ] **Step 3 — 身份与审计贯穿，分两类提交。**

```text
verified caller + authorized effective Principal
  -> required BotOperationContext (Human/Bot/System tagged)
  -> resource application/Core command
  -> owning Repo mutation:
       validate context -> DB transaction(business change + Applied audit) -> commit
external effect:
  -> persist Admitted -> perform I/O with no DB lock
  -> transaction(metadata/result + Completed audit), or persist Failed/Unknown
```

Session收藏等使用改变状态的条件UPDATE与同事务审计INSERT，恢复/读取使用相同strict errors；SQLite/MySQL均验证无变化行为，不依赖各方言no-op affected_rows的差异。Memory复制暂存状态与日志，全部通过后在同一临界区发布。文件当前实现是先storage.delete再repo.delete，本任务必须在I/O前补admitted，并把最终metadata删除与completed同事务；不要仅将FileStatus改为Deleting，因为现有Deleting分支会跳过backend调用直接删metadata。共享/发送等无metadata改变的成功操作仍写completed；admitted不是外部完成证明，未知结果不按audit key自动重放。重试/恢复复用持久操作上下文或遵循既有幂等合同，不新增客户端必须传operation_id的HTTP字段。

DTO的signed owner_id同步比较迁入application动态authority；不改变file owner、Human独立参与、Bot共享收藏和消息投影。stage/step action只用受控枚举；业务store自行映射共享记录到审计SQL，不依赖edge-permission-store concrete。按此次职责拆分所有已超限Session/file/service/store文件，相关Noop与recording doubles在本任务同步新必需参数。

- [ ] **Step 4 — GREEN。** 重跑四套聚焦测试，再运行app-session/bcs-session、HTTP session/file/connection及Repo conformance；真实audit行、Hook调用、事务失败、外部未知结果都要断言。每个修改source≤1000行；增加的审计SQL/锁时间计入spec §17.1成本，不以记录日志代替持久化。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): apply session authority with durable acting identity audit`。

### Task 12：好友/邀请、legacy 和 runtime 权限隔离

**Files:**
- Modify: `apps/bcs/crates/application/v1/bcs-app-invitation/src/lib.rs`
- Create: 同crate `src/authorization.rs`、`src/queries.rs`、`src/mutations.rs`、`src/tests.rs`
- Modify: `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs`
- Modify: `apps/bcs/crates/services/bcs-edge-permission-store/src/lib.rs`（已拆分后改对应grant读模块）
- Modify: `apps/bcs/crates/adapters/http/bcs-http/src/routes/bots.rs`、`friends.rs`、`friend_connections.rs`、`sessions.rs`、`session_files.rs`
- Modify: `apps/bcs/crates/services/bcs-bot/src/application/actor_directory.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/tests/role_runtime_isolation.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/application/connect.rs`、`invite.rs`、`message_flow.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/permission_request.rs`、`message_delivery.rs`、`chat_run.rs`
- Modify: `apps/bcs/crates/services/bcs-edge-permission-store/src/authority/audit.rs`（Task4已创建，普通业务与manager表严格分开）
- Modify: `apps/bcs/crates/services/bcs-message-flow/src/lib.rs`
- Modify: `apps/bcs/crates/services/bcs-message-store/src/delivery.rs`、`memory_delivery_work.rs`
- Modify: `apps/bcs/crates/services/bcs-chat-run-store/src/sql.rs`、`memory.rs`
- Modify: `apps/bcs/crates/contracts/bcs-domain/src/message_delivery.rs`
- Create: `apps/bcs/crates/services/bcs-message-store/src/action_audit.rs`、`apps/bcs/crates/services/bcs-chat-run-store/src/action_audit.rs`
- Create: `apps/bcs/crates/services/bcs-edge-permission/tests/acting_operation_audit.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-http/tests/current_authority.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/friendships.yaml`、`invitations.yaml`（friend connection 已包含于现有friendships合同，不新造同义文件）

**Interfaces:** Consumes统一Hook和已存在FriendAuthSyncPort；Produces owner/manager actingactor与current-owner通知接收人。不得将外部好友同步Bot ID后缀或Provider绑定owner改写成BCS owner。

- [ ] **Step 1 — 写 RED。**

```rust
assert!(!admission_from_manager_edge_only);
assert!(friend_ids.iter().all(|id| id != "manager-only-human"));
assert!(!authz_grants.iter().any(|g| matches!(g.grant_kind, GrantKind::Owner | GrantKind::Manager)));
assert_eq!(external_friend_sync_calls_after_role_change, 0);
assert!(former_creator_without_role_legacy_patch.is_err());
```

补 friend和manager并存，删一不影响另一；旧creator/后缀/claim不能onboard恢复；好友审批找当前owner；legacy `/bots/my` 返回相同标签但保留其既有筛选/排序。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-edge-permission --test role_runtime_isolation` 和 `-p bcs-http --test current_authority`。
- [ ] **Step 3 — runtime查询白名单、新旧入口一致。**

```sql
AND grant_kind IN ('permission_profile', 'rules')
```

添加到runtime admission的有效出边过滤与AuthzContext构建查询；friend仍按目标default profile精确判断，不能所有permission_profile都当好友。Connect approve/reject/cancel永远不处理manager审计ID。legacy handler移除业务授权比较、调用应用服务，不能为兼容保留 `created_by OR role`。好友/邀请与消息send/abort、workspace等授权相关写入按spec §12.5传BotOperationContext：既有permission request/dispatch/run记录与业务审计共享其拥有store事务；持久delivery/run新增必需操作上下文引用，同事务写admitted并保存operation_id；恢复读当前命令的身份快照，不用最新资源审计/ID后缀推断；区分迁移前历史行与新命令缺失context，后者必须拒绝、不能降级system。进入外部投递前必须有admitted，终态按原I/O不确定性合同记录，不把业务audits写进manager表。acting_operation_audit测试按真实持久记录覆盖operator/effective actor及失败分支；Task9/10/11的helper不能替本任务吞掉操作者。

- [ ] **Step 4 — GREEN。** Invitation、friend conformance、runtime admission、legacy suite；逐项审核 grep结果，历史来源用途允许保留并记录理由。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `fix(bcs): isolate bot roles from runtime and legacy creator grants`。

### Task 13：manager/team application 与 HTTP 凭证边界

**Files:**
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/bot_manager.rs`、`team_manager_sync.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-bot/src/management.rs`、`team_sync.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot_managers.rs`、`openapi/dto/bot_management.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/internal/routes/team_manager_sources.rs`
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/{mod.rs,common/state.rs,internal/mod.rs,internal/routes/mod.rs,openapi/routes/mod.rs,openapi/dto/mod.rs}`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/port/team_manager_credential.rs`
- Create: `apps/bcs/crates/services/bcs-jwt/src/team_manager_credential.rs`
- Create: `apps/bcs/crates/service-api/bcs-config-api/src/team_manager_sync.rs`
- Modify: `apps/bcs/crates/service-api/bcs-config-api/src/lib.rs`、`apps/bcs/crates/bootstrap/bcs/src/config.rs`、`config_loader.rs`、`auth_wiring.rs`
- Modify: `apps/bcs/crates/services/bcs-jwt/src/lib.rs`、`apps/bcs/crates/services/bcs-jwt/CONTEXT.md`
- Create: `apps/bcs/crates/bootstrap/bcs/src/config/team_manager_sync.rs`、`apps/bcs/crates/services/bcs-jwt/tests/team_manager_credential.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/tests/bot_manager_routes.rs`、`team_manager_routes.rs`
- Create: `apps/bcs/api-contracts/v1/internal/team-manager-sources.yaml`
- Modify: `apps/bcs/api-contracts/v1/{internal.yaml,openapi.yaml,domain-models.yaml,openapi/bots.yaml}`、`apps/bcs/scripts/validate_openapi_contract.py`、`apps/bcs/tests/openapi/test_internal_contract.py`

**Interfaces:** `BotManagerService` 提供 list/grant/revoke 用例（新名不与现有legacy `application::BotManagementService`注册/生命周期合同冲突）；`TeamManagerSyncService::sync(command) -> Result<TeamSyncReceipt, ApplicationError>`；`TeamManagerCredentialVerifier::verify(credential: &str) -> ServiceResult<VerifiedTeamManagerService>`，验证结果包含service_id/env/允许scope，凭证不进入审计、业务日志或command持久化。Verifier是application调用的port，具体签名/密钥实现复用现有bcs-jwt纯签名校验边界、由bootstrap注入，依Gate0批准的认证形态，不复用register purpose；不恢复已不存在的bcs-http-auth crate。`VerifiedTeamManagerService`包括service_id、env及已验证的Bot/team/operation约束；业务command带该验证结果，不凭AuditActor::Service的字符串决定授权。

- [ ] **Step 1 — 写 HTTP RED。**

```rust
assert_eq!(without_credential.status(), StatusCode::UNAUTHORIZED);
assert_eq!(invalid_scope.status(), StatusCode::FORBIDDEN);
assert_eq!(trusted_service_without_human.status(), StatusCode::OK);
assert_eq!(normal_internal_without_principal.status(), StatusCode::UNAUTHORIZED);
assert_eq!(delete_body["data"]["remaining_team_sources"], serde_json::json!(["team-a"]));
assert_eq!(unknown_team_field.status(), StatusCode::BAD_REQUEST);
```

补缺manager_user_ids/null/错类型400与合法[]200、未知operation/move目标、direct PUT含team400、owner target409；录制service身份和scope确实传入application而非body伪造。POST修复单人、DELETE修复单人使用同服务凭证/幂等，不让匿名请求改变权限。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-api-http --test bot_manager_routes --test team_manager_routes`。
- [ ] **Step 3 — 精确挂载与schema。**

```yaml
manager_user_ids:
  type: array
  items: {type: string, minLength: 1}
operation:
  type: string
  enum: [sync, move]
idempotency_key:
  type: string
  minLength: 1
```

body required包含三字段，additionalProperties=false；move要求new_team_id，sync不接受new_team_id；数组允许空，重复ID按集合规范化。服务安全声明与Human Principal分开；public_router外独立team slice只豁免通用登录中间件，不免凭证校验。validator新增精确team路径模板/方法允许集合和负例，不允许任意`/api/v1/*`。POST修复body建议为`user_id/idempotency_key`，DELETE保留`?user_id=`并通过`Idempotency-Key` header幂等；这两项是原spec运维接口的工程形状建议，在Gate0确认并回写spec后才能挂载，不把本计划当作对外已发布合同。

- [ ] **Step 4 — GREEN。** 两套route tests、app身份测试、config未知键/缺密钥/purpose隔离/无日志泄密测试、公开/internal validator及负例。未配置team凭证时不挂载team写路由；声明启用却缺失/非法密钥时启动报配置错误，不匿名放行。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): expose scoped manager and team synchronization APIs`。

### Task 14：ownership application/HTTP 与精确错误回执

**Files:**
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/ownership_transfer.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-bot/src/ownership.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot_ownership.rs`、`openapi/dto/bot_ownership.rs`
- Modify: `apps/bcs/crates/adapters/http/bcs-api-http/src/v1/common/error.rs`、`common/state.rs`、`openapi/routes/mod.rs`、`openapi/dto/mod.rs`
- Create: `apps/bcs/crates/adapters/http/bcs-api-http/tests/bot_ownership_routes.rs`
- Modify: `apps/bcs/api-contracts/v1/openapi/bots.yaml`、`apps/bcs/api-contracts/v1/domain-models.yaml`、`apps/bcs/api-contracts/v1/openapi.yaml`

**Interfaces:** `OwnershipTransferService` 提供 ownership/get/list/create/accept/reject/cancel；HumanOnly、资源concealment与双方读资格在application，Core负责领域状态/Repo原子性。

- [ ] **Step 1 — 写 RED。**

```rust
assert_eq!(created.status(), StatusCode::CREATED);
assert_eq!(same_request_replay.status(), StatusCode::OK);
assert_eq!(manager_initiates.status(), StatusCode::FORBIDDEN);
assert_eq!(unrelated_reads.status(), StatusCode::NOT_FOUND);
assert_eq!(mismatch_accept.status(), StatusCode::CONFLICT);
assert_eq!(persisted_transfer_status, "invalidated");
assert_eq!(mismatch_error_code, "ownership_changed");
```

补未初始化manager/ownership共享409、损坏500、Bot/App/AccessKey-only拒绝、pending不泄露Bot私有字段、无body动作未知字段拒绝；历史accepted后再转交重放不读当前owner改变回执。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-api-http --test bot_ownership_routes`。
- [ ] **Step 3 — 明确 committed outcome 的映射。**

```text
Receipt(accepted/rejected/cancelled) -> HTTP 200 + historical receipt
OwnerChanged                       -> HTTP 409 / ownership_changed
Expired                            -> HTTP 409 / ownership_transfer_expired
Invalidated                        -> 按持久行 terminal_reason 复核后映射（见下）
storage/decode/commit error         -> HTTP 500, no SQL leakage
```

重试已终态请求时按持久 transfer 行的 `terminal_reason` 复核映射，不能只靠状态枚举推断：`invalidated` 且 `terminal_reason=owner_changed` 返回 409/ownership_changed（OT12：响应丢失后同接收人重试与失效当次同码，不得退化为 not_pending）；其他失效原因返回 409/ownership_transfer_not_pending。application 映射层必须能拿到持久行的 terminal_reason（query/repo 结果携带该字段），状态枚举本身不足以区分。

不得对OwnerChanged再启动回滚。GET/list对expires_at投影expired，不写库；count/page共享DB时间和快照。响应不命名current_owner，接受界面文案需要的scope说明写入OpenAPI description。新建仅 `expected_owner_version` 过时（双方合法、无有效 pending）同样返回 409/ownership_changed 且不落单，与其他 400 参数错误区分，RED 需断言该路径不落库。

- [ ] **Step 4 — GREEN。** ownership routes、application recording、公开schema validator、错误envelope两种adapter一致性；update shared错误map不破坏原resource concealment。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): expose confirmation-based ownership transfer APIs`。

### Task 15：出站持续授权应用合同与有界批量证据

**Files:**
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/delivery_authorization.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/src/delivery_authorization.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/repo/bot_authority.rs`
- Create: `apps/bcs/crates/application/v1/bcs-app-session/tests/delivery_authorization.rs`
- Create: `apps/bcs/crates/test-support/bcs-test-support/src/contract/application/delivery_authorization.rs`

**Interfaces:** 新 `DeliveryAuthorizationService` 声明 `authorize_batch(contexts: Vec<DeliveryAuthorizationContext>) -> Result<Vec<DeliveryAuthorizationDecision>, ApplicationError>`；最多128项，结果逐项匹配输入，不能按user合并bool。context字段为可信tenant（沿用身份合同可空，不推断tenant/env）、env、user_id、resource_kind/resource_id、view_actor_id、action、visibility_domain、audience；kind/action是关闭枚举，不任意字符串。decision为 `Deliver/SkipMessage/InvalidateBinding`，DB/解码错误是Err。先判断当前绑定资格，失权直接InvalidateBinding；资格仍有效但当前消息不在audience/view scope内才SkipMessage，不能借过滤跳过撤权检查。只返回当次判定，不返回可长期缓存的token。

- [ ] **Step 1 — 写 RED。** 构造同User同Session的X/Y视角，仅撤X；分别调用新context builder取得`x_context/y_context`：

```rust
let decisions = service.authorize_batch(vec![x_context, y_context]).await.unwrap();
assert_eq!(decisions, vec![DeliveryAuthorizationDecision::InvalidateBinding, DeliveryAuthorizationDecision::Deliver]);
assert_eq!(recording_authority_batch_calls, 1);
```

追加合法participant的不可见FullOnly/他人Directed与下一条Public，分别建立fixture上下文 `full_only_context/other_directed_context/public_context`（身份/资源成员有效，视角为participant）：

```rust
let decisions = service.authorize_batch(vec![full_only_context, other_directed_context, public_context]).await.unwrap();
assert_eq!(decisions, vec![
    DeliveryAuthorizationDecision::SkipMessage,
    DeliveryAuthorizationDecision::SkipMessage,
    DeliveryAuthorizationDecision::Deliver,
]);
```

同一participant真实失权后连不可见帧也应InvalidateBinding；另测本人独立参与仍允许、显式撤权Bot不自动切视角、env/resource/scope不同不得合并、128合法/129需adapter拆批、读取失败为Err而非SkipMessage/空成功、Group/Session成员检查真实调用次数。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-app-session --test delivery_authorization`。
- [ ] **Step 3 — 批量只优化证据读取。**

```text
validate trusted contexts and size
fetch exact authority pairs once per bounded batch
for each context:
  identity/role/membership binding invalid -> InvalidateBinding
  valid binding but this message outside audience/view scope -> SkipMessage
  valid binding and visible message -> Deliver
return position-aligned decisions; propagate any undecodable authority failure as Err
```

批量pair读取复用Task3，但相同User不同View最终判定独立；资源内exists避免按用户所有Bot扫描。写清新增authority SQL与原资源查询预算，不把一次Hook当一次总SQL。

- [ ] **Step 4 — GREEN。** service合同及Memory/SQLite查询计数测试；129等大事件由下任务拆批，不把剩余目标当已授权。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): authorize protected delivery by exact scoped context`。

### Task 16：WS 队列、run fallback/replay 和实际发送前复核

**Files:**
- Create: `apps/bcs/crates/adapters/ws/bcs-ws/src/web/protected_delivery.rs`
- Modify: `apps/bcs/crates/adapters/ws/bcs-ws/src/web/connection_registry.rs`、`frontend_delivery.rs`、`handler.rs`、`dispatcher.rs`
- Modify: `apps/bcs/crates/adapters/ws/bcs-ws/src/shared/run_channels.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/delivery.rs`
- Create: `apps/bcs/crates/adapters/ws/bcs-ws/tests/protected_delivery.rs`
- Read: `apps/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs`（区分Provider入站SSE与向Human出站，不给Provider事件错误套Human权限）

**Interfaces:** 内部队列由String改为 `WorkbenchOutbound::{PublicControl(String), Protected { payload: String, context: DeliveryAuthorizationContext, binding_id: u64 }}`；这是adapter内部类型，不修改客户端frame schema。真正User/选定视角/绑定代次随队列项保存，不能把registry旧user_id字段当真实操作者（旧注释表示它可能是Actor）。

- [ ] **Step 1 — 写 RED。** 测试夹具阻塞socket writer，入队合法事件，另一实例撤X权限，再放开writer：

```rust
assert_eq!(x_socket_protected_frames_after_revoke, 0);
assert_eq!(y_socket_protected_frames_after_revoke, 1);
assert!(x_binding_invalidated);
assert!(!y_binding_invalidated);
assert_eq!(fallback_frames_for_denied_x, 0);
assert_eq!(authority_reads_after_binding_closed, 0);
assert_eq!(valid_participant_hidden_frames, 0);
assert_eq!(valid_participant_next_public_frames, 1);
assert!(!valid_participant_binding_invalidated);
assert_eq!(fallback_frames_for_skipped_message, 0);
```

数值来自真实registry/run channel和recording应用Hook；fixture分别验证1/128/129 contexts、多个同User不同view、FullOnly/他人Directed过滤后继续收Public/本人Directed、frame连续、Interaction replay、run fallback、scope改变替换connection、持续DB失败。不要用sleep猜并发点，用Barrier/Notify固定“入队—撤权—出队”。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-ws --test protected_delivery`。
- [ ] **Step 3 — 两个明确授权位置。** registry取有界目标快照后释放全局锁→application批量检查→复核conn_id/绑定代次/关闭状态→仅Deliver入队，SkipMessage只略过该帧不删除订阅，InvalidateBinding或Err按绑定失效处理；writer出队实际发送前重新检查：

```text
Protected frame dequeued
  -> binding still matches and not cancelled
  -> authorize_batch([frame.context]) against current committed authority
  -> Deliver: start socket send without DB lock
  -> SkipMessage: drop only this frame, keep binding and continue next frame
  -> InvalidateBinding/error: drop frame, invalidate this binding, stop its protected drain
```

不使用入队决策作writer通行证；无网络等待的DB事务。若未来合批出队检查必须同事件/有界，不引入额外延迟窗口/TTL。关闭时丢弃受保护积压，不先flush敏感帧；保留leader change现有关闭语义。run fallback绑定必须携带相同真实身份上下文；InvalidateBinding不另走session fallback，SkipMessage也不是投递失败或无连接，不为同一目标回退重发，后续新事件照常独立鉴权；Provider SSE流只在转发到Human出口执行该控制（spec评审补遗已明确当前无Human出向SSE adapter，该条款为条件性约束，不是本任务的实现对象）。若发现另有Human SSE adapter，将入口添加到任务传播表并用相同合同覆盖，不假设所有SSE都是Human出站。

- [ ] **Step 4 — GREEN。** WS完整测试、跨实例持久撤权fixture、查询计数和慢writer测试；按spec `Σceil(K/128)+Q_dequeue+Q_redispatch`报告，当前20连接50事件示例不得报告只50次。测连接池等待和故障后查询停止。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `fix(bcs): reauthorize queued and replayed protected delivery`。

### Task 17：旧数据回填、治理与一次性 authority cutover

**Files:**
- Create: `apps/bcs/crates/services/bcs-bot-store/src/ownership_backfill.rs`
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/application/ownership_migration.rs`、`apps/bcs/crates/service-api/bcs-service-api/src/core/ownership_migration.rs`
- Create: `apps/bcs/crates/services/bcs-bot/src/core/ownership_migration.rs`
- Create: `apps/bcs/crates/services/bcs-bot/src/application/ownership_migration.rs`
- Create: `apps/bcs/crates/bootstrap/bcs/src/bin/bcs-ownership-migrate.rs`、`apps/bcs/crates/bootstrap/bcs/src/ownership_migration_wiring.rs`
- Modify: `apps/bcs/crates/bootstrap/bcs/Cargo.toml`、`apps/bcs/crates/bootstrap/bcs/src/lib.rs`（显式注册同crate治理binary及composition入口，不新增crate）
- Create: `apps/bcs/crates/bootstrap/bcs/tests/ownership_migration.rs`
- Create: `apps/bcs/docs/runbooks/bot-authority-cutover.md`

**Interfaces:** migration application/Core提供 `inspect_batch(after_bot_id: Option<String>, limit: u32) -> ServiceResult<OwnershipCandidatePage>` 和 `initialize_batch(confirmed_candidate_ids: Vec<String>, batch_id: String) -> ServiceResult<OwnershipMigrationReport>`。`OwnershipCandidatePage`包含候选项与next_bot_id；候选项含bot_id/env/candidate_user_id（缺失来源合法时为空）/reason；`OwnershipMigrationReport`包含initialized/skipped/conflicted/failed的Bot ID与机器原因，任一数据库失败使命令非零退出，已提交前序Bot通过batch_id恢复。共享类型在types/bot_authority.rs定义；受治理运维binary经bootstrap装配调用application，无新公开claim HTTP接口；不让当前纯HTTP客户端bcs-cli直接持数据库或服务实现。candidate保存Bot/env/创建来源/冲突原因/批次，读取/执行都重新核实version与owner，不保存可写的平行owner名单。

- [ ] **Step 1 — 写 RED。**

```rust
assert_eq!(migrated_version, 1);
assert_eq!(original_created_by, created_by_after_migration);
assert_eq!(transferred_owner_before_rerun, owner_after_rerun);
assert_eq!(transferred_version_before_rerun, version_after_rerun);
assert!(conflicted_bot_is_in_governance_report);
assert_eq!(human_rows_migrated, 0);
assert_eq!(deleted_bots_migrated, 0);
```

覆盖缺creator、多creator、Human缺失、部分批次失败/重启恢复、已初始化损坏，不能迁移时不靠creator继续放行；裸runtime无Human保留version0和Agent自发现。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test ownership_migration`。
- [ ] **Step 3 — 有界迁移与发布runbook。** candidate按env/live physical/version0 keyset分页，每批最多100（工程任务默认，超限拒绝）；每Bot通过migration Core调用Task5原子初始化并记批次审计，application不直接调用Repo。重跑已初始化仅核对/跳过。完整发布顺序写入runbook：

```text
备份 -> 阻断不兼容旧role/runtime写实例 -> 新schema
-> dry-run候选/冲突治理 -> 有界回填 -> 完整性核对
-> 同一兼容版本切换全部Human授权/注册/WS入口 -> 联机验收
```

已经转交后禁止回滚到created_by授权版本；先冻结权限/转交写入，恢复支持当前事实的兼容版本，不删角色边或用旧created_by反推owner。前向修复与快照恢复影响写清楚，不能写“回滚migration就行”。

- [ ] **Step 4 — GREEN。** 完整旧库升级、重跑、重启和失败恢复；核对所有live Human-owned Bot有且只有一owner，检查v0治理清单。治理binary只通过已有配置加载选择datasource，要求maintenance执行和显式确认候选；测试dry-run无写、副作用只能apply、错误退出码非零。bcs-cli本身没有新leaf，不能声称它新增了迁移命令。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(bcs): migrate historical ownership with a guarded cutover`。

### Task 18：bootstrap、Noop/test doubles 和架构闭合

**Files:**
- Modify: `apps/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `apps/bcs/crates/service-api/bcs-services-container/src/lib.rs`、`services.rs`、`test_support.rs`
- Modify: `apps/bcs/crates/test-support/bcs-test-support/src/edge_permission_noop.rs`、`noop.rs`
- Modify: `apps/bcs/crates/services/bcs-session/src/noop.rs`
- Create: `apps/bcs/crates/bootstrap/bcs/tests/bot_authority_wiring.rs`
- Modify/Create: 所有触及crate的`CONTEXT.md`及conformance登记
- Modify: `apps/bcs/api-contracts/v1/gateway-principal/contract.md`、`apps/bcs/CHANGELOG.md`

**Interfaces:** bootstrap选择同datasource的生命周期/authority Repo、authority Core、Hook、management/transfer/team façade、出站authorization service；service-api公开状态仅application，无DbPlugin或具体store。

- [ ] **Step 1 — 写装配 RED。**

```rust
assert!(unconfigured_authority_result.is_err());
assert!(unconfigured_team_credential_result.is_err());
assert_eq!(v1_owner_relation, legacy_owner_relation);
assert_eq!(v1_manager_relation, legacy_manager_relation);
assert!(connection_router_authorization_hook_was_called);
assert!(registration_owner_initialization_was_called);
```

通过实际server constructor接recording implementations，不只new后检查字段。单独Session-token router、legacy、WS/run fallback都需被装配，Noop不得允许请求。

- [ ] **Step 2 — RED。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test bot_authority_wiring`。
- [ ] **Step 3 — 注入所有消费者与配置。**

```text
composition root
  -> authority Repo + lifecycle Repo (same DB / shared Memory state)
  -> authority Core
  -> Human/Service application + registered Hook
  -> v1/legacy HTTP, Session connection service, WS protected writer
```

显式更新所有constructors/recording doubles；配置读取只在config/bootstrap。演进Service/Repo新必需方法，不能为了旧test编译写默认空Vec/allow。对大型server/container/noop先做本任务责任拆分，使每个修改source≤1000。

- [ ] **Step 4 — GREEN与架构门禁。** `cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets`；`(cd apps/bcs && bash scripts/ci/arch-check.sh)`；R25/依赖/配置检查由该入口执行，未运行部分逐项记录。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `refactor(bcs): wire current authority across all supported entrypoints`。

### Task 19：Frontend-nextgen 最小契约消费与 ownership 交互

**Files:**
- Modify: `apps/frontend-nextgen/src/services/backendApi/collaboration/collaborationBotController.ts`
- Modify: `apps/frontend-nextgen/src/services/workspace/identityService.ts`、`groupService.ts`
- Modify: `apps/frontend-nextgen/src/services/collaborationPrivacy/mappers.ts`
- Modify: `apps/frontend-nextgen/src/assets/TaskPanel/GroupDrillDown.tsx`
- Modify: `apps/frontend-nextgen/src/pages/CollaborationPrivacy/index.tsx`、`apps/frontend-nextgen/src/hooks/useCollaborationPrivacy.ts`
- Modify: `apps/frontend-nextgen/src/domain/collaboration/types.ts`（IdentityView消费角色字段）
- Create: `apps/frontend-nextgen/src/services/backendApi/collaboration/botOwnershipController.ts`
- Create: `apps/frontend-nextgen/src/services/workspace/botAuthorityService.ts`
- Create: `apps/frontend-nextgen/src/components/BotOwnershipTransferPanel.tsx`
- Create: `apps/frontend-nextgen/test/botAuthority.test.ts`、`botOwnershipTransfer.test.tsx`

**Interfaces:** mine独立DTO `CollaborationMyBotDto = CollaborationBotDto & { access_relation: 'owner' | 'manager' }`；通用Bot DTO不强加该必填字段。`botAuthorityService`保留角色与sources结果，组件消费当前ownership/version/pending，不自行推断当前owner；UI不直接调用内部team endpoints。

- [ ] **Step 1 — 写 RED。** Jest mock API给created_by和当前角色不一致的结果：

```typescript
expect(ownedIdentity.accessRelation).toBe('owner');
expect(managedIdentity.accessRelation).toBe('manager');
expect(managedIdentity.id).toBe('bot-managed');
expect(deleteResult.remaining_team_sources).toEqual(['team-a']);
expect(screen.queryByRole('button', { name: '转交 ownership' })).not.toBeInTheDocument();
```

最后一项在manager视角渲染；另测owner发起、接收人确认/拒绝、发起人取消、仅一pending、409刷新、提交成功响应丢失用同key重试；DELETE有team来源不可把Bot从身份列表移除。

- [ ] **Step 2 — RED。** `npm --prefix apps/frontend-nextgen test -- --runInBand botAuthority botOwnershipTransfer`。
- [ ] **Step 3 — 服务适配与最小交互。**

```typescript
export type CollaborationMyBotDto = CollaborationBotDto & {
  access_relation: 'owner' | 'manager';
};
```

mine缺字段为合同错误，不从created_by补默认owner。身份切换纳入所有有效manager来源，403刷新并清理失权选中视角，不伪造Bot token。新增panel挂入现有 `pages/CollaborationPrivacy/index.tsx` 的BCS协作权限区域：Bot视角显示当前ownership/manager管理，Human视角显示转交收发件，不重做页面布局，不改BotWorkshop的Backend owner/space模型，文案明确“仅BCS ownership；原owner保留manager；不迁移部署和凭据”。Human共同建群传Human originator、受控Bots和正确private Group；不替Bot造好友。

- [ ] **Step 4 — GREEN。** 上述Jest、`npm --prefix apps/frontend-nextgen run typecheck`，再运行现有身份/群消费回归；缺公共依赖或环境时记录，不使用私有registry补洞。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `feat(frontend): consume bot authority and confirmed ownership transfers`；由前端模块评审消费方改动。

### Task 20：全链路验收、成本与发布证据

**Files:**
- Create: `apps/bcs/crates/bootstrap/bcs/tests/e2e_bot_authority.rs`
- Create: `apps/bcs/crates/bootstrap/bcs/tests/e2e_ownership_transfer.rs`
- Modify: `apps/bcs/scripts/e2e-test/stories.sh`、`cli-stories.sh`、`e2e.sh`
- Create: `apps/bcs/scripts/e2e-test/bot_authority.sh`
- Read: `apps/bcs/scripts/adapters_endpoint_coverage.py`、`cli_command_coverage.py`
- Modify: `apps/bcs/scripts/test_adapters_endpoint_coverage.py`（为新team slice添加发现/命中测试；仅真实解析缺陷时修parser，不删分母）
- Create: `apps/bcs/docs/superpowers/plans/2026-10-08-bot-owner-manager-validation.md`
- Modify: `apps/bcs/docs/runbooks/bot-authority-cutover.md`

**Interfaces:** consumes全部生产装配；produces逐项可复现验证记录，不增加业务接口，不用测试专用domain直调提高Singlebox覆盖率。

- [ ] **Step 1 — 写 live E2E RED。** 使用正式Human/API凭证和可信平台fixture，顺序执行注册A/X、授予B、mine标签、无friend共同建private群、session/file/ws、team move、A→B确认、撤销A非team、旧creator拒绝：

```rust
assert_eq!(a_mine_after_transfer["access_relation"], "manager");
assert_eq!(b_mine_after_transfer["access_relation"], "owner");
assert_eq!(former_owner_after_last_source_revoke_status, 403);
assert_eq!(pending_rows_for_bot, 0);
assert_eq!(active_owner_rows_for_bot, 1);
assert_eq!(runtime_friend_edges_after, runtime_friend_edges_before);
```

endpoint清单由router解析，bcs-cli leaf由--help动态发现；新增故事必须真实命中新端点，不手工标covered。独立maintenance binary通过integration/运维演练验证，不假装是bcs-cli leaf。SQL核对只在测试证据中，不成为客户端实现；补重启、双实例、响应丢失、慢连接与DB故障故事。POST/DELETE运维修复也计endpoint覆盖，不仅测正常PUT。

- [ ] **Step 2 — 聚焦RED/GREEN迭代。** `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test e2e_bot_authority --test e2e_ownership_transfer`；未配置服务/数据库导致失败不算功能RED，通过真实用例定位缺口再回对应任务修复。
- [ ] **Step 3 — 运行完整相关门禁并保存实际证据。**

```bash
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-service-api -p bcs-edge-permission -p bcs-edge-permission-store \
  -p bcs-bot-store -p bcs-bot -p bcs-app-bot -p bcs-app-group \
  -p bcs-app-session -p bcs-app-invitation -p bcs-app-register \
  -p bcs-group -p bcs-session -p bcs-session-store -p bcs-session-file -p bcs-session-file-store \
  -p bcs-group-store -p bcs-message-store -p bcs-chat-run-store \
  -p bcs-api-http -p bcs-http -p bcs-ws \
  -p bcs-jwt -p bcs-message-flow
(cd apps/bcs && bash scripts/ci/arch-check.sh)
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1 \
  --entrypoint internal.yaml --path-prefix /api/v1/collaboration/
cargo test --manifest-path apps/bcs/Cargo.toml --workspace
npm --prefix apps/frontend-nextgen run ci
singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
git diff --check
```

internal validator需先完成Task13精确team路径识别，不能把prefix参数改成宽泛`/api/v1/`规避校验。live MySQL运行共享authority/lifecycle/transfer完整合同与完整迁移链，用现有 `BCS_TEST_MYSQL_URL` 注入测试DSN，不把凭据写日志或文档。新authority/lifecycle/transfer各driver增加ignored MySQL case，显式 `-- --ignored` 运行；不把默认跳过报告为通过。成本记录正常/最大快照、深分页、transfer旧历史、128边界fan-out、慢writer、持续故障下SQL次数/扫描/锁/池等待；超过预算先调整方案并复审，不擅自取消持续授权。

- [ ] **Step 4 — 完成发布核对。** 记录实际命令/结果、未执行原因、AC/OT矩阵、迁移候选与冲突处置、回滚演练。检查所有新增/修改source行数≤1000、schema/配置/OpenAPI/conformance/endpoint/CLI覆盖同步；不降低既有Singlebox阈值。
- [ ] **Step 5 — 审阅检查点。** 建议提交 `test(bcs): verify bot authority migration and lifecycle end to end`；按根PR模板填写Problem/Solution/Validation/Compatibility and risk/Spec，不自动push或标ready。

## Spec → 任务 → 验收追踪

| Spec范围 | 实现任务 | 必须覆盖的验收 |
| --- | --- | --- |
| §1—4 事实源/主体/不变量 | Gate0、1、3、12、18 | AC10、AC11、AC17、AC19、OT20、OT27 |
| §5 来源、唯一性、审计/并发 | 1—5、7—8 | AC02、AC09、AC13、AC14、AC18、AC20、OT04、OT07、OT13、OT17 |
| §6 manager/team API | 4、7、13 | AC01、AC09、AC26、AC27、AC28、AC29 |
| §7 mine/标签/分页 | 9、19 | AC01、AC02、AC03、AC21、OT03 |
| §8 Group/Session与Human sponsorship | 10—11、19 | AC04、AC05、AC06、AC07、AC08、AC22、AC23、AC24、AC25 |
| §9—11 转交状态机/HTTP | 8、14 | OT01、OT02、OT04、OT05、OT06、OT07、OT08、OT09、OT10、OT11、OT12、OT13、OT14、OT18、OT21 |
| §12—13 认证/注册/隔离 | 5—6、11—12、18 | AC10、AC11、AC12、AC17、AC19、OT15、OT16、OT20、OT25、OT26、OT27；§12.5普通业务审计（Tasks 1/2/9—12：schema、必需上下文、原子写、外部I/O与重试），Task11含真实store RED |
| §14/17 持续授权与预算 | 3—4、7—8、9—12、15—16、20 | AC14、AC15、AC16、OT19、OT22；SkipMessage不关连接与审计SQL成本 |
| §15/16 传播/迁移/回滚 | 5—6、9—14、17—20 | AC18、AC19、AC21、OT15、OT16、OT17、OT23、OT24、OT25、OT26、OT27 |
| §18 验证/文件规模 | 全部任务、20 | AC01—AC29、OT01—OT27，56项均有任务归属 |

## 计划自审与本次验证范围

- [ ] 每个任务执行者完成RED→GREEN证据后才勾选，计划中所有未勾选项均不代表完成。
- [ ] 开始实施前重查Gate0、实际路径、迁移号和依赖接口；任何新产品规则先回写spec，而不是仅修改计划。
- [ ] 逐项检查共享类型/方法与前序声明一致；新增合同更新所有实现、Noop、recording doubles和conformance。
- [ ] 每个任务只提交其自身文件与必要传播；变更完成再做全分支审阅，不用单任务测试代替集成验收。

2026-10-08 本次仅编写计划和修订设计。已执行静态校验：Markdown代码围栏闭合、8个spec JSON合法、56项编号/任务映射完整、20个任务各有Files/Interfaces/RED/实现/GREEN/检查点、既存文件路径与`git diff --check`通过；当前公开/internal OpenAPI validator分别通过72/23个operation（YAML未修改，不证明拟议API已实现）；计划中的新增类型、schema、API和测试均尚不存在/未执行。Cargo、架构全门禁、MySQL、WS跨实例、Frontend CI和Singlebox证据必须由执行阶段产生，不能引用本次文档检查冒充实现验收。

2026-10-08 评审补遗：Task 11 显式承担 spec §12.1(6) operator/effective actor 写审计并进入追踪表；Task 1/2 统一非角色边 `none/none` 固定编码，与 spec §5.1/§5.2 对齐；Task 16 注明 Human 出向 SSE 当前不存在、条款为条件性约束。无任务删减，时间估计与执行顺序不变。

2026-10-08 终审修订：Task 14 的 Invalidated 映射改为按持久行 `terminal_reason` 复核——owner_changed 重试仍为 409/ownership_changed（OT12 同码承诺），公共命名段与 Task 8 RED 同步明确重试语义；新建仅 expected_owner_version 过时归 409/ownership_changed，Task 14 RED 断言不落单；清理 "friend GrantKind" 的不实表述；聚焦门禁清单补 bcs-jwt、bcs-message-flow。

2026-10-08 复审落实：Task1/2定义业务审计类型和独立schema，Tasks9—12分别贯穿本资源Core/Repo，Task11补真实store/I/O失败测试；Tasks15/16使用三态派发结果并测试不可见帧不关合法连接。保留全部20个任务和56项验收映射；本轮只改文档，用户已明确要求amend，未授权push或业务实现。
