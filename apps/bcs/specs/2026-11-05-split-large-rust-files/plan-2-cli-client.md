# CLI Client 职责拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 4864 行 BcsClient 按资源/API 职责拆分，保持库导出、调用签名与请求行为，所有拆出文件 ≤1000 行。

**Architecture:** `src/client.rs` 保留状态、类型与共用请求处理；私有子模块用 `impl BcsClient` 承接方法。测试作为所属功能的后代访问私有 helper，不增加生产可见性或新公共接口，保留现有 drafts 接线。

**Tech Stack:** Rust workspace、Cargo/nextest、Tokio、Python 3（仅验证清单）；沿用 crate 现有依赖。

**Spec:** [共用 spec](spec.md)，重点为第 3、5、7、8 节。

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

## 文件结构、方法归属与消费者

本节的 `S` 是 `apps/bcs/crates/tools/bcs-cli/src`；表内路径均相对 `S/`。
修改范围仅 `S/client.rs`、新增 `S/client/`，以及末任务的 client 行数豁免条目。
`S/lib.rs` 原导出、`S/draft_client.rs` 和所有消费者源码均保持不变。

| 动作 / 路径 | 职责 / 预计行数（含注释与 import 留量） |
| --- | --- |
| Modify `client.rs` | 状态/类型/构造、共用 headers/response；约 500 行，现有 drafts 挂载保留 |
| Create `client/bots.rs` | 13 个 Bot 方法；约 580 行 |
| Create `client/chat.rs` | 13 个 Chat 方法及私有 helper；约 420 行 |
| Create `client/collaboration.rs` | 9 个定义/执行/custom Group 方法；约 270 行 |
| Create `client/groups.rs` | 12 个 Group 管理方法；约 410 行 |
| Create `client/relations.rs` | 7 个 friend/visibility 方法；约 210 行 |
| Create `client/sessions.rs` | 11 个 Session 方法；约 470 行 |
| Create `client/channels.rs` | 4 个 Channel 方法；约 145 行 |
| Create `client/services.rs` | 2 个 Service 方法；约 130 行 |
| Create `client/files.rs` | 13 个文件/编码 helper；约 470 行 |
| Create `client/fusion.rs` | 2 个 Fusion 方法；约 80 行 |
| Create `client/files/tests.rs` | 11 个文件/MIME/charset/跨站认证测试；约 350 行 |
| Create `client/bots/tests.rs` | 1 个 discover organization query 测试；约 70 行 |
| Create `client/sessions/tests.rs` | 1 个 Group context delivery 测试；约 50 行 |
| Create `client/channels/tests.rs` | 1 个 conversation query 编码测试；约 45 行 |
| Create `client/chat/tests.rs` | 测试子模块接线与 `queued_submit`/`queued_status`；约 45 行 |
| Create `client/chat/tests/submission.rs` | 9 个提交/payload/反序列化/cancel/outcome 测试；约 220 行 |
| Create `client/chat/tests/polling.rs` | 9 个轮询/预算/durable identity 测试；约 570 行 |
| Create `client/tests.rs` | 根测试接线；约 5 行 |
| Create `client/tests/headers.rs` | 14 个构造/认证/headers 测试；约 310 行 |
| Modify `apps/bcs/docs/waivers/agentpass-subbots-client-source-size.md` | 只移除/完成 `client.rs` 活动豁免，保留其他文件和期限 |

`BcsClient`、`BotGroupListPage`、`CurrentActorGroupListPage`、`CreateCustomGroupOptions`、
`RunSessionCollaborationOptions`、`ChatRunOutcome`、`ChatAsyncError`、`DEFAULT_BCS_URL`
及 `impl ChatRunOutcome { admission_succeeded }` 留在原根模块。
`build_http_client() -> reqwest::Client` 也留在根，重定向策略/120 秒默认超时不变。

`cargo metadata --no-deps --offline --locked` 已确认直接 consumer 为 `bcs` 和 `bcs-http`
的 dev-dependency；此外编译 `bcs-admin`，不得只测 CLI lib。`bcs-admin` 当前不是上述
metadata 中的直接依赖，不把其检查误写成“直接 dependency 已验证”。

### 完整固有方法归属

