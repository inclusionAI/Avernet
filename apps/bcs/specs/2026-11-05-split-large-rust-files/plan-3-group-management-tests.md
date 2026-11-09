# Group Management 测试职责拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 4423 行 Group Management 测试拆成独立业务主题，保留现有入口和无 Session 创建测试。

**Architecture:** 保持 `management` integration target 和既有 `no_session` 子模块名称。共享 Fixture/Session/Registry/关系 fake 作为根的私有直接子模块，测试通过原 trait 服务验证，业务实现不变。

**Tech Stack:** Rust workspace、Cargo/nextest、Tokio、Python 3（仅验证清单）；沿用 crate 现有依赖。

**Spec:** [共用 spec](spec.md)，重点为第 3、6、7、8 节。

## Global Constraints

- 原目标文件及该 plan 所有新增、修改的 Rust 文件均不超过 **1000 物理行**；建议不超过 800 行。
- 函数体、断言、请求内容和调用顺序默认原样迁移；保留公共类型、方法签名和现有入口。
- 不新增业务功能；不改变权限、协议、超时、重试、错误或持久化语义。
- 不新增 production / dev dependency、公共 crate、通用测试框架或公共 API。
- 不运行 `cargo fmt` / `cargo fmt --all` 或全局格式化；仅调整必要的模块、import、path 和最小可见性。
- 不使用 `include!` / 数字分片，不删除或忽略测试，不修改 CI 阈值或覆盖率排除规则。
- 仅执行本 plan；不依赖另外两个 plan 的代码或新增 helper，不能扩张其写入范围。
- 无数据库/schema/config 迁移；原有 I/O 次数、顺序、锁和重试保持不变。

## 阅读、基线与工作方式

先读仓库 `AGENTS.md`、`apps/bcs/AGENTS.md`、`apps/bcs/CLAUDE.md`、spec 第 2 节的
架构文档与本 crate 的 `CONTEXT.md`。开始执行时用 using-git-worktrees 流程检查隔离
工作区，优先复用合适的 worktree；本计划编写阶段不创建 worktree、不修改 Rust。

行号锚点基于源码 `41f728aaa`，文档以同目录共用 spec 为准；实施前用实际 HEAD 重采清单。
若上游新增测试/方法，先补充本 plan 的归属表，再迁移；不照旧行号机械剪切。

这是纯重构：使用已有测试作为行为回归保护，先取得绿色基线，文件大小检查先红后绿。
不为了制造 TDD 红灯删除测试或修改业务逻辑。中间任务的根文件可能仍超过 1000 行，
不把中间提交当作可交付版本；本 plan 全部完成后才可推送并声明满足文件大小要求。

所有命令在仓库根目录的同一个 Bash 会话执行；保存 `EVIDENCE` / `SOURCE_BASE`，
执行任务顺序不跨 plan。验证脚本和运行日志只放临时证据目录，不提交本地运行状态。

## 文件结构与归属

本节的 `T` 是 `apps/bcs/crates/services/bcs-group/tests`；表内路径均相对 `T/`。
所有新增文件均位于既有 `management/`，不创建额外 Cargo 顶层测试入口。

| 动作 / 路径 | 职责 / 预计行数（含接线留量） |
| --- | --- |
| Modify `management.rs` | 原公共 import、support 挂载/导入与主题声明；约 100 行 |
| Create `management/creation.rs` | 11 个常规创建/配额/策略/角色/可达性测试；约 420 行 |
| Create `management/initial_session.rs` | 7 个初始化测试与 `InitialRunSystemMessage`；约 310 行 |
| Create `management/query_projections.rs` | 3 个详情/通知模式投影测试；约 220 行 |
| Create `management/query_lists.rs` | 6 个分页/排序/过滤测试；约 390 行 |
| Create `management/members.rs` | 10 个成员/参与模式测试与 `RecordingParticipantViewBindings`；约 550 行 |
| Create `management/mutations.rs` | 2 个状态/次级变更测试；约 145 行 |
| Create `management/dm.rs` | 4 个 DM 创建/复用/限制测试；约 230 行 |
| Create `management/deletion.rs` | 4 个删除测试与 `RecordingChannelBindingCleanup`；约 210 行 |
| Create `management/interaction.rs` | 2 个 interaction resolve 测试；约 130 行 |
| Create `management/workbench_connect.rs` | 4 个 connect/lease 测试与 `CallerEnabledControlPlane`；约 300 行 |
| Create `management/workbench_chat.rs` | 4 个发送者/参与状态/DM proxy 测试；约 265 行 |
| Create `management/workbench_abort.rs` | 2 个 abort 授权测试；约 145 行 |
| Create `management/human_authority.rs` | 15 个 Human 角色与 originator/driver 权限测试；约 470 行 |
| Create `management/visibility.rs` | 10 个 visibility 测试；约 305 行 |
| Keep `management/no_session.rs` | 原 3 个测试；89 行，不变更其业务测试体 |
| Create `management/support/fixture.rs` | 原 `Fixture` 与构造/依赖注入方法；约 165 行 |
| Create `management/support/builders.rs` | `create_cmd`、`participant`、`bot`、`human`；约 80 行 |
| Create `management/support/session.rs` | 原 Session management + authorization fake 与 `test_session`；约 210 行 |
| Create `management/support/registry.rs` | `FakeRegistry` 与所有固有/trait impl；约 180 行 |
| Create `management/support/friend.rs` | `FakeFriendCoreService` 与 `canonical_pair`；约 95 行 |
| Create `management/support/relation.rs` | `FakeRelationCoreService` 与所有 impl；约 125 行 |
| Create `management/support/runtime.rs` | `FakeBotRuntimeConnectionService` 与原 impl；约 60 行 |

预计值不是代码压缩目标。原测试中多个不连续代码段属于同一主题，按名称归属移动，
不是把原文件从上到下切块。`no_session` 现有文件与挂载路径优先完全不动。

