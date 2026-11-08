# Message Flow 契约测试职责拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 4936 行契约测试拆成职责明确的套件内部模块，保持测试入口与所有行为不变。

**Architecture:** 保持 integration target `contract_message_flow`。主题与 support 文件均挂载为该测试根的私有直接子模块，跨主题 helper 仅 `pub(super)`，不改共享支持 crate。

**Tech Stack:** Rust workspace、Cargo/nextest、Tokio、Python 3（仅验证清单）；沿用 crate 现有依赖。

**Spec:** [共用 spec](spec.md)，重点为第 3、4、7、8 节。

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

本节的 `T` 是 `apps/bcs/crates/services/bcs-message-flow/tests`；表内路径均以 `T/` 开头，
命令与后续 Tasks 使用仓库根路径。除原入口外只允许创建 `T/contract_message_flow/` 内文件。

| 动作 / 路径 | 内容 / 预计行数（含接线留量） |
| --- | --- |
| Modify `contract_message_flow.rs` | 保留原公共 import、共享 support/session 挂载与主题声明；约 100 行 |
| Create `contract_message_flow/session_history.rs` | 10 个 Session history 测试、`FallbackParamHistoryBotRequest`；约 560 行 |
| Create `contract_message_flow/group_history.rs` | 7 个 Group history 测试、Recording/NewerOnly/Target history fake；约 620 行 |
| Create `contract_message_flow/web_send_routing.rs` | 7 个路由/拦截/DM/失败投递测试；约 450 行 |
| Create `contract_message_flow/web_send_projection.rs` | 8 个投影/上下文/文本持久化测试；约 630 行 |
| Create `contract_message_flow/web_send_provider.rs` | 7 个 Provider 测试、`RecordingSystemMessage`、`install_provider_driver_group`；约 480 行 |
| Create `contract_message_flow/web_send_sessions.rs` | 4 个 V2/V3/Provider Session 测试；约 350 行 |
| Create `contract_message_flow/group_chat.rs` | 7 个 Group chat 输入/发送者校验测试；约 340 行 |
| Create `contract_message_flow/persistent_send.rs` | 5 个持久发送测试、`EnabledEventFactory`；约 330 行 |
| Create `contract_message_flow/bot_event_callback.rs` | 6 个 Bot event/callback 测试、`request_id_for_method`；约 300 行 |
| Create `contract_message_flow/abort.rs` | 8 个精确 scope/原始 downstream abort 测试；约 460 行 |
| Create `contract_message_flow/attachments.rs` | 4 个附件测试、`image_attachment`/`file_attachment`/`human_web_send`；约 210 行 |
| Create `contract_message_flow/human_mentions.rs` | 11 个通知测试、`web_send_human_mentions`/present Human helper；约 430 行 |
| Create `contract_message_flow/support/authority.rs` | `staff_authority`、`with_hook`、`DRIVER_OWNER_1`；约 40 行 |
| Create `contract_message_flow/support/group_setup.rs` | 跨主题 `make_human_bot_dm`；约 45 行 |
| Create `contract_message_flow/support/history.rs` | 跨 Session/Group 的 `ControllableHistoryBotRequest`；约 55 行 |
| Create `contract_message_flow/support/message_repo.rs` | `RecordingMessageRepo` 及其 impl；约 100 行 |
| Create `contract_message_flow/support/policy.rs` | 跨 routing/mentions 的 `BlockingInterceptor`；约 25 行 |
| Create `contract_message_flow/support/runs.rs` | 两个 active-run registration helper；约 70 行 |

预计值用于阻止把主题搬成另一个大文件，最终按真实物理行数验收。
原 `../../../test-support/message_flow_contract_support.rs` 和 `tests/support/session.rs`
在原入口中继续挂载，**不修改内容、不移入新 support，也不改变相对路径**。

## 内部接口与可见性

- 每个主题文件 `use super::*;`，保留原测试依赖，不重新构造另一种 FlowTestSupport。
- support 的逻辑模块直接位于 integration crate 根，物理目录不形成额外父模块。
  跨模块类型、被读取的记录字段、固有方法改为 `pub(super)`；仅文件内部使用的保持私有。
- `authority` 产生 `staff_authority(&[(&str, &str)]) -> Arc<LazySeedAuthorityHook>`、
  `with_hook(BcsMessageFlow, &[(&str, &str)]) -> BcsMessageFlow` 和原常量；无额外 I/O。
- `group_setup` 产生 `async fn make_human_bot_dm(&support::FlowTestSupport)`，保留原 upsert。
- `history` 产生原 `ControllableHistoryBotRequest` 及
  `with_delays(HashMap<String, u64>, HashMap<String, Result<serde_json::Value, String>>) -> Self`；
  测试直接构造/检查的原字段才开放 `pub(super)`，sleep/response 行为不变。
- `message_repo` 保留原 `MessageRepoPort` impl，产生
  `async fn appended(&self) -> Vec<bcs_domain::NewMessage>` 与
  `async fn events(&self) -> Vec<bcs_service_api::port::repo::AppendEventRecord>`。
- `runs` 产生原 `register_active_run` 与 `register_active_run_with_downstream`，参数、
  `Arc<MemoryBotRunContextStore>`、`BotRunTransportOwner` 和写入顺序逐字保留。