从原实现块按下表方法名移动，每个方法连同 doc comment 与完整签名/body。`root` 的
23 个方法留在 `client.rs`，不为了消除隐私报错扩大字段/方法可见性。

| 原方法 | 目标（`client/` 下，root 除外） | 原行号 |
| --- | --- | ---: |
| `current_bot_uuid` | `bots.rs` | 174 |
| `join` | `bots.rs` | 497 |
| `connect` | `bots.rs` | 545 |
| `get_onboard_url` | `bots.rs` | 607 |
| `onboard` | `bots.rs` | 704 |
| `update_status_with_token` | `bots.rs` | 755 |
| `update_status` | `bots.rs` | 788 |
| `group_context_request` | `bots.rs` | 822 |
| `get_bot` | `bots.rs` | 849 |
| `list_bots` | `bots.rs` | 893 |
| `discover_bots` | `bots.rs` | 918 |
| `discover_bots_extended` | `bots.rs` | 952 |
| `query_bots` | `bots.rs` | 2237 |
| `new` | `root` | 194 |
| `with_token` | `root` | 209 |
| `with_token_and_cookie` | `root` | 224 |
| `with_token_and_oauth` | `root` | 243 |
| `with_service_key` | `root` | 265 |
| `set_service_key` | `root` | 280 |
| `set_token` | `root` | 285 |
| `set_cookie` | `root` | 290 |
| `set_client_identity` | `root` | 295 |
| `set_traceparent` | `root` | 302 |
| `token` | `root` | 309 |
| `cookie` | `root` | 314 |
| `set_oauth_headers` | `root` | 319 |
| `from_env` | `root` | 324 |
| `base_url` | `root` | 330 |
| `health_check` | `root` | 335 |
| `add_headers` | `root` | 356 |
| `add_auth` | `root` | 426 |
| `add_chat_headers` | `root` | 430 |
| `add_chat_dispatch_headers` | `root` | 435 |
| `ensure_success` | `root` | 455 |
| `ensure_success_status` | `root` | 473 |
| `set_actor_id` | `root` | 2076 |
| `normalize_tags` | `chat.rs` | 1008 |
| `chat_async` | `chat.rs` | 1025 |
| `chat_async_payload` | `chat.rs` | 1073 |
| `chat_run_status` | `chat.rs` | 1121 |
| `chat_run_cancel` | `chat.rs` | 1155 |
| `chat_poll_run` | `chat.rs` | 1185 |
| `chat_poll_run_until_running` | `chat.rs` | 1227 |
| `terminal_outcome` | `chat.rs` | 1280 |
| `running_outcome` | `chat.rs` | 1300 |
| `timeout_outcome` | `chat.rs` | 1318 |
| `poll_error_outcome` | `chat.rs` | 1342 |
| `group_chat` | `chat.rs` | 1361 |
| `admitted_outcome` | `chat.rs` | 2058 |
| `validate_collaboration_definition` | `collaboration.rs` | 1407 |
| `get_session_state_machine_permission` | `collaboration.rs` | 1436 |
| `run_session_collaboration` | `collaboration.rs` | 1464 |
| `collaboration_run_url` | `collaboration.rs` | 1500 |
| `query_collaboration_run` | `collaboration.rs` | 1511 |
| `respond_collaboration_node` | `collaboration.rs` | 1527 |
| `collaboration_response` | `collaboration.rs` | 1561 |
| `create_custom_group` | `collaboration.rs` | 1577 |
| `create_custom_group_with_initial_session` | `collaboration.rs` | 1586 |
| `create_group` | `groups.rs` | 1640 |
| `create_group_no_mode` | `groups.rs` | 1692 |
| `create_group_with_context` | `groups.rs` | 1702 |
| `create_group_with_strategy_and_context` | `groups.rs` | 1714 |
| `create_group_with_initial_session` | `groups.rs` | 1735 |
| `get_group` | `groups.rs` | 1790 |
| `list_groups` | `groups.rs` | 1812 |
| `list_bot_groups` | `groups.rs` | 1836 |
| `list_my_groups` | `groups.rs` | 1869 |
| `add_group_member` | `groups.rs` | 1903 |
| `update_group_status` | `groups.rs` | 1938 |
| `terminate_group` | `groups.rs` | 1982 |
| `fuse_context` | `fusion.rs` | 2011 |
| `fuse_context_with_focus` | `fusion.rs` | 2022 |
| `send_friend_request` | `relations.rs` | 2082 |
| `list_friend_requests` | `relations.rs` | 2121 |
| `accept_friend_request` | `relations.rs` | 2159 |
| `reject_friend_request` | `relations.rs` | 2178 |
| `list_friends` | `relations.rs` | 2197 |
| `get_visibility` | `relations.rs` | 2216 |
| `set_visibility` | `relations.rs` | 2263 |
| `create_session` | `sessions.rs` | 2294 |
| `list_sessions` | `sessions.rs` | 2350 |
| `get_session` | `sessions.rs` | 2403 |
| `session_chat` | `sessions.rs` | 2427 |
| `session_messages` | `sessions.rs` | 2456 |
| `patch_session` | `sessions.rs` | 2509 |
| `complete_session` | `sessions.rs` | 2534 |
| `add_session_member` | `sessions.rs` | 2578 |
| `remove_session_member` | `sessions.rs` | 2615 |
| `set_session_member_mode` | `sessions.rs` | 2652 |
| `create_session_invite_link` | `sessions.rs` | 2691 |
| `create_channel_binding` | `channels.rs` | 2736 |
| `list_channel_bindings` | `channels.rs` | 2766 |
| `list_channel_conversations_by_session` | `channels.rs` | 2793 |
| `delete_channel_binding` | `channels.rs` | 2827 |
| `service_invoke` | `services.rs` | 2867 |
| `service_session_status` | `services.rs` | 2923 |
| `prepare_session_file` | `files.rs` | 2963 |
| `put_session_file_bytes` | `files.rs` | 2988 |
| `complete_session_file` | `files.rs` | 3013 |
| `guess_mime_from_extension` | `files.rs` | 3036 |
| `is_text_mime` | `files.rs` | 3073 |
| `guess_charset` | `files.rs` | 3097 |
| `upload_session_file` | `files.rs` | 3127 |
| `list_session_files` | `files.rs` | 3220 |
| `delete_session_file` | `files.rs` | 3259 |
| `get_session_file` | `files.rs` | 3275 |
| `download_session_file` | `files.rs` | 3292 |
| `share_session_file` | `files.rs` | 3345 |
| `session_file_capabilities` | `files.rs` | 3378 |