## 内部接口与可见性

support 的物理目录不形成额外逻辑父模块。各文件通过根挂载为直接子模块，可使用
`use super::*;` 延续原依赖；这样对测试开放的字段/固有方法只需 `pub(super)`。

- `Fixture`、`new() -> Self`、`service_with_limits(usize, usize, usize) -> GroupManagement`、
  `service_with_limits_and_session(usize, usize, usize, Arc<StaticSessionManagement>) -> GroupManagement`、
  `service_with_system_message(Arc<dyn SystemMessageService>) -> GroupManagement` 以及原 builder：
  `with_bot(self, &str, &str, &str, Option<&str>) -> Self`、
  `with_human(self, &str, &str) -> Self`、
  `with_provider_downlink_bot(self, &str, &str, &str, Option<&str>) -> Self`、
  `with_friendship(self, &str, &str) -> Self` 供主题使用，设 `pub(super)`；`group` / `relation` 供断言读取，设 `pub(super)`。
  `registry` / `friend` / `authority` / provider 集合不因迁移向测试开放。
- `FakeRegistry` 与 `insert(&self, RegisteredBot)` 对 Fixture 开放，内部 `bots` map 保持私有。
- `FakeFriendCoreService` 与 `insert(&self, &str, &str)` 对 Fixture 开放，`pairs` 与
  `canonical_pair(&str, &str) -> (String, String)` 仍仅在 friend 文件内私有。
- `FakeRelationCoreService` 与 `insert_creator(&self, &str, &str, &str)` 对 Fixture/mutations
  开放，`edges` map 保持私有，所有 RelationCoreService 的行为与 env key 保持原样。
- `StaticSessionManagement`、`new(Session) -> Self`、`failing_create(Session) -> Self`、
  `commands: Mutex<Vec<CreateOrReactivateCommand>>` 对主题开放；`session`/`fail_create` 私有。
  必须同时搬迁该类型的 `SessionAuthorizationCoreService` 和 `SessionManagementService` impl。
- `test_session(&str, &str, Vec<Participant>) -> Session` 保留所有原字段默认值。
- runtime fake 的类型和 `provider_downlink_bots: Arc<RwLock<HashSet<String>>>` 对 Fixture 开放，
  原实现内的连接/target 行为不变；不新增 session 或 authority 读。
- 三类 recording port / InitialRun fake / CallerEnabled fake 留在对应主题内私有。

## 测试迁移清单（完整静态归属）

每个条目连同其测试属性、注释、完整函数体原样移动；本表用于定位，不替代实际发现清单。