- 单主题 fake 留在主题内私有：不把 `FallbackParamHistoryBotRequest`、
  `RecordingSystemMessage` 或附件 builder 暴露给其他主题。

## 测试迁移清单（完整静态归属）

每个条目连同其测试属性、注释、完整函数体原样移动；本表用于定位，不替代实际发现清单。

| 原函数名 | 目标模块（相对本 plan 的主题目录） | 原起始行 |
| --- | --- | ---: |
| `session_history_allows_human_who_is_only_session_participant` | `session_history` | 381 |
| `session_history_allows_human_whose_owned_bot_is_session_participant` | `session_history` | 437 |
| `session_history_denies_human_with_no_session_or_group_stake` | `session_history` | 503 |
| `session_history_rejects_public_caller` | `session_history` | 554 |
| `session_history_denies_group_owner_when_not_in_session_and_owns_no_session_bot` | `session_history` | 587 |
| `session_history_denies_group_participant_bot_not_in_session` | `session_history` | 630 |
| `session_history_denies_non_participant_bot` | `session_history` | 665 |
| `session_history_resolves_from_prefix_using_session_participants` | `session_history` | 700 |
| `session_history_human_view_prefers_session_bot_before_group_lead` | `session_history` | 763 |
| `session_history_v3_falls_back_to_session_key_when_explicit_session_request_errors` | `session_history` | 832 |
| `group_history_falls_back_to_driver_and_normalizes_bot_history` | `group_history` | 868 |
| `history_requests_provider_target_without_ws_connection` | `group_history` | 954 |
| `group_history_falls_back_to_store_when_bot_window_has_no_older_messages` | `group_history` | 1025 |
| `group_history_expands_legacy_unbounded_limit_for_bot_request` | `group_history` | 1098 |
| `web_send_resets_message_count_routes_and_delivers` | `web_send_routing` | 1133 |
| `web_send_persists_human_only_audience_for_manager_worker` | `web_send_projection` | 1192 |
| `accepted_chat_send_records_run_context_for_callback` | `web_send_projection` | 1291 |
| `web_send_delivers_to_registered_provider_target_without_ws_connection` | `web_send_provider` | 1375 |
| `failed_provider_web_send_uses_unified_system_message` | `web_send_provider` | 1449 |
| `deprecated_provider_stream_gray_match_keeps_provider_delivery_target` | `web_send_provider` | 1499 |
| `deprecated_provider_stream_gray_miss_keeps_provider_delivery_target` | `web_send_provider` | 1542 |
| `deprecated_provider_stream_gray_disabled_keeps_provider_delivery_target` | `web_send_provider` | 1585 |
| `deprecated_provider_stream_gray_does_not_change_inject_delivery_target` | `web_send_provider` | 1628 |
| `provider_send_context_captures_bypass_headers_for_abort_routing` | `web_send_provider` | 1741 |
| `web_send_explicit_mentions_do_not_inject_manager_worker_workers` | `web_send_routing` | 1798 |
| `web_send_in_human_bot_dm_uses_dm_routing_and_keeps_frontend_echo` | `web_send_routing` | 1883 |
| `web_send_in_human_bot_dm_omits_group_context_by_default` | `web_send_projection` | 1938 |
| `opted_in_channel_sender_identity_is_projected_to_bot_frame` | `web_send_projection` | 2000 |
| `web_send_blocking_interceptor_prevents_bot_delivery` | `web_send_routing` | 2064 |
| `web_send_delivery_frame_contains_recipient_group_context` | `web_send_projection` | 2113 |
| `web_send_with_session_id_routes_v2_by_substituting_wire_group_id` | `web_send_sessions` | 2174 |
| `web_send_with_legacy_session_id_routes_v2_with_group_wire_id` | `web_send_sessions` | 2247 |
| `web_send_with_session_id_routes_v3_with_explicit_bcs_session_id` | `web_send_sessions` | 2305 |
| `web_send_to_provider_with_session_id_uses_explicit_bcs_session_id` | `web_send_sessions` | 2364 |
| `web_send_direct_bot_projection_hides_bcs_group_context` | `web_send_projection` | 2482 |
| `web_send_prefers_human_from_name_in_delivered_frame` | `web_send_projection` | 2562 |
| `web_send_inject_delivery_uses_event_frame` | `web_send_routing` | 2627 |
| `web_send_delivers_to_private_group_targets` | `web_send_routing` | 2681 |
| `web_send_partial_delivery_failure_is_represented_in_outcome` | `web_send_routing` | 2740 |
| `group_chat_validates_sender_and_returns_legacy_delivery_projection` | `group_chat` | 2788 |
| `group_chat_uses_session_participants_for_human_sender_validation` | `group_chat` | 2839 |
| `group_chat_rejects_bot_that_is_not_a_session_participant` | `group_chat` | 2890 |
| `group_chat_rejects_session_from_another_group` | `group_chat` | 2929 |
| `group_chat_rejects_session_without_participants` | `group_chat` | 2970 |
| `group_chat_treats_requested_sender_id_literally_without_trimming` | `group_chat` | 3007 |
| `group_chat_uses_human_staff_number_as_display_name_when_speaking_as_owned_bot` | `group_chat` | 3044 |
| `persistent_group_send_routes_stores_and_returns_legacy_fields` | `persistent_send` | 3093 |
| `persistent_group_send_resolves_running_session_and_persists_message_event` | `persistent_send` | 3150 |
| `persistent_group_send_delivers_to_registered_provider_target_without_ws_connection` | `persistent_send` | 3216 |
| `persistent_group_send_in_human_bot_dm_uses_dm_routing` | `persistent_send` | 3280 |
| `persistent_group_send_marks_group_inactive_when_legacy_message_cap_is_reached` | `persistent_send` | 3325 |
| `bot_final_event_routes_and_publishes_frontend_through_message_flow` | `bot_event_callback` | 3364 |
| `bot_final_event_relay_to_provider_uses_explicit_bcs_session_id` | `bot_event_callback` | 3436 |
| `group_callback_command_routes_and_publishes_frontend_through_message_flow` | `bot_event_callback` | 3488 |
| `group_callback_stops_before_delivery_when_persistence_fails` | `bot_event_callback` | 3529 |
| `group_callback_continues_when_frontend_publish_fails` | `bot_event_callback` | 3565 |
| `group_callback_all_mentions_use_routing_service` | `bot_event_callback` | 3597 |
| `chat_abort_delivers_abort_frame_to_bot_participants` | `abort` | 3624 |
| `chat_abort_reuses_the_session_key_from_the_original_plugin_delivery` | `abort` | 3668 |
| `chat_abort_publishes_frontend_event_through_port` | `abort` | 3726 |
| `chat_abort_with_session_rejects_a_run_from_another_session` | `abort` | 3769 |
| `chat_abort_with_session_accepts_a_run_from_the_same_session` | `abort` | 3811 |
| `chat_abort_without_run_id_uses_only_the_bound_session_key` | `abort` | 3852 |
| `chat_abort_provider_sends_one_scope_request_for_parallel_runs` | `abort` | 3899 |
| `chat_abort_provider_rejects_ids_outside_the_requested_scope` | `abort` | 3988 |
| `group_history_prefers_high_priority_even_when_low_priority_finishes_first` | `group_history` | 4103 |
| `group_history_falls_back_to_driver_when_view_bot_fails` | `group_history` | 4163 |
| `group_history_falls_back_to_store_when_all_bots_fail` | `group_history` | 4219 |
| `web_send_event_echoes_attachments_verbatim` | `attachments` | 4333 |
| `temporary_file_attachment_is_sent_only_to_active_chat_send` | `attachments` | 4369 |
| `web_send_event_without_attachments_has_no_attachments_key` | `attachments` | 4415 |
| `web_send_event_omits_attachments_key_for_empty_attachment_list` | `attachments` | 4439 |
| `web_send_persists_raw_mention_text_while_bots_receive_cleaned_text` | `web_send_projection` | 4463 |
| `web_send_notifies_explicitly_mentioned_human` | `human_mentions` | 4586 |
| `web_send_skips_notification_for_self_mention` | `human_mentions` | 4626 |
| `web_send_skips_notification_for_bot_only_mentions` | `human_mentions` | 4647 |
| `web_send_skips_notification_when_port_unavailable` | `human_mentions` | 4668 |
| `web_send_text_mention_notifies_resolved_human` | `human_mentions` | 4705 |
| `web_send_at_all_alone_does_not_notify` | `human_mentions` | 4731 |
| `web_send_at_all_with_explicit_human_notifies_that_human` | `human_mentions` | 4754 |
| `group_callback_never_triggers_human_notification` | `human_mentions` | 4779 |
| `web_send_dm_group_does_not_notify_mentioned_human` | `human_mentions` | 4825 |
| `web_send_policy_blocked_message_does_not_notify_humans` | `human_mentions` | 4865 |
| `persistent_group_send_text_mention_notifies_human` | `human_mentions` | 4895 |