### 子模块接口与导入规则

- 每个功能文件提供原签名的 `impl BcsClient`；不新建 Client trait、工厂或 request wrapper。
- 父模块私有字段和共用 headers/response 方法对子模块可见，保持 private，不需要 getter。
- `normalize_tags(&[String]) -> Vec<String>` 只被 `chat_async_payload` 使用，和 Chat 一起移动；
  不留在 bots 或改成 `pub(crate)`。Chat/Collaboration/文件私有 helper 同理留在其原调用所在模块。
- 公共 `admitted_outcome(&ChatRunSubmitResponse) -> ChatRunOutcome` 随 Chat 实现移动，
  `ChatRunOutcome::admission_succeeded(&self) -> bool` 留在原根，两个调用入口均兼容。
- 文件测试挂在 `client::files` 的后代；payload/轮询测试挂在 `client::chat` 的后代，
  因而可调用所属模块定义的 private inherent helper，不提升生产 helper 的可见性。
- 子模块使用 `super` 的显式导入和原依赖。下面为各文件的最小导入起点；
  仅删除由迁移造成的实际未使用 import，不对其他源文件整理 import。

`client/files.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
```

`client/bots.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    BindingChannels,
    BotCapabilities,
    BotConnectParams,
    BotConnectResponse,
    BotDynamicStatus,
    BotInfo,
    DiscoverBotsExtendedResponse,
    DiscoverBotsResponse,
    JoinRequest,
    JoinResponse,
    OnboardRequest,
    OnboardResponse,
    QueryBotEntry,
    QueryBotsRequest,
    Skill,
    UpdateStatusRequest,
    UpdateStatusResponse,
};
use tracing::{debug, info};
```

`client/groups.rs`：