| 原函数名 | 目标模块（相对本 plan 的主题目录） | 原起始行 |
| --- | --- | ---: |
| `interaction_resolve_uses_current_exact_session_relationships` | `interaction` | 36 |
| `interaction_resolve_rejects_absent_human_and_non_human_actor` | `interaction` | 98 |
| `create_group_authorizes_human_caller_with_any_driver` | `creation` | 258 |
| `create_group_without_explicit_id_uses_native_namespace` | `creation` | 300 |
| `create_group_human_caller_with_ownerless_driver_ok` | `creation` | 321 |
| `create_group_fails_and_rolls_back_when_initial_session_creation_fails` | `initial_session` | 349 |
| `create_state_machine_group_auto_creates_service_invocation_session` | `initial_session` | 389 |
| `create_chat_group_accepts_session_scoped_opening_message` | `initial_session` | 443 |
| `create_state_machine_group_with_human_caller_adds_present_human_to_initial_session` | `initial_session` | 469 |
| `query_methods_list_detail_and_bot_groups` | `query_projections` | 505 |
| `group_application_projections_expose_human_mention_notify_mode` | `query_projections` | 566 |
| `dm_projection_exposes_human_mention_notify_mode_for_visibility_only` | `query_projections` | 653 |
| `create_manager_worker_group_requires_exactly_one_manager` | `creation` | 696 |
| `create_manager_worker_group_allows_provider_downlink_bot` | `creation` | 736 |
| `create_manager_worker_group_returns_only_manager_send_as_initial_run` | `initial_session` | 759 |
| `create_chat_group_returns_driver_send_as_initial_run` | `initial_session` | 790 |
| `queued_group_bootstrap_returns_queued_initial_run` | `initial_session` | 820 |
| `add_member_allows_provider_downlink_bot_in_manager_worker_group` | `members` | 842 |
| `add_member_assigns_worker_in_manager_worker_group` | `members` | 877 |
| `list_groups_orders_by_updated_at_desc_before_pagination` | `query_lists` | 913 |
| `list_bot_groups_filters_absent_participant_groups` | `query_lists` | 962 |
| `list_bot_groups_orders_by_updated_at_desc_after_filtering` | `query_lists` | 1025 |
| `list_bot_groups_filters_query_by_label_only` | `query_lists` | 1090 |
| `list_bot_groups_returns_empty_when_actor_only_has_absent_groups` | `query_lists` | 1144 |
| `list_bot_groups_keeps_bot_groups_with_absent_human_members` | `query_lists` | 1187 |
| `update_status_parses_status_and_rejects_unknown_status` | `mutations` | 1264 |
| `add_member_authorizes_coordinator_and_checks_reachability` | `members` | 1301 |
| `add_member_writes_subscription_edge_with_driver_identity_for_public_targets` | `members` | 1405 |
| `create_group_persists_typed_routing_policy_and_validates_sender_routes` | `creation` | 1479 |
| `create_group_maps_participant_roles_and_reachability` | `creation` | 1536 |
| `create_group_allows_private_friend_participant` | `creation` | 1581 |
| `create_group_writes_subscription_edge_for_public_participants_only` | `creation` | 1609 |
| `create_group_hides_private_non_friend_participant` | `creation` | 1652 |
| `create_group_enforces_member_limits` | `creation` | 1678 |
| `create_dm_creates_and_reuses_human_bot_pair` | `dm` | 1701 |
| `create_dm_without_explicit_id_uses_dm_namespace` | `dm` | 1772 |
| `create_dm_enforces_human_to_bot_reachability` | `dm` | 1800 |
| `create_dm_rejects_mismatched_driver_bot_for_human_caller` | `dm` | 1864 |
| `workbench_human_bot_dm_rejects_owner_bot_proxy_sender_by_default` | `workbench_chat` | 1892 |
| `delete_group_enforces_legacy_driver_and_dm_rules` | `deletion` | 1936 |
| `delete_group_is_idempotent_when_group_is_missing` | `deletion` | 1995 |
| `delete_group_cleans_up_channel_bindings` | `deletion` | 2011 |
| `delete_group_keeps_committed_deletion_when_channel_binding_cleanup_fails` | `deletion` | 2043 |
| `group_secondary_mutations_run_through_management_service` | `mutations` | 2078 |
| `participant_mode_update_authorizes_self_or_creator_and_inserts_human` | `members` | 2149 |
| `workbench_session_service_connects_for_owner_and_authorizes_owned_sender` | `workbench_connect` | 2265 |
| `state_machine_group_connect_requires_lease_with_caller_enabled_members` | `workbench_connect` | 2400 |
| `chat_group_connect_aggregates_a_caller_enabled_member_to_lease_required` | `workbench_connect` | 2439 |
| `workbench_normal_group_allows_bound_human_sender_when_participant` | `workbench_chat` | 2477 |
| `workbench_chat_send_allows_bound_human_sender_when_present_in_session` | `workbench_chat` | 2513 |
| `workbench_session_service_rejects_invalid_sender_states` | `workbench_chat` | 2553 |
| `workbench_connect_allows_bound_human_when_present_in_session` | `workbench_connect` | 2659 |
| `create_group_with_human_consultant_chat` | `human_authority` | 3504 |
| `create_group_with_human_worker_manager_worker` | `human_authority` | 3537 |
| `create_group_rejects_human_as_driver` | `human_authority` | 3571 |
| `create_group_rejects_human_as_manager` | `human_authority` | 3592 |
| `create_group_rejects_all_human_group` | `human_authority` | 3616 |
| `create_group_human_owner_not_participant_ok` | `human_authority` | 3637 |
| `create_group_rejects_human_driver_bot_id` | `human_authority` | 3660 |
| `add_member_human_consultant_ok` | `human_authority` | 3683 |
| `add_member_human_worker_in_manager_worker_ok` | `human_authority` | 3713 |
| `originator_human_can_delete_group` | `human_authority` | 3743 |
| `originator_human_can_add_member` | `human_authority` | 3769 |
| `originator_human_can_update_label` | `human_authority` | 3798 |
| `non_coordinator_cannot_delete_group` | `human_authority` | 3824 |
| `originator_bot_self_driver_can_manage` | `human_authority` | 3850 |
| `originator_and_driver_different_both_can_manage` | `human_authority` | 3889 |
| `create_public_group_with_all_public_bots_succeeds` | `visibility` | 3945 |
| `create_public_group_rejects_non_public_bot` | `visibility` | 3964 |
| `create_group_rejects_invalid_visibility` | `visibility` | 3984 |
| `update_visibility_to_public_with_all_public_bots` | `visibility` | 4003 |
| `update_visibility_to_public_rejects_non_public_bot` | `visibility` | 4027 |
| `update_visibility_to_private_always_succeeds` | `visibility` | 4057 |
| `update_visibility_rejects_non_coordinator` | `visibility` | 4080 |
| `add_non_public_bot_to_public_group_rejected` | `visibility` | 4102 |
| `add_human_to_public_group_succeeds` | `visibility` | 4129 |
| `list_groups_filters_by_visibility` | `visibility` | 4152 |
| `bot_can_self_leave_group` | `members` | 4213 |
| `human_owner_can_remove_their_bot_from_group` | `members` | 4234 |
| `driver_cannot_self_leave_group` | `members` | 4255 |
| `non_owner_cannot_remove_others_bot_from_group` | `members` | 4277 |
| `coordinator_can_still_kick_members` | `members` | 4300 |
| `workbench_chat_abort_allows_session_bot_owner_and_absent_target` | `workbench_abort` | 4321 |
| `workbench_chat_abort_rechecks_human_mode_and_authentication` | `workbench_abort` | 4372 |

静态分组核对：`interaction` 2 个；`creation` 11 个；`initial_session` 7 个；`query_projections` 3 个；`members` 10 个；`query_lists` 6 个；`mutations` 2 个；`dm` 4 个；`workbench_chat` 4 个；`deletion` 4 个；`workbench_connect` 4 个；`human_authority` 15 个；`visibility` 10 个；`workbench_abort` 2 个。

## 执行前基线（并入 Task 1，不单独交付脚手架）

- [x] 确认当前工作区干净、工具存在；读取上节指令和实际源码。

```bash
set -euo pipefail
export SOURCE_BASE="$(git rev-parse HEAD)"
export EVIDENCE="$(mktemp -d /tmp/avernet-split-p3.XXXXXX)"
printf '%s\n' "$SOURCE_BASE" > "$EVIDENCE/source-base.sha"
git show "$SOURCE_BASE:apps/bcs/crates/services/bcs-group/tests/management.rs" > "$EVIDENCE/source.rs"
cargo --version > "$EVIDENCE/toolchain.txt"
cargo nextest --version >> "$EVIDENCE/toolchain.txt"
cargo metadata --manifest-path apps/bcs/Cargo.toml --no-deps --offline --locked \
  --format-version 1 > "$EVIDENCE/metadata.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-group \
  --message-format json > "$EVIDENCE/package.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management \
  --message-format json > "$EVIDENCE/target.before.json"
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group \
  > "$EVIDENCE/baseline.log" 2>&1
```