静态分组核对：`session_history` 10 个；`group_history` 7 个；`web_send_routing` 7 个；`web_send_projection` 8 个；`web_send_provider` 7 个；`web_send_sessions` 4 个；`group_chat` 7 个；`persistent_send` 5 个；`bot_event_callback` 6 个；`abort` 8 个；`attachments` 4 个；`human_mentions` 11 个。

## 执行前基线（并入 Task 1，不单独交付脚手架）

- [x] 确认当前工作区干净、工具存在；读取上节指令和实际源码。

```bash
set -euo pipefail
export SOURCE_BASE="$(git rev-parse HEAD)"
export EVIDENCE="$(mktemp -d /tmp/avernet-split-p1.XXXXXX)"
printf '%s\n' "$SOURCE_BASE" > "$EVIDENCE/source-base.sha"
git show "$SOURCE_BASE:apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs" > "$EVIDENCE/source.rs"
cargo --version > "$EVIDENCE/toolchain.txt"
cargo nextest --version >> "$EVIDENCE/toolchain.txt"
cargo metadata --manifest-path apps/bcs/Cargo.toml --no-deps --offline --locked \
  --format-version 1 > "$EVIDENCE/metadata.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow \
  --message-format json > "$EVIDENCE/package.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow \
  --message-format json > "$EVIDENCE/target.before.json"
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow \
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
p = Path('apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs')
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
  apps/bcs/crates/services/bcs-message-flow apps/bcs/scripts apps/bcs/.config \
  > "$EVIDENCE/path-audit.txt" || test "$?" -eq 1
```