```rust
use super::{BcsClient, BotGroupListPage, CurrentActorGroupListPage};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    CreateGroupRequest,
    CreateGroupResponse,
    ParticipantInfo,
};
use tracing::{debug};
```

`client/collaboration.rs`：

```rust
use super::{BcsClient, CreateCustomGroupOptions, RunSessionCollaborationOptions};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    CreateGroupRequest,
    CreateGroupResponse,
};
```

`client/fusion.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    FusionRequest,
    FusionResponse,
};
```

`client/relations.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result};
use bcs_protocol::{
    FriendApiResponse,
    SetVisibilityRequest,
};
```

`client/sessions.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
```

`client/channels.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
```

`client/services.rs`：

```rust
use super::{BcsClient};
use anyhow::{Context, Result, anyhow};
```

`client/chat.rs`：

```rust
use super::{BcsClient, ChatAsyncError, ChatRunOutcome};
use anyhow::{Context, Result, anyhow};
use bcs_protocol::{
    ChatRunCancelResponse,
    ChatRunState,
    ChatRunStatusResponse,
    ChatRunSubmitResponse,
};
use tracing::{debug, warn};
use std::time::Duration;
```

`client.rs` 的 `HashMap` / `BTreeMap`、Duration、Deserialize、HeaderValue、
ParticipantBindingInfo、Chat version 常量及根 helper 的 anyhow/tracing import 保留。
上述 `use` 不从私有子模块重新导出类型；现有 `lib.rs` 导出保持原样。

## 测试迁移清单（完整静态归属）

每个条目连同其测试属性、注释、完整函数体原样移动；本表用于定位，不替代实际发现清单。

| 原函数名 | 目标模块（相对本 plan 的主题目录） | 原起始行 |
| --- | --- | ---: |
| `guess_mime_from_extension_known_types` | `files/tests` | 3396 |
| `guess_mime_from_extension_unknown_returns_none` | `files/tests` | 3432 |
| `is_text_mime_recognizes_textual_types` | `files/tests` | 3440 |
| `guess_charset_returns_none_for_ascii` | `files/tests` | 3454 |
| `guess_charset_detects_utf8_and_cjk` | `files/tests` | 3467 |
| `guess_charset_detects_large_utf8_truncated` | `files/tests` | 3501 |
| `guess_charset_handles_utf16_bom` | `files/tests` | 3523 |
| `explicit_text_mime_still_eligible_for_charset` | `files/tests` | 3556 |
| `test_default_bcs_url` | `tests/headers` | 3592 |
| `test_custom_bcs_url` | `tests/headers` | 3603 |
| `test_with_token_and_oauth` | `tests/headers` | 3609 |
| `test_set_oauth_headers` | `tests/headers` | 3623 |
| `test_no_oauth_headers_by_default` | `tests/headers` | 3634 |
| `test_oauth_headers_layout` | `tests/headers` | 3646 |
| `test_no_oauth_uses_standard_authorization` | `tests/headers` | 3707 |
| `test_chat_headers_include_chat_version` | `tests/headers` | 3726 |
| `test_chat_dispatch_headers_include_configured_traceparent` | `tests/headers` | 3747 |
| `test_status_poll_headers_omit_trace_and_wait_controls` | `tests/headers` | 3788 |
| `test_set_traceparent_forwards_http_safe_values_without_w3c_semantic_validation` | `tests/headers` | 3814 |
| `test_set_traceparent_rejects_unsafe_http_header_values` | `tests/headers` | 3846 |
| `test_chat_version_header_advertises_version_3` | `tests/headers` | 3854 |
| `test_chat_run_status_deserializes_submitted_state` | `chat/tests/submission` | 3859 |
| `test_chat_run_status_deserializes_unknown_future_state` | `chat/tests/submission` | 3880 |
| `test_discover_bots_extended_includes_organization_scope_query` | `bots/tests` | 3901 |
| `delete_session_file_tolerates_204_no_content` | `files/tests` | 3953 |
| `test_chat_poll_run_until_running_waits_through_submitted_until_running` | `chat/tests/polling` | 3986 |
| `test_chat_poll_run_times_out_with_ids_from_submit` | `chat/tests/polling` | 4120 |
| `test_chat_poll_run_until_running_accepts_completed_as_success` | `chat/tests/polling` | 4226 |
| `test_chat_async_returns_transport_error_on_non_listening_port` | `chat/tests/submission` | 4289 |
| `test_chat_async_returns_invalid_response_on_malformed_202` | `chat/tests/submission` | 4321 |
| `test_chat_poll_run_returns_poll_error_on_http_500` | `chat/tests/polling` | 4364 |
| `test_chat_poll_run_returns_failed_on_terminal_failed_state` | `chat/tests/polling` | 4422 |
| `test_durable_admission_distinguishes_queued_from_rejected` | `chat/tests/submission` | 4509 |
| `test_queued_run_keeps_polling_to_requested_state` | `chat/tests/polling` | 4534 |
| `test_queued_run_deadline_bounds_slow_status_without_cancelling` | `chat/tests/polling` | 4571 |
| `test_queued_run_poll_errors_preserve_durable_identity` | `chat/tests/polling` | 4601 |
| `test_queued_run_terminal_failure_is_not_running_success` | `chat/tests/polling` | 4623 |
| `test_chat_run_cancel_preserves_unknown_delivery_state` | `chat/tests/submission` | 4639 |
| `test_non_chat_headers_omit_chat_version` | `tests/headers` | 4656 |
| `list_channel_conversations_by_session_encodes_query_parameters` | `channels/tests` | 4667 |
| `test_chat_async_payload_omits_cli_wait_controls` | `chat/tests/submission` | 4694 |
| `test_chat_async_payload_includes_organization_code_when_present` | `chat/tests/submission` | 4709 |
| `test_chat_async_payload_omits_blank_organization_code` | `chat/tests/submission` | 4720 |
| `cross_host_put_omits_authorization` | `files/tests` | 4736 |
| `create_session_sends_group_context_delivery` | `sessions/tests` | 4786 |
| `share_session_file_sends_ttl_seconds_field` | `files/tests` | 4820 |