预期：上述命令成功；完整 crate 的未忽略测试通过。`list` 包含 ignored 元数据，
不是要求默认执行依赖外部环境的 ignored 测试。失败就保留日志并报告，不能静默记为绿色。
如需特定 features，前后所有发现/执行命令都使用同一组 features 并在证据里记录。

- [x] 执行现状大小断言，预期内部检查 **FAIL**，确认能发现原超大文件。下列 `if`
  记录预期失败而不结束 Bash 会话；如果检查意外通过，停止并重新核实是否仍需要拆分。

```bash
if python3 - <<'CHECK'
from pathlib import Path
p = Path('apps/bcs/crates/services/bcs-group/tests/management.rs')
n = len(p.read_bytes().splitlines())
assert n <= 1000, (str(p), n)
CHECK
then
  echo '原文件大小断言已通过：停止并重新核实本计划基线' >&2
  exit 2
else
  echo '已记录超大文件的预期大小失败；开始行为保持迁移'
fi
```

- [x] 审计路径与过滤消费者，并把命中及处理决定放进 `$EVIDENCE/path-audit.txt`。

```bash
rg -n --glob '*.rs' --glob '*.py' --glob '*.sh' --glob '*.toml' \
  'contract_message_flow|tests/management\.rs|client\.rs|#\[path|include_str!|include_bytes!|module_path!|file!' \
  apps/bcs/crates/services/bcs-group apps/bcs/scripts apps/bcs/.config \
  > "$EVIDENCE/path-audit.txt" || test "$?" -eq 1
```

## Tasks

### Task 1：Fixture 与 Session/关系 fake 分责，并保持 no_session 兼容

**Files:** Modify `apps/bcs/crates/services/bcs-group/tests/management.rs`；Create 上表七个
`management/support/*.rs`。Read-only `management/no_session.rs`。
**Test:** `management` target，必须包含原 no_session 三个测试。
**Interfaces:** Consumes 原 GroupStore/authority/services；Produces 上节所有原 Fixture/builder/fake 接口。

- [x] 完成前述基线；核对 `management` 原 84 个根测试与 `no_session` 3 个测试均被发现。
- [x] 逐类型搬迁完整结构与全部 impl，包含两种 Session trait 实现；按上节调整可见性。
  原入口保留所有原公共类型 import，并新增以下完整 helper 挂载/导入：

```rust
#[path = "management/support/fixture.rs"]
mod fixture;
#[path = "management/support/builders.rs"]
mod builders;
#[path = "management/support/session.rs"]
mod session_support;
#[path = "management/support/registry.rs"]
mod registry_support;
#[path = "management/support/friend.rs"]
mod friend_support;
#[path = "management/support/relation.rs"]
mod relation_support;
#[path = "management/support/runtime.rs"]
mod runtime_support;
use fixture::Fixture;
use builders::{bot, create_cmd, human, participant};
use session_support::{StaticSessionManagement, test_session};
use registry_support::FakeRegistry;
use friend_support::FakeFriendCoreService;
use relation_support::FakeRelationCoreService;
use runtime_support::FakeBotRuntimeConnectionService;
```

保留既有 `#[path = "management/no_session.rs"] mod no_session;`；这些根别名让其
原 `use super::*;` 无需改动。各 support 首行 `use super::*;`，只删除原重复定义。

- [x] 原目标整体运行一次，再单独运行无 Session 测试，预期 3 个用例通过。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management no_session::
```

- [x] 核实 lazy live authority seed、原 Arc 共用、Session 授权快照来自同一 Session，
  没有创建第二套独立 fake；新文件 ≤1000 行，提交：
  `refactor(bcs): isolate group management test fixtures`。

### Task 2：创建、查询与初始化主题化

**Files:** Modify 原入口；Create `creation.rs`、`initial_session.rs`、`query_projections.rs`、`query_lists.rs`。
**Interfaces:** Consumes Task 1 Fixture/session；Produces 原 27 个业务场景，初始化结果不变。

- [x] 按归属表迁移 27 个测试及 `InitialRunSystemMessage`，保留 created/queued initial run、
  Session 创建失败回滚、通知模式投影与排序分页断言。各主题 `use super::*;`。
- [x] 根入口增加下列声明，删除原对应测试与局部 fake：

```rust
#[path = "management/creation.rs"]
mod creation;
#[path = "management/initial_session.rs"]
mod initial_session;
#[path = "management/query_projections.rs"]
mod query_projections;
#[path = "management/query_lists.rs"]
mod query_lists;
```

- [x] 运行原目标全量测试，确认 `no_session` 仍在同一 target；新文件均 ≤1000 行。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management
```

- [x] 审查移动差异并提交：`refactor(bcs): separate group creation and query tests`。

### Task 3：成员、次级变更、DM、删除、可见性主题化

**Files:** Modify 原入口；Create `members.rs`、`mutations.rs`、`dm.rs`、`deletion.rs`、`visibility.rs`。
**Interfaces:** Consumes 原 Fixture/fake；Produces 原 30 个业务场景，无 persistence 语义变更。

- [x] 按归属表迁移 30 个测试；`RecordingParticipantViewBindings` 留在 members 私有，
  `RecordingChannelBindingCleanup` 留在 deletion 私有，不提取共享基础框架。
- [x] 原入口增加以下接线并删除对应原测试，主题都使用 `use super::*;`：

```rust
#[path = "management/members.rs"]
mod members;
#[path = "management/mutations.rs"]
mod mutations;
#[path = "management/dm.rs"]
mod dm;
#[path = "management/deletion.rs"]
mod deletion;
#[path = "management/visibility.rs"]
mod visibility;
```

- [x] 运行原目标全量测试，审查 subscription edge、模式 scope-change lease、好友/私有可达性、
  DM pair、删除已提交后清理失败不回滚的原断言；文件大小均达标。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management