## Tasks

### Task 1：历史查询与共享 helper 的模块化

**Files:** Modify `apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs`；
Create 上表 `session_history.rs`、`group_history.rs` 与六个 `support/*.rs`。
**Test:** 原 `contract_message_flow` target。
**Interfaces:** Consumes 原 `FlowTestSupport` 与 Session support；Produces 本 plan“内部接口”
列出的跨主题 helper，后续主题从根模块导入，不相互调用测试。

- [x] 完成“执行前基线”，核实静态 84 条归属与实际发现；保留源文件快照。
- [x] 将六组 support 完整定义从原文件移入表内路径，保留 trait impl、derive、注释与函数体；
  原入口只留下下面的挂载/导入，不留下重复实现。

```rust
#[path = "contract_message_flow/support/authority.rs"]
mod authority;
#[path = "contract_message_flow/support/group_setup.rs"]
mod group_setup;
#[path = "contract_message_flow/support/history.rs"]
mod history_support;
#[path = "contract_message_flow/support/message_repo.rs"]
mod message_repo;
#[path = "contract_message_flow/support/policy.rs"]
mod policy;
#[path = "contract_message_flow/support/runs.rs"]
mod runs;
use authority::{DRIVER_OWNER_1, staff_authority, with_hook};
use group_setup::make_human_bot_dm;
use history_support::ControllableHistoryBotRequest;
use message_repo::RecordingMessageRepo;
use policy::BlockingInterceptor;
use runs::{register_active_run, register_active_run_with_downstream};
```

support 文件使用 `use super::*;` 获得原依赖；原入口已有的 `support` / `session_support`
声明保持不变。先迁移 support 后运行一次原 target，确认可见性调整未改变行为。

- [x] 按归属表移入 17 个历史测试及各自局部 fake，主题文件首行 `use super::*;`，挂载：

```rust
#[path = "contract_message_flow/session_history.rs"]
mod session_history;
#[path = "contract_message_flow/group_history.rs"]
mod group_history;
```

- [x] 执行以下命令，预期原 target 全部未忽略测试通过；两个历史模块没有丢失场景。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow
```

- [x] 检查新文件不超过 1000 行、`git diff --check` 无错误；仅暂存本任务路径并提交：
  `refactor(bcs): isolate message-flow history contract tests`。不推送中间超大根文件。

### Task 2：Web send 与 Group chat 主题化

**Files:** Modify 原入口；Create 上表四个 `web_send_*.rs`、`group_chat.rs`。
**Interfaces:** Consumes Task 1 helper；Produces 原样 MessageFlowService 测试，公开接口不变。

- [x] 对照归属表把 33 个测试及 Provider 局部 fake/helper 连同属性/函数体迁移，
  尤其保留 accepted Run context、Human-only audience、gray-list 与 bypass headers。
- [x] 根入口加入以下接线；各新文件使用 `use super::*;`。只在自身文件内使用的 helper 私有。

```rust
#[path = "contract_message_flow/web_send_routing.rs"]
mod web_send_routing;
#[path = "contract_message_flow/web_send_projection.rs"]
mod web_send_projection;
#[path = "contract_message_flow/web_send_provider.rs"]
mod web_send_provider;
#[path = "contract_message_flow/web_send_sessions.rs"]
mod web_send_sessions;
#[path = "contract_message_flow/group_chat.rs"]
mod group_chat;
```

- [x] 执行原 target 全量测试，并核实脚本保留的过滤方式仍能发现对应测试。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow provider -- --list
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow accepted_chat_send_records_run_context_for_callback -- --list
```

`apps/bcs/scripts/test_provider_downlink.sh:178-182` 使用上述过滤，函数名保持不变，
不修改该脚本、不额外运行依赖 live stack 的脚本作为近端单测。

- [x] 核实每个新文件不超过 1000 行，检查最小差异后提交：
  `refactor(bcs): separate message-flow send contract scenarios`。

### Task 3：持久化、回调与取消主题化

**Files:** Modify 原入口；Create `persistent_send.rs`、`bot_event_callback.rs`、`abort.rs`。
**Interfaces:** Consumes `RecordingMessageRepo` / runs；Produces 原 trait 观察结果，不新增 store 调用。

- [x] 迁移归属表 19 个测试，`EnabledEventFactory` 只进入 `persistent_send.rs`，
  `request_id_for_method(&[BcsFrame], &str) -> String` 只进入 `bot_event_callback.rs`。
- [x] 删除根中的原定义，新增以下接线，三个主题各以 `use super::*;` 开头。

```rust
#[path = "contract_message_flow/persistent_send.rs"]
mod persistent_send;
#[path = "contract_message_flow/bot_event_callback.rs"]
mod bot_event_callback;
#[path = "contract_message_flow/abort.rs"]
mod abort;
```

- [x] 运行原 target 全量测试，预期持久化失败阻断投递、前端失败继续回调、scope 拒绝及
  原 downstream Session/Run identity 的既有测试原样通过。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow
```

- [x] 核实新文件大小、属性及原 body 差异后提交：
  `refactor(bcs): isolate persistence callback and abort contract tests`。

### Task 4：附件/通知迁移与独立验收

**Files:** Modify 原入口；Create `attachments.rs`、`human_mentions.rs`；最终只调整本套件必要 import。
**Interfaces:** Consumes Task 1 `BlockingInterceptor` / DM setup；Produces 完整一一映射的 84 个原测试。

- [x] 迁移归属表 15 个测试及各自私有 builder；将原 mention 文本持久化测试留在
  `web_send_projection`，不复制到通知模块；根入口加入：

```rust
#[path = "contract_message_flow/attachments.rs"]
mod attachments;
#[path = "contract_message_flow/human_mentions.rs"]
mod human_mentions;
```

- [x] 确认根入口无残留测试体、单主题 fake 或重复定义。执行下方清单检查和全量/大小检查；
  映射应覆盖 84 个迁移测试，所属 crate 其他测试身份不变。
- [x] 检查目录中每个 `.rs` 的大小并审阅迁移差异，确认没有 body/断言语义变化；提交：
  `refactor(bcs): complete message-flow contract test decomposition`。
- [ ] 在最终源码提交上执行下方架构、覆盖率和统一 Singlebox gate；完成交付记录。

## 末任务使用的清单一致性检查（可直接执行）

每个迁移任务后先运行目标测试；全部迁移完成后，执行以下完整检查。它保留未迁移
测试的完整名称，只对源文件中的迁移测试允许模块前缀变化；比较 binary ID、测试
集合及 ignored 状态，并生成逐测试映射，重复挂载会导致失败。

```bash
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow \
  --message-format json > "$EVIDENCE/package.after.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --test contract_message_flow \
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
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow
cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow --all-targets
git add -N -- apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow
git diff --check
# 每次对本 plan 的执行基线审查迁移差异；不以格式化后的 diff 替代。
git diff "$SOURCE_BASE" --color-moved=blocks -- apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow
python3 - <<'CHECK'
from pathlib import Path
files = set()
for name in ['apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs', 'apps/bcs/crates/services/bcs-message-flow/tests/contract_message_flow']:
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

- [ ] 基于最新实际合并目标计算范围，然后执行架构与单元覆盖率 gate。

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

- [ ] 执行统一 Singlebox 与报告验证；不调用旧独立 E2E gate 替代。

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

编写阶段只做源码归属、命令接口和文档自检，没有执行迁移、Rust 测试、覆盖率或
Singlebox；上述预期结果不是编写阶段的实测结果。实施与验收的实际值见下节
“交付记录”，未通过或未验证的门禁保持未勾选并逐条说明。

## 交付记录

### 执行范围与提交

| 阶段 | 提交 SHA |
| --- | --- |
| 执行基线 `SOURCE_BASE` | `5b6952f7eb07f0a25df5288274247735840dd471` |
| Task 1 `refactor(bcs): isolate message-flow history contract tests` | `9f8c2c315ab8b137f54359a7861aeea552e1853d` |
| Task 2 `refactor(bcs): separate message-flow send contract scenarios` | `8f51ff98633ee7081efa637b57e61851c439878d` |
| Task 3 `refactor(bcs): isolate persistence callback and abort contract tests` | `ae530e0f58295363ebfdd637506eac7b24eb5a1b` |
| Task 4 `refactor(bcs): complete message-flow contract test decomposition`（验收 HEAD） | `1af0d1326c794646185b11e45be2aa3b304f1713` |

门禁在 `1af0d1326c` 上执行；本文档记录单独提交为
`docs(bcs): record plan-1 contract test decomposition delivery`，位于源码提交之后。
未推送任何提交。验收合并基线由计划脚本计算：`origin/dev` →（fetch 时刻记录）
`520be5ed6e695b9c379e99c007730e37df8ae95e`，`RANGE_BASE =
41f728aaada7684543da36c0807e7b945314ebc2`；`git merge-base origin/dev HEAD` 至今仍
解析到 `41f728aa…`，因此下文记录的 RANGE_BASE 口径与数值保持有效。

### 最终文件行数（物理行）

| 文件 | 行数 |
| --- | ---: |
| `tests/contract_message_flow.rs` | 74 |
| `contract_message_flow/session_history.rs` | 521 |
| `contract_message_flow/group_history.rs` | 529 |
| `contract_message_flow/web_send_routing.rs` | 410 |
| `contract_message_flow/web_send_projection.rs` | 588 |
| `contract_message_flow/web_send_provider.rs` | 443 |
| `contract_message_flow/web_send_sessions.rs` | 309 |
| `contract_message_flow/group_chat.rs` | 306 |
| `contract_message_flow/persistent_send.rs` | 291 |
| `contract_message_flow/bot_event_callback.rs` | 271 |
| `contract_message_flow/abort.rs` | 423 |
| `contract_message_flow/attachments.rs` | 187 |
| `contract_message_flow/human_mentions.rs` | 403 |
| `contract_message_flow/support/authority.rs` | 22 |
| `contract_message_flow/support/group_setup.rs` | 31 |
| `contract_message_flow/support/history.rs` | 38 |
| `contract_message_flow/support/message_repo.rs` | 81 |
| `contract_message_flow/support/policy.rs` | 15 |
| `contract_message_flow/support/runs.rs` | 58 |