静态分组核对：`files/tests` 11 个；`tests/headers` 14 个；`chat/tests/submission` 9 个；`bots/tests` 1 个；`chat/tests/polling` 9 个；`channels/tests` 1 个；`sessions/tests` 1 个。

## 执行前基线（并入 Task 1，不单独交付脚手架）

- [ ] 确认当前工作区干净、工具存在；读取上节指令和实际源码。

```bash
set -euo pipefail
export SOURCE_BASE="$(git rev-parse HEAD)"
export EVIDENCE="$(mktemp -d /tmp/avernet-split-p2.XXXXXX)"
printf '%s\n' "$SOURCE_BASE" > "$EVIDENCE/source-base.sha"
git show "$SOURCE_BASE:apps/bcs/crates/tools/bcs-cli/src/client.rs" > "$EVIDENCE/source.rs"
cargo --version > "$EVIDENCE/toolchain.txt"
cargo nextest --version >> "$EVIDENCE/toolchain.txt"
cargo metadata --manifest-path apps/bcs/Cargo.toml --no-deps --offline --locked \
  --format-version 1 > "$EVIDENCE/metadata.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-cli \
  --message-format json > "$EVIDENCE/package.before.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib \
  --message-format json > "$EVIDENCE/target.before.json"
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli \
  > "$EVIDENCE/baseline.log" 2>&1
```

预期：上述命令成功；完整 crate 的未忽略测试通过。`list` 包含 ignored 元数据，
不是要求默认执行依赖外部环境的 ignored 测试。失败就保留日志并报告，不能静默记为绿色。
如需特定 features，前后所有发现/执行命令都使用同一组 features 并在证据里记录。

- [ ] 执行现状大小断言，预期内部检查 **FAIL**，确认能发现原超大文件。下列 `if`
  记录预期失败而不结束 Bash 会话；如果检查意外通过，停止并重新核实是否仍需要拆分。

```bash
if python3 - <<'CHECK'
from pathlib import Path
p = Path('apps/bcs/crates/tools/bcs-cli/src/client.rs')
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

- [ ] 审计路径与过滤消费者，并把命中及处理决定放进 `$EVIDENCE/path-audit.txt`。

```bash
rg -n --glob '*.rs' --glob '*.py' --glob '*.sh' --glob '*.toml' \
  'contract_message_flow|tests/management\.rs|client\.rs|#\[path|include_str!|include_bytes!|module_path!|file!' \
  apps/bcs/crates/tools/bcs-cli apps/bcs/scripts apps/bcs/.config \
  > "$EVIDENCE/path-audit.txt" || test "$?" -eq 1