```

- [x] 提交：`refactor(bcs): isolate group membership and lifecycle tests`。

### Task 4：Workbench 与 Human 权限主题化，完成独立验收

**Files:** Modify 原入口；Create `interaction.rs`、`workbench_connect.rs`、`workbench_chat.rs`、
`workbench_abort.rs`、`human_authority.rs`。
**Interfaces:** Consumes 同一 Fixture/Session authority；Produces 原 27 个场景以及完整 87 个入口测试。

- [x] 按归属表迁移 27 个测试；`CallerEnabledControlPlane` 只放 connect 主题。
  精确 Session 参与者、Human present/absent、originator/driver 和 caller lease 判断不变。
- [x] 根入口增加下列声明，主题首行 `use super::*;`，根中不残留业务测试或单主题 fake。

```rust
#[path = "management/interaction.rs"]
mod interaction;
#[path = "management/workbench_connect.rs"]
mod workbench_connect;
#[path = "management/workbench_chat.rs"]
mod workbench_chat;
#[path = "management/workbench_abort.rs"]
mod workbench_abort;
#[path = "management/human_authority.rs"]
mod human_authority;
```

- [x] 执行下面的清单映射、全量测试和大小检查，预期 84 条根测试建立映射，原 no_session
  三个完整名称不变且均纳入 package 清单比较；确认未改任何 Group 生产源码。
- [x] 差异审查后提交：`refactor(bcs): complete group management test decomposition`。
- [x] 最终源码提交上执行架构、覆盖率和统一 Singlebox；填写交付记录，不跨入其他 plan。

## 末任务使用的清单一致性检查（可直接执行）

每个迁移任务后先运行目标测试；全部迁移完成后，执行以下完整检查。它保留未迁移
测试的完整名称，只对源文件中的迁移测试允许模块前缀变化；比较 binary ID、测试
集合及 ignored 状态，并生成逐测试映射，重复挂载会导致失败。

```bash
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-group \
  --message-format json > "$EVIDENCE/package.after.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-group --test management \
  --message-format json > "$EVIDENCE/target.after.json"
python3 - <<'CHECK'
import json, os, re
from pathlib import Path
p = Path(os.environ['EVIDENCE'])
source = (p / 'source.rs').read_text()
pattern = (r'^\s*#\[(?:tokio::)?test(?:\([^\n]*\))?\]\s*'
           r'(?:#\[[^\n]+\]\s*)*(?:async\s+)?fn\s+(\w+)\(')
moved = set(re.findall(pattern, source, re.M))
assert len(moved) == 84, '源码已漂移：先更新归属表，不可丢弃新增测试'
load = lambda n: json.loads((p / n).read_text())['rust-suites']
tb, ta = load('target.before.json'), load('target.after.json')
assert len(tb) == len(ta) == 1 and tb.keys() == ta.keys()
target_id = next(iter(tb))
def collect(filename):
    result = {}
    for sid, suite in load(filename).items():
        for name, case in suite.get('testcases', {}).items():
            leaf = name.rsplit('::', 1)[-1]
            moving = sid == target_id and leaf in moved and (True)
            canonical = '@moved:' + leaf if moving else name
            key = (sid, canonical)
            assert key not in result, ('重复测试/重复挂载', key)
            result[key] = (case['ignored'], name)
    return result
before, after = collect('package.before.json'), collect('package.after.json')
assert before.keys() == after.keys(), (before.keys() - after.keys(), after.keys() - before.keys())
rows = []
for key, (ignored, old_name) in before.items():
    new_ignored, new_name = after[key]
    assert ignored == new_ignored, ('ignored 属性改变', old_name)
    if key[0] == target_id and key[1].startswith('@moved:'):
        rows.append((key[0], old_name, new_name))
assert len(rows) == 84
(p / 'test-map.tsv').write_text(''.join('\t'.join(row) + '\n' for row in sorted(rows)))
print('PASS: full package discovery stable; mapped', len(rows), 'moved tests')
CHECK
```

对照本 plan 归属表逐行审阅 `test-map.tsv` 的新模块前缀；不能只满足数量相同。
`allow`、其他 `cfg`、超时属性和源码断言还要通过移动差异审查验证，不能只依赖 ignored 比对。

## 末任务的交付验证与提交边界

- [x] 执行所属 crate 全量测试、编译及路径/属性差异审查。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-group
cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-group --all-targets
git add -N -- apps/bcs/crates/services/bcs-group/tests/management
git diff --check
# 每次对本 plan 的执行基线审查迁移差异；不以格式化后的 diff 替代。
git diff "$SOURCE_BASE" --color-moved=blocks -- apps/bcs/crates/services/bcs-group/tests/management.rs apps/bcs/crates/services/bcs-group/tests/management
python3 - <<'CHECK'
from pathlib import Path
files = set()
for name in ['apps/bcs/crates/services/bcs-group/tests/management.rs', 'apps/bcs/crates/services/bcs-group/tests/management']:
    p = Path(name)
    files.update(p.rglob('*.rs') if p.is_dir() else [p])
for p in sorted(files):
    n = len(p.read_bytes().splitlines())
    print(n, p)
    assert n <= 1000, (str(p), n)
CHECK
```

预期：全量测试与编译通过，受影响 Rust 文件全部不超过 1000 行；测试和 helper 只迁移
一次，原函数体/断言未改变。结合编译输出核实 import/可见性，不扩大公共 API 消除报错。
最终 `git diff --name-only "$SOURCE_BASE"` 中每个修改路径都必须属于本 plan 的明确范围。

- [x] 基于最新实际合并目标计算范围，然后执行架构与单元覆盖率 gate。