大小门禁：19 个文件全部 ≤1000 行（最大 588 行，原入口 4936 → 74 行）。根入口只保留
原公共 import、共享 `support`/`session_support` 挂载、12 个主题与 6 个 support 的
`#[path]` 接线及 6 条 `use`，无测试体、无单主题 fake、无重复定义。根 import 经
`cargo check --all-targets` 验证后未删除任何一条（子模块通过 `use super::*` 使用，
未出现新增 unused-import 警告）。

### 84 个测试映射

- `cargo nextest list` 前后清单（同一工具链、同一 features）：
  `$EVIDENCE/package.before.json` / `package.after.json`、
  `$EVIDENCE/target.before.json` / `target.after.json`（`$EVIDENCE` =
  `/private/var/folders/5s/v4sf5jfn18qfxvjvtdbbcnnw0000gn/T/opencode/avernet-split-p1`，
  本地临时证据，不随仓库交付）。
- 计划的清单一致性检查输出：`PASS: full package discovery stable; mapped 84 moved
  tests`；crate 其余测试完整名称与 `ignored` 状态不变，1 个 target suite 未变。
- 逐测试映射写入 `$EVIDENCE/test-map.tsv`；与“测试迁移清单”逐行核对：84 行，
  **0 处模块前缀不符**。各模块数量：`session_history` 10、`group_history` 7、
  `web_send_routing` 7、`web_send_projection` 8、`web_send_provider` 7、
  `web_send_sessions` 4、`group_chat` 7、`persistent_send` 5、
  `bot_event_callback` 6、`abort` 8、`attachments` 4、`human_mentions` 11（合计 84）。
- 迁移保真：从 `$EVIDENCE/source.rs` 抽取的 84 个测试块（属性行至函数结尾）与拆分后
  文件中的 84 个块逐字节相同；无重复函数名。84 个测试全部为 `#[tokio::test]`，
  源文件不存在 `ignore`/`cfg`/`timeout` 等附加属性，`ignored` 前后比对一致。
- `attachments.rs` 与 `human_mentions.rs` 的整段内容与基线同区域逐字节相同（仅去掉
  文件末尾用于分隔的空行，保证 EOF 只有一个换行）；附件段落注释
  `// ==== Workbench realtime attachment echo ====` 随主题迁入 `attachments.rs`。
- `web_send_persists_raw_mention_text_while_bots_receive_cleaned_text` 只存在于
  `web_send_projection`，未复制到 `human_mentions`。
- 测试 ID 兼容性：拆分后完整测试 ID 带模块前缀（如 `abort::…`、`web_send_provider::…`）；
  仓库内消费者已审计且安全（`apps/bcs/scripts/test_provider_downlink.sh` 的子串过滤已
  重新验证），但外部按完整扁平测试 ID 保存的过滤/报告需要补上新前缀。

### 验证命令与实际结果