```

## Tasks

### Task 1：文件 API 与编码/隔离测试一起拆出

**Files:** Modify `apps/bcs/crates/tools/bcs-cli/src/client.rs`；Create
`src/client/files.rs`、`src/client/files/tests.rs`（均在 bcs-cli 下）。
**Test:** CLI lib 的 11 个 file 测试，以及原 caller file transfer integration tests。
**Interfaces:** Consumes 原 BcsClient 状态/headers/ensure_success；Produces 原文件 API 与
private `guess_mime_from_extension` / `is_text_mime` / `guess_charset`，签名完全不变。

- [ ] 完成“执行前基线”，确认原源文件的 46 个测试；`test_default_bcs_url` 的
  `#[allow(unsafe_code)]` 是原测试属性，后续移动时也必须保留。
- [ ] 按方法归属表迁移 files 的 13 个原方法（含 doc comment、body），使用上面的
  files 导入；根模块增加 `mod files;`，不增加可见性、不改原上传 URL 检查。
- [ ] 迁移归属表 11 个文件测试到 `client/files/tests.rs`。文件模块末尾挂载：

```rust
#[cfg(test)]
mod tests;
```

`client/files/tests.rs` 的模块依赖如下，原函数内 local use 保留：

```rust
use super::*;
use serde_json::json;
use std::time::Duration;
```

- [ ] 运行新增子模块测试和既有消费者文件传输回归，预期 11 个迁移测试通过，
  204、跨 origin 不发送 Authorization、share `ttl_seconds`、MIME/charset 语义不变。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib client::files::tests::
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test caller_file_transfer
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib
```

- [ ] 核实两个新文件大小与调用顺序、diff 后提交：
  `refactor(bcs): isolate CLI session file client methods`。原根尚超限，不单独交付。

### Task 2：Bot、Group、Collaboration、Fusion API 分责

**Files:** Modify 原 client 根；Create `client/bots.rs`、`client/bots/tests.rs`、
`client/groups.rs`、`client/collaboration.rs`、`client/fusion.rs`。
**Interfaces:** Consumes 原 root 类型与共用 helper；Produces 原方法归属表 36 个资源方法。

- [ ] 迁移 bots 的 13 个方法，包括不在原连续区间内的 `current_bot_uuid` 与 `query_bots`；
  迁移其 1 个 discover 测试，在 bots.rs 末尾增加 `#[cfg(test)] mod tests;`，测试文件
  使用 `use super::*; use serde_json::json; use std::time::Duration;`。
- [ ] 迁移 groups 12 个、collaboration 9 个、fusion 2 个方法；使用本节导入块。
  custom Group 方法留在 collaboration，普通 Group 管理留在 groups；所有原类型留在根。
- [ ] 根增加以下声明；迁移函数逐个从原 impl 块删除，不保留转发壳、重复实现或更换签名。

```rust
mod bots;
mod groups;
mod collaboration;
mod fusion;
```

- [ ] 执行以下回归，预期 Group 创建、组织查询、custom definition、draft 接线和原 CLI 输出兼容。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test create_group_tests
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test group_context_client
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test collaboration_draft
```

原 `draft_client.rs` 的声明/实现/测试均不修改，`draft_request` 仍使用根 client 私有字段。
- [ ] 检查新文件 ≤1000 行、原公共类型导出没变后提交：
  `refactor(bcs): separate CLI bot group and collaboration clients`。

### Task 3：Session、关系、Channel、Service API 分责

**Files:** Modify 原 client 根；Create `client/sessions.rs`、`client/sessions/tests.rs`、
`client/relations.rs`、`client/channels.rs`、`client/channels/tests.rs`、`client/services.rs`。
**Interfaces:** Consumes 原共用 auth/response；Produces 原方法归属表 24 个资源方法和 2 个迁移测试。

- [ ] 按方法归属表分别迁移 Session 11 个、relations 7 个、channels 4 个、services 2 个方法。
  `set_actor_id` 留在根；`query_bots` 已在 bots，不复制到 relations。
- [ ] 原 Session/Channel 各 1 个测试随所属模块迁移，各生产文件末尾使用
  `#[cfg(test)] mod tests;`；两个测试文件使用 `use super::*; use serde_json::json;`。