```bash
TARGET_REF="${AVERNET_PRE_PUSH_MERGE_TARGET:-$(git config --get avernet.prePush.mergeTarget || printf 'origin/dev')}"
case "$TARGET_REF" in */*) ;; *) echo 'merge target 必须为 remote/branch' >&2; exit 2;; esac
git fetch "${TARGET_REF%%/*}" "${TARGET_REF#*/}"
TARGET_SHA="$(git rev-parse FETCH_HEAD)"
RANGE_BASE="$(git merge-base "$TARGET_SHA" HEAD)" || {
  echo '目标分支与 HEAD 无法计算 merge-base，停止验收' >&2
  exit 2
}
export RANGE_BASE
BCS_BASE_REF="$RANGE_BASE" bash apps/bcs/scripts/ci/arch-check.sh
bash apps/bcs/scripts/ci_test.sh --base "$RANGE_BASE" --head HEAD --fast-fail --coverage
(
  cd apps/bcs
  python3 scripts/cov_gate.py --base-ref "$RANGE_BASE" --bcs-dir "$PWD" \
    --pass-rate-min 100 --overall-line-min 87 --changed-line-min 85 --changed-line-min-lines 51
)
```

必须在最终源码已提交后采集 HEAD 范围报告。不能 fetch 失败后回退到旧 remote SHA、
根提交或推送旧 SHA；不能用目标分支与 HEAD 的直接树差异替代 merge-base。
架构脚本的环境性 SKIP 按实际原因记录，不能把 SKIP 写成通过。

- [x] 执行统一 Singlebox 与报告验证；不调用旧独立 E2E gate 替代。

```bash
BCS_E2E_CHANGED_BASE_REF="$RANGE_BASE" SINGLEBOX_BASE_REF="$RANGE_BASE" \
  bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

保持 spec AC-8/AC-9 的所有门槛：unit pass 100%、overall line >87%，unit changed-line
在至少 51 条时 >85%；BCS E2E 全故事通过、runtime line ≥54%、method ≥48%、HTTP
endpoint/CLI leaf-command 100%，E2E scope 至少 20 条 changed lines 时 >65%。
不通过设置 opt-out 环境变量、缩小默认模块集合或降低阈值绕过。组合交付可以复用同一
最终 SHA 的共享栈报告，但必须注明覆盖本 plan；独立交付不能等待另一个 plan 才验收。

- [x] 在本 plan 的交付记录中填写执行起止 SHA、修改文件行数、测试映射、每条验证命令
  的结果和报告位置。没有工具、服务或网络时，明确标记未验收，不能声称实施完成。
- [x] 最后审查 AC-1 至 AC-10；业务修复另开任务，数据库/缓存/网络访问成本变化应为零。
  回滚只回滚本 plan 的源码迁移及记录，不改数据库、配置或另外两个 plan 的文件。

## Spec 覆盖与编写状态

| Spec 要求 | 本 plan 的落点 |
| --- | --- |
| 按职责拆分且保持行为 | 文件/方法/测试归属表、内部接口与 Task 1–4 |
| AC-1 / AC-2：大小和最小迁移差异 | 各任务新文件检查、末任务全量大小断言与 color-moved 审查 |
| AC-3：路径/过滤依赖 | 执行前 path audit、各任务的模块接线与消费者检查 |
| AC-4 / AC-5：测试不漏、不重复、不弱化 | 完整归属表、前后 nextest 清单脚本、属性与原 body 人工差异审查 |
| AC-6：近端与 crate 全量 | 各任务 Test 命令、末任务完整 crate 测试 |
| AC-7 / AC-8：架构、编译与 unit coverage | 末任务最终提交上的 arch-check / all-targets / cov_gate |
| AC-9 / AC-10：统一 E2E 与报告、不弱化门禁 | 末任务统一 Singlebox、artifact verifier 与固定阈值 |
| 独立交付、回滚和运行成本不变 | Global Constraints、范围审查、最后的交付/回滚记录 |

编写阶段只做源码归属、命令接口和文档自检。执行结果、未达标门禁的实际原因和证据位置
见文末「交付记录」，勾选状态只对实际运行过的条目生效。

## 交付记录

### 执行起止 SHA

| 阶段 | 提交 |
| --- | --- |
| `SOURCE_BASE`（本 plan 基线） | `5b6952f7eb07f0a25df5288274247735840dd471` |
| Task 1 `refactor(bcs): isolate group management test fixtures` | `d2054057e88` |
| Task 2 `refactor(bcs): separate group creation and query tests` | `217e7f161b` |
| Task 3 `refactor(bcs): isolate group membership and lifecycle tests` | `88208cd8b2` |
| Task 4 `refactor(bcs): complete group management test decomposition` | `c49c8306cd` |
| 本交付记录 `docs(bcs): record plan-3 group management split delivery` | 本节所在提交 |

验收使用的最新合并目标与范围：

- `TARGET_REF=origin/dev`，`TARGET_SHA=b281b03683208b751b7547aae3bf698e0eacb406`
  （2026-11-05 `git fetch origin dev` 实际取得，未回退旧 SHA）。
- `RANGE_BASE=41f728aaada7684543da36c0807e7b945314ebc2`
  （`git merge-base "$TARGET_SHA" HEAD`，未用直接树差异替代）。

### 修改文件与行数（物理行，上限 1000）

Task 4 新增/修改（refactor 提交 `c49c8306cd`，仅 `tests/management.rs` 与 5 个新文件）：

| 文件 | 行数 |
| --- | ---: |
| `tests/management.rs`（1207 → 纯接线） | 85 |
| `tests/management/interaction.rs` | 92 |
| `tests/management/workbench_connect.rs` | 283 |
| `tests/management/workbench_chat.rs` | 227 |
| `tests/management/workbench_abort.rs` | 104 |
| `tests/management/human_authority.rs` | 431 |

Task 1–3 已提交的拆分后终态（全 plan 完成后统一复核，全部 ≤1000）：

| 文件 | 行数 |
| --- | ---: |
| `tests/management/creation.rs` | 377 |
| `tests/management/initial_session.rs` | 288 |
| `tests/management/query_projections.rs` | 192 |
| `tests/management/query_lists.rs` | 352 |
| `tests/management/members.rs` | 516 |
| `tests/management/mutations.rs` | 109 |
| `tests/management/dm.rs` | 192 |
| `tests/management/deletion.rs` | 185 |
| `tests/management/visibility.rs` | 267 |
| `tests/management/no_session.rs`（未变更业务体） | 89 |
| `tests/management/support/fixture.rs` | 149 |
| `tests/management/support/builders.rs` | 66 |
| `tests/management/support/session.rs` | 189 |
| `tests/management/support/registry.rs` | 164 |
| `tests/management/support/friend.rs` | 73 |
| `tests/management/support/relation.rs` | 113 |
| `tests/management/support/runtime.rs` | 43 |

`git diff --name-only "$SOURCE_BASE"` 的每个路径都在本 plan 范围内
（`apps/bcs/crates/services/bcs-group/tests/management.rs` 与 `tests/management/` 下的
文件；本交付记录单独由 docs 提交引入）。`git diff --name-only "$SOURCE_BASE" --
apps/bcs/crates apps/bcs/src apps/bcs/migrations | grep -v tests/management` 为空，
未改动任何 Group/BCS 生产源码。

根入口为纯接线：85 行、22 条 `mod` 声明、0 个测试、0 个 fake；5 个主题文件首行均为
`use super::*;`，`CallerEnabledControlPlane`（含 `#[async_trait]` 与全部 impl，70 行）
逐字移入 `workbench_connect.rs` 且仅出现一次。