| 命令 | 实际结果 |
| --- | --- |
| `cargo test -p bcs-message-flow --test contract_message_flow` | 84 passed / 0 failed / 0 ignored，exit 0（`task4-target.log`） |
| `cargo nextest list`（package + target）+ 计划清单脚本 | exit 0；`mapped 84 moved tests`（`task4-inventory.log`） |
| `git diff --check`（含新文件 intent-to-add 后） | 无输出、exit 0；`git show --check` 亦无空白错误（`task4` 提交） |
| 目录大小断言（计划 python 块） | 19 个文件全部 ≤1000，exit 0（`task4-size-gate.log`） |
| `cargo test -p bcs-message-flow` | 991 passed / 0 failed / 0 ignored，exit 0（`task4-crate-test.log`） |
| `cargo check -p bcs-message-flow --all-targets` | exit 0；11 条 warning 全部来自 `bcs-bot-store`、`caller_dispatch_stage_timeout_contract`、`caller_owner_relay_contract`、`delivery_runtime` 等既有 target，`contract_message_flow` 0 条（`task4-check.log`） |
| `git diff --name-only "$SOURCE_BASE"`（在源码提交 `1af0d1326c` 上） | 19 个路径，全部为本 plan 范围（测试入口 + `tests/contract_message_flow/` 内部），无其他文件（`task4-scope.txt`） |
| Task 2 过滤发现：`... --test contract_message_flow provider -- --list` | exit 0，13 条匹配（`task4-filter-provider.list`） |
| Task 2 过滤发现：`... accepted_chat_send_records_run_context_for_callback -- --list` | exit 0，1 条匹配（`task4-filter-accepted.list`） |
| `BCS_BASE_REF="$RANGE_BASE" bash apps/bcs/scripts/ci/arch-check.sh` | **exit 5（未通过）**：Total 15 / Pass 8 / Skip 2 / Fail 5，失败为 `DEP-1~8`、`LINT-1`、`LINT-4`、`TEST-1`、`R25`；SKIP 为 `DEP-8`（cargo-machete 未安装）、`PR-1`（非 PR 环境）、`API-1`（cargo-public-api 未安装）（`Skip 2` 只统计 15 个顶层 check 中退出码 77 的两项，即 `PR-1` 与 `API-1`；`DEP-8` 是 `check-deps.sh` 组内打印的 SKIP 行，该组整体按 FAIL 计数——脚本 `CHECKS` 循环与 `check-deps.sh` 的 `log SKIP 8` 逻辑如此）。全部失败项均在 merge-base `41f728aa` 上复现且失败集合相同（并行扫描导致行序不同，排序后一致；覆盖 DEP、LINT-1、LINT-4、R25.1/R25.2）；`TEST-1` 是 `check-r25-conformance.sh` 的包装。`R25.3` 另有既有脚本缺陷：`set -o pipefail` 下 `echo "$listed" \| rg -q` 因 echo 收到 SIGPIPE 返回 141，实测手动执行同一命令可匹配 27 条、`pipefail` 下流水线状态为 141。日志中 **0 处**提及本 plan 路径（`task4-arch-check-full.log`、`task4-r25-rerun.log`、`base/head-*` 对比日志） |
| `bash apps/bcs/scripts/ci_test.sh --base "$RANGE_BASE" --head HEAD --fast-fail --coverage` | **exit 100（未通过）**：2745 passed / 2 failed / 65 skipped，fast-fail 停在 2747/9250；2 个失败均在 `bcs-bot-store::conformance_bot_control_plane_repo`（与本 plan 无关，`task4-ci-test.log`） |
| 上述 2 个失败用例的基线复核（HEAD 与 merge-base） | 两处均为 18 tests / 16 passed / 2 failed，失败用例相同（`task4-control-plane-head.log`、`task4-control-plane-base.log`）→ 既有失败，非本 plan 引入 |
| `bash apps/bcs/scripts/ci_test.sh --base "$RANGE_BASE" --head HEAD --coverage`（非 fast-fail，用于产出完整报告） | exit 0（脚本在非 fast-fail 模式不阻塞）；9250 tests / 9248 passed / 2 failed / 65 skipped；`apps/bcs/testresult/junit.xml` 生成（`task4-ci-test-runall.log`） |
| `python3 scripts/cov_gate.py --base-ref "$RANGE_BASE" ... --pass-rate-min 100 --overall-line-min 87 --changed-line-min 85 --changed-line-min-lines 51` | **exit 1**：`[pass-rate] tests=9250 passed=9248 ... 99.98%` → 未达 100%（2 个既有失败）；`[overall-line-cov] 87.29% > 87%` OK；`[changed-line-cov] no instrumentable changed source lines mapped` → N/A（`task4-cov-gate.log`）。`cargo llvm-cov` 未在测试非零退出时写 cobertura，故用 `cargo llvm-cov report --cobertura` 从同一批 profraw 生成 `apps/bcs/testresult/cobertura.xml`（`task4-llvmcov-report.log`） |
| `BCS_E2E_CHANGED_BASE_REF="$RANGE_BASE" SINGLEBOX_BASE_REF="$RANGE_BASE" bash singlebox/ci/singlebox_coverage.sh`（第 1 次） | **exit 1**：8000 端口被 checkout 外进程占用（`/Users/zony/teamclaw-coding/fe/frontend-nextgen`），脚本拒绝终止外部进程，frontend start 失败（`task4-singlebox.log`） |
| 同上，加 `FRONTEND_PORT=8021`（第 2 次，完整运行） | **exit 1（未通过）**：栈启动成功并执行完整验收；BCS E2E 全部故事通过且门禁 PASS：runtime line 56.61%（≥54）、method 50.25%（≥48）、Router API 173/173（100%）、CLI leaf command 66/66（100%）、E2E changed-line 为 N/A（19 个 changed test-only 文件不计入）。Backend acceptance 61 passed / 5 failed（`expert_chat` 1、`files` 2、`harness` 2），失败现场为 live personal bot 创建返回 `BaaS publish FAILED`（`reports/acceptance.log` 记录 5 次，`task4-singlebox-retry.log` 同样可查）；确切原因未保留在工件中（BaaS 日志已被后续运行清理），交互式观察到的现场为 BaaS 设备端口池被其它 worktree 的孤儿 engine adapter 占用，作为现场记录而非工件证据；因此 `expert_chat`、`files`、`harness` 模块覆盖率阈值不达标，`summary.status=failed`（`task4-singlebox-retry.log`、`reports/acceptance.log`、`reports/summary.json`、`reports/bcs/e2e.log`） |
| `python3 singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports` | **exit 1**：`summary.status expected 'passed', got 'failed'`；`acceptance JUnit has failures/errors: failures=5, errors=0`（`task4-artifact-verifier.log`） |

报告位置：`apps/bcs/testresult/`（junit/cobertura）、
`singlebox/.dependencies/coverage/singlebox/reports/`（`summary.json`、`summary.md`、
`dashboard.html`、`acceptance.log`、`acceptance-junit.xml`、`backend-*`、`baas-*`、
`bcs/e2e.log`、`bcs/summary.json`、`bcs/cobertura.xml`、`bcs/coverage.txt`、
`bcs/endpoint_coverage.*`、`bcs/cli_command_coverage.txt`、`html/`）。
所有运行日志另存于 `$EVIDENCE`（见“偏差”）。