- [ ] 根增加以下接线，使用上面的显式导入；Service Key 和调用方 attribution 逻辑不移动到资源方法。

```rust
mod sessions;
mod relations;
mod channels;
mod services;
```

- [ ] 运行 lib、caller context 及 CLI 协议测试，预期路径/query 编码、body、Auth/Actor 和输出不变。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test caller_context
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test cli_command_contracts
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test cli_decomposition_contract
```

- [ ] 核实新文件大小、已有 consumer 调用仍编译后提交：
  `refactor(bcs): separate CLI session relation and service clients`。

### Task 4：Chat/headers 测试分责、收敛根文件与独立验收

**Files:** Modify 原 client 根及 client 豁免文档；Create `client/chat.rs`、
`client/chat/tests.rs`、`client/chat/tests/submission.rs`、`client/chat/tests/polling.rs`、
`client/tests.rs`、`client/tests/headers.rs`。
**Interfaces:** Consumes root headers/公开类型；Produces 原 13 个 Chat 方法、18 个 Chat 测试、
14 个 root headers 测试以及所有原对外导出。此时原 46 个测试均已迁移一次。

- [ ] 将 13 个 Chat 方法（含 normalize_tags、4 个 outcome helper、admitted_outcome）
  一起移入 chat.rs，使用上面的 chat 导入块；根增加 `mod chat;`。私有 helper 保持 private。
- [ ] 迁移 18 个 Chat 测试，9 个 submission 和 9 个 polling 按表分配；两个既有
  `queued_submit() -> ChatRunSubmitResponse`、
  `queued_status(&str, &str, u64) -> serde_json::Value` 的完整原函数体放到 `client/chat/tests.rs`。
  保留原固定数据、版本/终态判断，不引入新的 fixture。聚合文件的接线如下：

```rust
use super::*;
mod submission;
mod polling;
```

chat.rs 末尾使用 `#[cfg(test)] mod tests;`；submission/polling 两文件使用
`use super::*; use serde_json::json;`。它们是 chat 的后代，能访问 private Chat helper。

- [ ] 将剩余 14 个构造/headers 测试原样放在 `client/tests/headers.rs`，保留
  `test_default_bcs_url` 的 unsafe allow。原 client 根的 inline tests 块替换为：

```rust
#[cfg(test)]
mod tests;
```

`client/tests.rs` 完整内容为 `mod headers;`；headers.rs 使用：

```rust
use crate::client::*;
use serde_json::json;
```

- [ ] 运行全 lib 和 caller context 回归，保持 Submit/Queued/Unknown/terminal、预算与
  poll-error identity 不变；共用 headers、重定向策略和所有状态/类型仍留在根。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test caller_context
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli --test caller_file_transfer
```

- [ ] 执行下方清单、完整 crate、所有 consumer 和大小检查；review 验证 root/所有模块
  均 ≤1000 行后，再移除 waiver 表内 `crates/tools/bcs-cli/src/client.rs` 这一条。
  将 reason 的“五个文件”数量说明更新为剩余活动条目的真实数量，保留其他路径、历史基线、
  owner、到期日 **2026-11-12**、补偿控制和 removal 条件；不删除整份 waiver 或延长期限。
- [ ] 审核 `lib.rs` 未修改、draft 接线未修改、无新 dependency 与行为变化，提交：
  `refactor(bcs): complete CLI client responsibility split`。
- [ ] 在最终提交上运行架构、unit coverage、统一 Singlebox，记录所有结果后才可声明验收完成。

## 末任务使用的清单一致性检查（可直接执行）

每个迁移任务后先运行目标测试；全部迁移完成后，执行以下完整检查。它保留未迁移
测试的完整名称，只对源文件中的迁移测试允许模块前缀变化；比较 binary ID、测试
集合及 ignored 状态，并生成逐测试映射，重复挂载会导致失败。

```bash
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-cli \
  --message-format json > "$EVIDENCE/package.after.json"