### 测试映射

- 源码清单脚本：`source.rs` 提取 84 个根测试，`assert len(moved) == 84` 通过。
- `PASS: full package discovery stable; mapped 84 moved tests`（完整 package 清单
  前后 binary ID、测试集合与 `ignored` 状态一致；无重复挂载）。
- 84 行 `test-map.tsv` 逐行对照归属表：0 mismatch，模块前缀全部落在 plan 指定主题：

  | 主题 | 测试数 | 主题 | 测试数 |
  | --- | ---: | --- | ---: |
  | creation | 11 | interaction | 2 |
  | initial_session | 7 | workbench_connect | 4 |
  | query_projections | 3 | workbench_chat | 4 |
  | query_lists | 6 | workbench_abort | 2 |
  | members | 10 | human_authority | 15 |
  | mutations | 2 | visibility | 10 |
  | dm | 4 | deletion | 4 |

- `no_session` 三个测试完整名称不变，仍在同一 `management` target 中参与 package 清单比较。
- 27 个 Task 4 测试块与 `SOURCE_BASE` 字节级一致（含属性、注释与断言顺序）；
  `CallerEnabledControlPlane` 逐字一致。保真证据：`$EVIDENCE/task4.fidelity.txt`。
- 目标测试：`cargo test -p bcs-group --test management` → **87 passed, 0 failed**（84 + no_session 3）。

### 验证命令与结果

证据根目录：`$EVIDENCE = /private/var/folders/5s/v4sf5jfn18qfxvjvtdbbcnnw0000gn/T/opencode/avernet-split-p3`。

| 命令 | 结果 |
| --- | --- |
| `cargo test -p bcs-group --test management` | 87 passed（`task4.management-test.log`） |
| `cargo test -p bcs-group` | 172/172 passed（全部 target，`task4.full-crate-test.log`） |
| `cargo check -p bcs-group --all-targets` | exit 0；`bcs-group` 无新增 warning，唯一 warning 为 `bcs-bot-store/src/lib.rs:24` 的既有 `unused_imports`（基线 `baseline.log` 已存在）（`task4.check-all-targets.log`） |
| `git diff --check` | 无输出，clean |
| 文件大小断言 | 22 个变更 Rust 文件全部 ≤1000 行；大小扫描覆盖含未变更的 `no_session.rs` 在内共 23 个文件，最大 516 行（`task4.sizes.txt`） |
| `arch-check.sh`（`BCS_BASE_REF=$RANGE_BASE`） | **FAIL**：8 pass / 2 skip / 5 fail，见下节说明（`gates/arch.log`） |
| `ci_test.sh --base $RANGE_BASE --head HEAD --fast-fail --coverage` | **exit 100**：2742/2744 后中止，2 个 `bcs-bot-store` 既有失败（`gates/ci_test.log`） |
| `ci_test.sh --base $RANGE_BASE --head HEAD --coverage --reuse-build`（为取得完整覆盖率数据） | 9250 tests：9248 passed、2 failed、65 skipped；写出 junit/cobertura（`gates/ci_test.full-coverage.log`） |
| `cov_gate.py --pass-rate-min 100 --overall-line-min 87 --changed-line-min 85 --changed-line-min-lines 51` | pass-rate **99.98% FAIL**；overall line **87.30% OK**；changed-line **N/A**（0 条可 instrument 的变更源码行，`gates/cov_gate.log`） |
| `singlebox/ci/singlebox_coverage.sh` | **passed**（第 4 次尝试，`gates/singlebox.retry4.log`） |
| `verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports` | exit 0，`singlebox coverage artifacts verified`（`gates/singlebox-verify.log`） |

### 未达标门禁的实际原因（未验收项，不得记为通过）

1. **架构检查 FAIL（5 项）**：`DEP-1/DEP-6`、`LINT-1`、`LINT-4`、`TEST-1`、`R25`。
   在 `RANGE_BASE`（`41f728aaad`）检出同一仓库状态后重跑对应静态检查，
   `check-deps.sh`、`check-import-rules.sh`、`check-trait-naming.sh`、
   `check-conformance-entries.sh`、R25 静态部分输出按排序比较完全一致
   （`gates/base.*.log` vs `gates/head.*.log`），且 `BASELINE` 检查 PASS
   （`forbidden-symbols 1/1`、`trait-naming 1/1`、`conformance-missing 9/9`，无增长）。
   这些失败全部落在本 plan 未触碰的 `crates/services/*`、`crates/service-api/*` 源码，
   与本 plan 无关，属于基线既存红状态；本 plan 未降低任何阈值。
   SKIP 实际原因：`DEP-8` 未安装 `cargo-machete`；`API-1` 未安装 `cargo-public-api`；
   `PR-1` 非 PR 环境无 PR 描述。SKIP 未记为通过。