### 未通过 / 未验证项与原因

- **架构门禁未通过（既有问题，非本 plan 引入）**：`DEP-1~8`、`LINT-1`、`LINT-4`、
  `TEST-1`、`R25` 在 merge-base 上失败集合相同（并行扫描只影响行序，排序后一致）；
  日志中没有任何一条指向本 plan 的路径。`R25.3` 是脚本 `pipefail` + `rg -q` 的既有缺陷，
  已单独复现。`DEP-8`、`API-1` 因本机缺 `cargo-machete`、`cargo-public-api` 而 SKIP；
  `PR-1` 因非 PR 环境 SKIP。上述 SKIP 不记为通过（`Skip 2` 只统计顶层 check，
  `DEP-8` 是 `check-deps.sh` 组内的 SKIP 行，详情见上文 arch-check 行）。
- **单元测试通过率门禁未通过**：2 个 `bcs-bot-store` control-plane 用例既有失败，
  在 HEAD 与 merge-base 行为一致；本 plan 未修改该 crate，也未降低阈值。
- **AC-9 统一 Singlebox 门禁未通过（环境性，未验收）**：5 个与本 plan 无关的
  Backend acceptance 故事（`expert_chat` 1、`files` 2、`harness` 2）在 live personal
  bot 创建处返回 `BaaS publish FAILED`，`summary.status=failed`；这两点由
  `reports/acceptance.log` 与 `reports/summary.json` 工件直接支持。确切原因未保留在
  工件中（BaaS 日志已被后续运行清理）；交互式观察到的现场为 BaaS 单机设备端口池
  20010–20099 被其它 worktree（`bot-avatar-backend`，2026-11-04 启动、父进程为 1）
  遗留的孤儿 engine adapter 进程占用、剩余端口不足以创建 live personal bot——该描述是
  现场记录，**不作为工件证据**。未终止这些外部进程、未缩小模块集合、未使用 opt-out
  环境变量、未改阈值。BCS E2E 与 BCS 覆盖率门禁（本 plan 影响面）已实测通过。
- **未执行**：未推送、未创建 PR；未以旧独立 E2E 入口替代统一 Singlebox。
- **推送后果（下一个人必读）**：仓库 pre-push hook 默认 lint-only，本分支会通过；
  若用 `OCB_PRE_PUSH_RUN_CI=1 git push` 运行完整 BCS 门禁，则会被上文已记录的既有失败
  拒绝（2 × `bcs-bot-store::conformance_bot_control_plane_repo` 通过率、arch-check 的
  DEP/LINT/R25 集合、singlebox `summary.status=failed`）——这些失败均已在本 plan 的
  merge-base `41f728aa` 上复现，无一由本次改动引入。

### AC 复核

- AC-1 通过（19 个文件 ≤1000 行）；AC-2 通过（无业务逻辑、无格式化、无新依赖）；
  AC-3 通过（仅新增 `#[path]` 接线，审计见 `$EVIDENCE/path-audit.txt`，
  `test_provider_downlink.sh` 依赖的两个过滤仍能发现 13 / 1 个测试）。
- AC-4 通过（84 → 84 一一映射，无重复挂载，`ignored` 不变）；AC-5 通过（84 个测试块
  逐字节相同）；AC-6 近端与 crate 全量通过（84/84、991/991）。
- AC-7 通过（`cargo check --all-targets` 成功）；AC-8 部分通过（overall line 87.29%
  > 87%；changed-line N/A；pass-rate 因既有失败未达 100%）。
- AC-9 **未通过/未验收**（见上）；AC-10 通过（未替换、未弱化任何 gate）。
- 运行时访问成本：纯测试文件迁移，未改变数据库/缓存/网络调用次数与顺序，无 schema、
  配置或迁移变更。

### 回滚

回滚 = 依次 revert 本 plan 的 4 个源码提交与本记录提交；不涉及数据库、缓存、配置或
schema 变更，不影响 plan-2 / plan-3 的文件。

### 与计划的偏差

- `EVIDENCE` 使用预先创建的
  `/private/var/folders/5s/v4sf5jfn18qfxvjvtdbbcnnw0000gn/T/opencode/avernet-split-p1`
  代替 `mktemp -d /tmp/avernet-split-p1.XXXXXX`，以便多个子任务复用同一证据目录
  （见 `$EVIDENCE/deviation-notes.txt`）。
- Singlebox 需以 `FRONTEND_PORT=8021` 运行，因为默认 8000 端口被 checkout 外进程占用
  （脚本自身提示“Stop it manually or choose another port”）；除端口外未改动任何运行
  参数或门禁。
- `ci_test.sh --fast-fail --coverage` 因既有失败中断；为取得完整覆盖率报告，另以
  非 fast-fail 的同一脚本（CI 默认模式）跑完整套件，并用 `cargo llvm-cov report`
  生成 cobertura。两者均如实记录，未把非 fast-fail 结果当作 fast-fail 通过。
- Singlebox 运行中的 `uv sync` 会重写 `apps/bcsfuse/uv.lock`（61 行增删，仅依赖解析
  顺序/元数据抖动）；该本地状态改动已用 `git checkout` 还原，未纳入任何提交。