cargo nextest list --manifest-path apps/bcs/Cargo.toml -p bcs-cli --lib \
  --message-format json > "$EVIDENCE/target.after.json"
python3 - <<'CHECK'
import json, os, re
from pathlib import Path
p = Path(os.environ['EVIDENCE'])
source = (p / 'source.rs').read_text()
pattern = (r'^\s*#\[(?:tokio::)?test(?:\([^\n]*\))?\]\s*'
           r'(?:#\[[^\n]+\]\s*)*(?:async\s+)?fn\s+(\w+)\(')
moved = set(re.findall(pattern, source, re.M))
assert len(moved) == 46, '源码已漂移：先更新归属表，不可丢弃新增测试'
load = lambda n: json.loads((p / n).read_text())['rust-suites']
tb, ta = load('target.before.json'), load('target.after.json')
assert len(tb) == len(ta) == 1 and tb.keys() == ta.keys()
target_id = next(iter(tb))
def collect(filename):
    result = {}
    for sid, suite in load(filename).items():
        for name, case in suite.get('testcases', {}).items():
            leaf = name.rsplit('::', 1)[-1]
            moving = sid == target_id and leaf in moved and (name.startswith('client::'))
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
assert len(rows) == 46
(p / 'test-map.tsv').write_text(''.join('\t'.join(row) + '\n' for row in sorted(rows)))
print('PASS: full package discovery stable; mapped', len(rows), 'moved tests')
CHECK
```

对照本 plan 归属表逐行审阅 `test-map.tsv` 的新模块前缀；不能只满足数量相同。
`allow`、其他 `cfg`、超时属性和源码断言还要通过移动差异审查验证，不能只依赖 ignored 比对。

## 末任务的交付验证与提交边界

- [ ] 执行所属 crate 全量测试、编译及路径/属性差异审查。

```bash
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-cli
cargo check --manifest-path apps/bcs/Cargo.toml -p bcs-cli --all-targets
cargo check --manifest-path apps/bcs/Cargo.toml -p bcs -p bcs-http -p bcs-admin --all-targets
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-http --test cli_session_integration
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-http --test cli_service_integration
git add -N -- apps/bcs/crates/tools/bcs-cli/src/client
git diff --check
# 每次对本 plan 的执行基线审查迁移差异；不以格式化后的 diff 替代。
git diff "$SOURCE_BASE" --color-moved=blocks -- apps/bcs/crates/tools/bcs-cli/src/client.rs apps/bcs/crates/tools/bcs-cli/src/client
python3 - <<'CHECK'
from pathlib import Path
files = set()
for name in ['apps/bcs/crates/tools/bcs-cli/src/client.rs', 'apps/bcs/crates/tools/bcs-cli/src/client']:
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

- [ ] 在本 plan 的交付记录中填写执行起止 SHA、修改文件行数、测试映射、每条验证命令
  的结果和报告位置。没有工具、服务或网络时，明确标记未验收，不能声称实施完成。
- [ ] 最后审查 AC-1 至 AC-10；业务修复另开任务，数据库/缓存/网络访问成本变化应为零。
  回滚只回滚本 plan 的源码迁移及记录，不改数据库、配置或另外两个 plan 的文件。

## CLI 专项最终审查

- metadata 中新增的直接消费者也纳入 `--all-targets` 检查，不能永久写死只检查旧消费者。
- 源码方法表的 109 个 BcsClient 固有方法（含 root 23 个）全部有且仅有一份；
  `ChatRunOutcome::admission_succeeded`、`draft_request` 保留原实现而非重复到新模块。
- 默认超时、chat submit 的 10 秒请求 timeout、客户端总轮询预算、不同请求的 trace/
  wait/version header 设置、省略行为、service-key/Actor/OAuth 语义只迁移、不重写。
- 若 consumer 编译需要改动参数/导入或公开新类型，视为偏离 spec，停止并重新审阅，
  不修改消费者让不兼容的拆分通过。

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

本文件是待执行计划；所有复选框保持未勾选。编写阶段只做源码归属、命令接口和文档
自检，没有执行迁移、Rust 测试、覆盖率或 Singlebox，不能把文中预期结果记为实际通过。