2. **Unit gate pass-rate 99.98%（<100%）**：2 个失败均在
   `bcs-bot-store::conformance_bot_control_plane_repo`
   （`cases_2::persistent_capability_save_preserves_patched_internal_attributes`、
   `cases_3::memory_control_plane_list_by_task_modes_filters_visibility_status_user_visibility`，
   其中 `cases_3` panic 于 `pub-online row`）。`bcs-bot-store` 不在本 plan 的 diff 范围内
   （`git diff --name-only $RANGE_BASE..HEAD` 不含该 crate），本 diff 未改动其源码、
   测试数据或依赖，因此该失败与本 diff 无因果关系；本 plan 也未修改阈值或排除规则。
   整体行覆盖率与 changed-line 门槛不受影响。
3. **`arch-check` 与 `ci_test` 未达绿**：按要求如实标记为未验收项，不声称实施完成。

### 统一 Singlebox 结论

第 1–3 次尝试失败于本机环境（非代码）：

1. ARCA adapter 端口区间 `20010-20099` 被其他 worktree 遗留进程占满，
   demo bot 创建报 `No free port available in range 20010-20099`。
2. `FRONTEND_PORT=8000` 被 checkout 外的常驻 dev server 占用，frontend 启动失败。
3. openclaw 网关端口区间 `18800-18899` 被遗留进程占满，backend acceptance 中 5 个
   bot 的 `BaaS publish` 失败；随后一次尝试中 5 个 local bot 的 WS 连接在 615s 后断开，
   导致 9 条 E2E story 报 `not connected via WebSocket`。

处置：清理 `ppid=1` 且属于本 worktree 或已停跑一天以上的 `bot-avatar-backend`
worktree 的孤儿 sandbox/gateway 进程（`gates/port-reap.log`），未杀其他 plan 正在使用的
进程；前端改用 `FRONTEND_PORT=8100`（端口配置项，非门禁 opt-out，未改变模块集合与阈值）。
第 4 次尝试通过。

第 4 次尝试结果（`gates/singlebox.retry4.log`、`singlebox/.dependencies/coverage/singlebox/reports/`）：

- Backend acceptance：12 个 target 全部 passed（`status: passed`）。
- BCS E2E：全部 story 通过；runtime line **56.72%**（≥54%）、method **50.23%**（≥48%）、
  HTTP endpoint **173/173 = 100%**、CLI leaf-command **66/66 = 100%**。
- E2E changed-line：`no instrumentable changed lines in e2e scope — not applicable`
  （22 个 changed test-only 文件不计入），非降低阈值。
- 模块阈值：全部达标（如 `expert_chat` core 62.14% ≥ 61.18%、`files` core 69.39% ≥ 63.52%、
  `harness` core 50.75% ≥ 40.86%）。
- 报告摘要：`singlebox/.dependencies/coverage/singlebox/reports/summary.json`（`status: passed`）
  与 `summary.md`、`dashboard.html`；artifact 校验器 exit 0
  （backend 49.43%、BaaS 45.95%、BCS 56.72%/50.23%/100%/100%）。
- 未设置任何 opt-out 环境变量，未缩小默认模块集合，未降低阈值。运行产物
  （`singlebox/.dependencies/`、`apps/bcs/testresult/`、`apps/bcsfuse/uv.lock` 的 uv 版本重写）
  均未提交，`uv.lock` 已还原。

### AC 复核（AC-1 至 AC-10）

| AC | 结论 | 证据 |
| --- | --- | --- |
| AC-1 大小 | 通过：根 85 行，最大文件 516 行，全部 ≤1000 | `task4.sizes.txt` |
| AC-2 最小迁移差异 | 通过：无业务逻辑、依赖、API/schema/config 变更；未运行全局格式化；无新 waiver | `git diff --color-moved` 审查、`git diff --check` |
| AC-3 路径/过滤依赖 | 通过：path audit 仅命中历史文档记录；`#[path]` 接线与消费者一致，无 `include!` | 执行前 `path-audit.txt`、根 22 条 mod |
| AC-4 测试不漏/不重复 | 通过：84 条一一映射，`no_session` 名称不变，`ignored` 一致 | `test-map.tsv`、`task4.discovery-compare.txt` |
| AC-5 断言不变弱 | 通过：27 个测试块与 `SOURCE_BASE` 字节级一致 | `task4.fidelity.txt` |
| AC-6 近端与 crate 全量 | 通过：`--test management` 87/87、`-p bcs-group` 172/172 | 上述日志 |
| AC-7 架构/依赖/编译 | 编译通过；架构检查为基线既存红状态（非本 plan 引入），如实未验收 | `gates/arch*.log`、`gates/base.*.log` |
| AC-8 unit gate 阈值 | overall line 87.30% 达标；pass-rate 99.98% 因 2 个基线既有失败未达 100%，如实未验收；changed-line N/A；未修改阈值/排除 | `gates/cov_gate.log` |
| AC-9 统一 Singlebox + 报告验证 | 通过（第 4 次尝试，环境处置已记录） | `gates/singlebox.retry4.log`、`gates/singlebox-verify.log`、`reports/summary.json` |
| AC-10 不弱化门禁 | 通过：使用统一入口与阈值，无 opt-out、无模块裁剪、无迁移文件排除 | 上述命令原文 |

运行时访问成本：仅移动测试与 helper 定义，未新增数据库/缓存/网络调用、锁、后台任务或重试，
I/O 次数与顺序不变。

### 回滚

只回滚本 plan 的 4 个源码迁移提交与本次 docs 提交；不涉及数据库、配置、CI 阈值或
另外两个 plan 的文件。
