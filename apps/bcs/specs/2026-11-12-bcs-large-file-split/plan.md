# Avernet BCS 五个超限 Rust 文件拆分实施计划

- **状态**：实施完成（执行记录见文末「实施执行记录（2026-11-12）」）；集成门禁未全部通过，不标记为完成。
- **Design**：[五文件设计](spec.md)。
- **目标**：只在 Avernet `apps/bcs` 内完成 design 的 F1–F5；五个入口和全部本次新增/修改
  Rust 文件 ≤1000 物理行，目标 ≤800，行为和现有契约保持不变。
- **基线**：`9a61798cb7752308aaa01f8e59d78a46809066cb`；下列映射来自此版本，实施前重新采样。
  （映射采样自该基线；实际实施在 `b98b580a870050eebcd9d98602d45c9553d1d1e4` 上执行，
  Task 0 重采确认零漂移，SOURCE_BASE 记录见文末执行记录。）
- **技术**：当前 workspace 声明的 Rust edition/toolchain、Cargo、nextest 和既有公共 CI。
  不把某次工作机的工具版本或缺失状态写成仓库前置事实。

> 本文的勾选框、逐阶段流程与“预计/约”行数保留**原始计划**形态；实际执行结果、最终行数
> 和门禁状态以文末「实施执行记录（2026-11-12）」为准。

## 执行约束与共同验收

按 Task 0 → F5 → F4 → F2 → F3 → F1 → 收口执行；每个目标自包含，可独立 review。
不为分阶段而提交不可编译的状态；目标根文件在迁移过程中可能超限，只在该目标全部迁移后验收尺寸。
每阶段提交前检查完整 diff；使用精确路径暂存，不使用全仓 `git add -A`。
不改 dependencies、Cargo.lock、schema、业务逻辑、fixtures 内容、权限和测试属性，不运行全局格式化。
原入口中的共有测试 helpers 保留私有，主题直接作为后代访问，不批量扩大可见性。
F2 根预计约 880 行，明确接受建议 800 行目标的例外，仍必须 ≤1000；其余目标按 ≤800 预算。

所有命令从 **Avernet 仓库根目录**、同一 Bash 会话执行，`set -euo pipefail`。
源码相对路径都以 `apps/bcs/` 开头。宿主 overlay、子模块初始化和内部 ACI 脚本不是此计划前置。
按 [开发规则](../../../../docs/agents/development.md)、[验证规则](../../../../docs/agents/validation.md)
和 [PR 规范](../../../../docs/agents/pull-requests.md) 执行；失败、缺工具与 skip 单独报告，不能作为通过。

每个目标执行共同流程：

1. 按下方静态映射定位完整测试函数或生产 item（连属性、doc comments），检查真实 HEAD 漂移。
2. 按职责逐个主题迁移，立即执行该目标的 check/test；不改变函数名、断言、mock 与调用顺序。
3. 用 `check_size.py 原入口 新目录` 验收该单元；不能提前检查尚未拆完的其他入口。
4. 重新采集该 crate 的 nextest JSON；逐项核对 before→after 全名和 ignored 状态。
   本文映射中的根测试变成 `主题::原函数名`，未迁的子模块测试保持完整名称；
   `task_rpc_scope::*` 等必须保留，不只核对根部静态数量。
5. 比较全部 tests 的 cfg/ignore/timeout 与路径敏感引用、fixture 内容、公共导出；
   staged diff 必须包含新增文件，不能只统计删除行或依靠 color-moved 总数证明纯移动。
6. 单元通过后，只暂存其入口和目录并提交；记录实际命令、退出码和日志路径。

## Task 0：基线、测试发现和尺寸检查工具

**写入范围**：临时证据目录；不修改源码或机器配置，不覆盖 `.cargo/config.toml`。

- [ ] 阅读 design 中的全部指令/模块 context；确认工作分支干净、同步目标基线并重采五文件行数。
  用实际源码更新后文映射，若目标已被其他 PR 拆分，先修订范围，不能照旧表复建旧代码。
- [ ] 在原有 hooks 保持启用的情况下确认 cargo、nextest、protoc 及覆盖率工具可用。
  采用仓库公开安装入口；工具缺失先补齐，不伪造测试基线，不用失败后继续的 tail 管道。

```bash
set -euo pipefail
export SOURCE_BASE="$(git rev-parse HEAD)"
export EVIDENCE="$(mktemp -d /tmp/avernet-bcs-split.XXXXXX)"
export M=apps/bcs/Cargo.toml
printf '%s\n' "$SOURCE_BASE" > "$EVIDENCE/source-base.sha"
git status --short > "$EVIDENCE/status.before.txt"
cargo --version > "$EVIDENCE/toolchain.txt"
rustc --version >> "$EVIDENCE/toolchain.txt"
cargo nextest --version >> "$EVIDENCE/toolchain.txt"
cargo metadata --manifest-path "$M" --no-deps --offline --locked \
  --format-version 1 > "$EVIDENCE/metadata.before.json"
for p in bcs-http bcs-ws bcs-bot bcs-organization; do
  cargo nextest list --manifest-path "$M" --locked -p "$p" \
    --message-format json > "$EVIDENCE/$p.before.json"
  cargo test --manifest-path "$M" --locked -p "$p" \
    > "$EVIDENCE/$p.baseline.log" 2>&1
done
```

`nextest list` 会构建测试，不是纯 metadata 查询。基线失败时停止该目标，保留完整日志；
先查清既有失败或环境问题，不通过删除、忽略、修改 fixture 来得到绿色基线。
全部后续命令与基线保持相同 features/toolchain；默认忽略的外部环境测试保持原标记。

- [ ] 将下面脚本保存到 `$EVIDENCE/check_size.py`（仅临时工具，不引入新的仓库门禁）。
  无参数模式检查五个入口、相对 `SOURCE_BASE` 的全部新增/修改 Rust 文件，以及未暂存新文件；
  指定路径模式用于单元验收。文件不存在、没有找到文件、任何文件 >1000 均失败。

```python
import os
from pathlib import Path
import subprocess
import sys

TARGETS = (
    "apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs",
    "apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs",
    "apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs",
    "apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs",
    "apps/bcs/crates/services/bcs-organization/tests/management.rs",
)

def git_paths(*args):
    data = subprocess.check_output(["git", *args])
    return {Path(os.fsdecode(p)) for p in data.split(b"\0") if p}

paths = set()
if sys.argv[1:]:
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            raise SystemExit(f"missing path: {path}")
        paths.update(path.rglob("*.rs") if path.is_dir() else [path])
else:
    paths.update(map(Path, TARGETS))
    paths.update(git_paths("diff", "--name-only", "-z", "--diff-filter=ACMR",
                           os.environ["SOURCE_BASE"], "--", "apps/bcs"))
    paths.update(git_paths("ls-files", "--others", "--exclude-standard", "-z",
                           "--", "apps/bcs"))
paths = sorted(p for p in paths if p.suffix == ".rs")
if not paths:
    raise SystemExit("no Rust files selected")
failed = False
for path in paths:
    data = path.read_bytes()
    lines = data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
    print(f"{lines:5} {path}")
    failed |= lines > 1000
raise SystemExit(1 if failed else 0)
```

- [ ] 基线运行 `python3 "$EVIDENCE/check_size.py"` 应报告五个原入口超限并返回 1；
  这只是尺寸检查的预期红灯，不是允许 cargo 基线失败。最终同脚本必须返回 0。

## Task 1：F5 Organization management

**Files**：修改 `apps/bcs/crates/services/bcs-organization/tests/management.rs`；
新建其旁 `management/{membership,conformance,member_profile,candidates,runtime_membership}.rs`。
不修改 `services/bcs-group/tests/management.rs`。

- [ ] 保留根的 ProviderFixture、TestContext、auth/query builders、test_context、register_provider*、
  grant_manager、register_bot、create_org。按附录 F5 将 26 个测试完整搬入主题。
- [ ] 根用 `#[path = "management/membership.rs"] mod membership;` 模式显式挂载每个主题；
  子模块加 `use super::*;`，不新增公共 helper 或改变 root fixture 的字段。
- [ ] 执行验证、清单映射与 diff 审阅，再按共同流程提交本单元。

```bash
cargo check --manifest-path "$M" --locked -p bcs-organization --all-targets
cargo test --manifest-path "$M" --locked -p bcs-organization --test management
cargo test --manifest-path "$M" --locked -p bcs-organization
cargo nextest list --manifest-path "$M" --locked -p bcs-organization \
  --message-format json > "$EVIDENCE/bcs-organization.after.json"
python3 "$EVIDENCE/check_size.py" \
  apps/bcs/crates/services/bcs-organization/tests/management.rs \
  apps/bcs/crates/services/bcs-organization/tests/management
```

## Task 2：F4 Bot use cases

**Files**：修改 `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs`；
新建其旁 `bot_use_cases/{queries,discovery,organization_discovery,connection,status,authority_writes}.rs`。

- [ ] 根保留 RegistryFixture、ProviderRegistryFixture、OrganizationProviderFixture、
  StaticFriendCoreService、StaticOrganizationCoreService 及其全部 impl、注册/auth/caps helpers。
- [ ] 按附录 F4 移动 37 个测试；根按 `#[path = "bot_use_cases/queries.rs"] mod queries;`
  模式接线，主题直接用父模块设施。不得复写另一份不同权限语义的 fixture。
- [ ] 执行下列命令与共同验收；owner/manager、provider、动态 active 与静态描述的断言逐字保留。

```bash
cargo check --manifest-path "$M" --locked -p bcs-bot --all-targets
cargo test --manifest-path "$M" --locked -p bcs-bot --test bot_use_cases
cargo test --manifest-path "$M" --locked -p bcs-bot
cargo nextest list --manifest-path "$M" --locked -p bcs-bot \
  --message-format json > "$EVIDENCE/bcs-bot.after.json"
python3 "$EVIDENCE/check_size.py" \
  apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs \
  apps/bcs/crates/services/bcs-bot/tests/bot_use_cases
```

## Task 3：F2 Bot frame compatibility

**Files**：修改 `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs`；
新建其旁 `frame_compat/{connection,diagnostics,v3_events,interactions,task_dispatch,coordination_echo,chat_events,tracing,run_aliases}.rs`。

- [ ] 按 design §4 保留所有非测试设施在根；尤其不移动 `task_rpc_scope` 声明，不复制共享模块。
- [ ] 按附录 F2 移动 58 个直接测试；每个主题用显式 `#[path = "frame_compat/主题.rs"]`
  挂载为根的直接子模块，`use super::*;` 可继续访问 private fields。
- [ ] 每个主题迁完运行入口测试；12 个 interaction tests 和 15 个 coordination echo tests
  均需完整保留，不能通过忽略 timeout/sequence/去重用例处理拆分失败。
- [ ] 完成后运行共同验收。完整发现清单应保留未迁的 `task_rpc_scope::*`，不把 58 当作 target 总数。

```bash
cargo check --manifest-path "$M" --locked -p bcs-ws --all-targets
cargo test --manifest-path "$M" --locked -p bcs-ws --test frame_compat
cargo test --manifest-path "$M" --locked -p bcs-ws
cargo nextest list --manifest-path "$M" --locked -p bcs-ws \
  --message-format json > "$EVIDENCE/bcs-ws.after-f2.json"
python3 "$EVIDENCE/check_size.py" \
  apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs \
  apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat
```

## Task 4：F3 Workbench frame compatibility

**Files**：修改 `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs`；
新建其旁 `web_frame_compat/{diagnostics,subscriptions,chat_send,abort,interactions,bound_connection,bound_messages,ingress_identity}.rs`。

- [ ] 根保留 `mod support;`、所有 Recording 类型/impl、SessionHumanInputBehavior 及 design §4
  列出的 session-bound/ingress helpers，不改 `tests/support/mod.rs`。
- [ ] 按附录 F3 移动 40 个测试，用 `#[path = "web_frame_compat/主题.rs"]` 接线。
- [ ] 每个主题迁完跑入口；最终检查 bound Session/identity 不能逃逸、重放在 ACK 之后、
  pending Human input 消费、abort scope 和 live ingress 的原断言没有弱化。
- [ ] 按共同流程验收，比较最终 bcs-ws 清单与 Task 0 基线，既覆盖 F2 也覆盖 F3。

```bash
cargo check --manifest-path "$M" --locked -p bcs-ws --all-targets
cargo test --manifest-path "$M" --locked -p bcs-ws --test web_frame_compat
cargo test --manifest-path "$M" --locked -p bcs-ws
cargo nextest list --manifest-path "$M" --locked -p bcs-ws \
  --message-format json > "$EVIDENCE/bcs-ws.after.json"
python3 "$EVIDENCE/check_size.py" \
  apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs \
  apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat
```

## Task 5：F1 Session HTTP handlers

**Files**：修改 `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs`；新建
`src/routes/sessions/{creation,queries,lifecycle,participants,chat,history,collections}.rs`。
原有 `src/router.rs`、`routes/{channel,services,session_files}.rs`、测试入口及依赖不改。

- [ ] 先记录 `sessions.rs` 的全部 `pub` / `pub(crate)` 函数和 DTO，与 design §3 对照。
  明确创建/查询/生命周期/参与者/chat/history/collection 归属及跨模块 helper。
- [ ] 逐簇抽取，建议 collections → creation → queries → lifecycle → participants → chat → history。
  每簇完成编译和 suite_sessions；根仍可能超限，不提前宣称尺寸通过。
- [ ] 根保持公共入口，显式 re-export：

```rust
mod creation;
mod queries;
mod lifecycle;
mod participants;
mod chat;
mod history;
mod collections;

pub use creation::{CreateSessionRequest, create_session_for_group};
pub use queries::{ListSessionsQuery, get_session_by_id, list_sessions_for_group};
pub use lifecycle::{PatchSessionRequest, complete_session, delete_session, patch_session};
pub use participants::{AddParticipantRequest, UpdateParticipantModeRequest,
    add_session_participant, remove_session_participant, update_session_participant_mode};
pub use chat::{SessionChatRequest, session_chat};
pub use history::{GetSessionMessagesQuery, get_session_messages};
pub use collections::{CollectSessionRequest, collect_session, uncollect_session};
use collections::resolve_collector_bot;
```

各 child 保持原 public 项，新增 child module 自身私有。共享父 helpers 无需放宽。
`resolve_collector_bot` 在 collections 内标 `pub(super)`，其签名/实现不变。
`delete_session` 的 Query 类型路径改为 `crate::routes::groups::DeleteSessionQuery`。
原有 human 控制判断、session_error 映射等留在根；不能让其他 routes 改 import 才能编译。

- [ ] 六处 tracing 宏显式添加 `target: "bcs_http::routes::sessions"`：participants 的两个 warn、
  chat 的 warn/error、lifecycle 的 info/warn。保留原日志级别/字段/文案，不改求值顺序。
  这六处 metadata 保持补丁与纯移动分别审阅，不能留下隐式 target 随模块名改变。
- [ ] 所有生产簇迁完后才检查 sessions 根与全部子模块行数。
- [ ] 执行契约、完整 crate 和 workspace 消费者编译；检查 DTO 导入、路由及 authority/history/collection
  行为不变。保存 bcs-http 测试清单（F1 不移动测试，完整测试名应保持一致）。

```bash
# 每簇的快速循环
cargo check --manifest-path "$M" --locked -p bcs-http --all-targets
cargo test --manifest-path "$M" --locked -p bcs-http --test suite_sessions
# F1 收口
cargo test --manifest-path "$M" --locked -p bcs-http
cargo check --manifest-path "$M" --locked --workspace --all-targets
cargo nextest list --manifest-path "$M" --locked -p bcs-http \
  --message-format json > "$EVIDENCE/bcs-http.after.json"
python3 "$EVIDENCE/check_size.py" \
  apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs \
  apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions
```

## Task 6：组合验收、豁免清理与交付

**Files**：仅清理 `apps/bcs/docs/waivers/agentpass-subbots-source-size.md` 中 F1/F2/F4/F5 的活动条目；
在本文/design 追加实际执行记录。历史基线另保留为已完成记录，其余 waiver 条目/期限不变。

- [ ] 所有阶段完成，运行全范围尺寸检查与 diff 检查；五个原文件和每个新增/修改 `.rs` 均列入报告。
  即使全仓另有超限文件，也不把本工单扩大成全部历史清理；本次引入的文件不得漏检。

```bash
python3 "$EVIDENCE/check_size.py" | tee "$EVIDENCE/size.after.txt"
git diff --check
git diff --cached --check
git diff --stat "$SOURCE_BASE"
```

- [ ] 对比 Cargo metadata：不新增 integration test binary、不改变原 targets；对比四个 crate 的
  before/after 测试清单，附录映射应为一一映射。已有子模块和所有未涉及的测试保持原样。
  `git diff --color-moved` 只辅助审阅，不能自动证明完整性。
- [ ] 按 PR 模板准备真实说明并保存到 `$EVIDENCE/pr-body.md`；记录无契约变化、实际消费者与
  migration/waiver 处理。完成精确路径暂存与提交后，再运行基于 commit range 的集成门禁。

```bash
cargo metadata --manifest-path "$M" --no-deps --offline --locked \
  --format-version 1 > "$EVIDENCE/metadata.after.json"
BCS_BASE_REF="$SOURCE_BASE" PR_BODY_FILE="$EVIDENCE/pr-body.md" \
  bash apps/bcs/scripts/ci/arch-check.sh
bash apps/bcs/scripts/ci_test.sh --fast-fail --coverage --base "$SOURCE_BASE" --head HEAD
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

审计 arch-check 各子检查的输出，不能因聚合退出 0 就把 skip 算作覆盖。
保持现有 unit 100% pass、overall line >87%、changed line >85%（超过50条 instrumentable changed lines）的要求；
Singlebox 保持全故事通过、BCS runtime line ≥54%、method ≥48%、HTTP endpoint/CLI leaf 100%，
E2E scope ≥20 条 instrumentable changed lines 时 changed-line >65%；更小变更按现有规则报告。
工具/环境不可用的检查列为未验收，由 CI 补充同一最终提交的实际通过证据，不能降低门槛。

- [ ] 正常安装 hooks 并 push，不绕过 hooks，不设置禁用门禁变量；依赖远端补验时保持 PR 未完成状态。
  提交之后若修改了 Rust 或文档中的验收范围，需对最终提交重跑受影响检查。
- [ ] 只有所有必需证据齐全才更新状态为完成；记录最终行数、测试映射、验证结果和实施 PR/commit。
  回滚按目标文件单元进行，无 schema/config 迁移；本计划完成不代表其他超限文件已清理。

## 附录：四个 integration target 的逐测试迁移表

下表只包含原入口的直接测试，保留原函数名；测试属性、doc comments 随函数完整搬迁。
列中的行号是原始声明定位，不是迁移边界。已有子模块测试不迁移，实际 nextest 清单另行保留。
共享 helpers 不在表中，因为全部留在各自 integration 根。静态总数：F2=58、F3=40、F4=37、F5=26。

### F2

| 原测试函数 | 目标主题 | 声明行 |
| --- | --- | ---: |
| `bot_connect_rejects_provider_delivery_before_streaming_registration` | `connection` | 503 |
| `bot_connect_error_preserves_requested_uuid` | `connection` | 560 |
| `invalid_bot_events_are_dropped_with_request_correlated_warnings` | `diagnostics` | 743 |
| `status_for_removed_bot_returns_not_registered_and_correlates_warning` | `diagnostics` | 767 |
| `bot_socket_errors_keep_request_identity_and_cleanup_after_disconnect_failure` | `diagnostics` | 787 |
| `bot_connect_and_status_frames_are_compatible` | `connection` | 903 |
| `bot_connect_without_version_stays_on_v2_compatibility_default` | `connection` | 960 |
| `bot_v3_native_mcp_declaration_does_not_self_authorize_task_intent` | `v3_events` | 995 |
| `bot_v3_connect_negotiates_canonical_events_and_validates_run_sequence` | `v3_events` | 1037 |
| `bot_v3_agent_error_is_adapted_to_terminal_chat_error` | `v3_events` | 1165 |
| `bot_v3_releases_sequence_when_business_processing_fails` | `v3_events` | 1232 |
| `bot_v3_duplicate_terminal_frame_is_idempotent` | `v3_events` | 1302 |
| `bot_v3_interaction_requested_forwards_to_interaction_service` | `interactions` | 1368 |
| `bot_v3_interaction_resolved_forwards_to_interaction_service` | `interactions` | 1439 |
| `bot_v3_interaction_rejects_terminal_run` | `interactions` | 1502 |
| `bot_v3_interaction_rejects_frame_seq_mismatch` | `interactions` | 1593 |
| `bot_v3_interaction_rejects_unknown_run` | `interactions` | 1623 |
| `bot_v3_interaction_rejects_foreign_bot_run_identity` | `interactions` | 1653 |
| `bot_v3_interaction_rejects_session_identity_mismatch` | `interactions` | 1696 |
| `bot_v3_interaction_invalidates_pending_on_expired_run` | `interactions` | 1738 |
| `bot_v3_interaction_from_unregistered_bot_is_dropped_safely` | `interactions` | 1786 |
| `bot_v3_interaction_duplicate_seq_is_rejected` | `interactions` | 1813 |
| `bot_v3_interaction_reserves_seq_shared_with_agent_frames` | `interactions` | 1871 |
| `bot_v3_interaction_releases_seq_when_service_fails` | `interactions` | 1972 |
| `legacy_bot_event_cannot_spoof_task_intent_eligibility` | `task_dispatch` | 2029 |
| `task_dispatch_unwraps_legacy_session_group_id` | `task_dispatch` | 2071 |
| `task_dispatch_maps_muted_target_error_code` | `task_dispatch` | 2096 |
| `task_message_unwraps_legacy_session_group_id` | `task_dispatch` | 2122 |
| `task_complete_unwraps_legacy_session_group_id` | `task_dispatch` | 2148 |
| `coordination_echo_manager_assign_task_dispatches_once_and_dedups` | `coordination_echo` | 2174 |
| `coordination_echo_manager_assign_task_forwards_response_mode` | `coordination_echo` | 2223 |
| `coordination_echo_manager_assign_task_error_notifies_manager` | `coordination_echo` | 2254 |
| `coordination_echo_manager_assign_task_whitespace_message_does_not_dispatch` | `coordination_echo` | 2305 |
| `coordination_echo_tool_start_phase_does_not_dispatch` | `coordination_echo` | 2333 |
| `coordination_echo_non_exec_tool_name_does_not_dispatch` | `coordination_echo` | 2361 |
| `coordination_echo_worker_cannot_assign_task` | `coordination_echo` | 2389 |
| `coordination_echo_manager_send_task_message_notifies_manager_and_keeps_result_event` | `coordination_echo` | 2417 |
| `coordination_echo_worker_send_task_message_dispatches_with_session` | `coordination_echo` | 2467 |
| `coordination_echo_worker_send_task_message_empty_message_does_not_dispatch` | `coordination_echo` | 2499 |
| `coordination_echo_worker_send_task_message_without_real_session_does_not_dispatch` | `coordination_echo` | 2526 |
| `coordination_echo_manager_task_complete_dispatches_via_echo` | `coordination_echo` | 2576 |
| `coordination_echo_manager_task_complete_whitespace_summary_does_not_dispatch` | `coordination_echo` | 2611 |
| `coordination_echo_error_result_does_not_dispatch` | `coordination_echo` | 2638 |
| `chat_event_with_tool_like_coordination_payload_is_not_coordination_echo` | `coordination_echo` | 2665 |
| `bot_chat_event_frame_is_forwarded_to_message_flow` | `chat_events` | 2723 |
| `direct_chat_event_scope_comes_from_run_context_for_all_session_key_shapes` | `chat_events` | 2759 |
| `traced_chat_event_creates_bot_response_child_span_after_message_flow_accepts` | `tracing` | 2817 |
| `traced_plugin_run_alias_creates_bot_response_child_span` | `tracing` | 2899 |
| `chat_event_without_trace_mapping_does_not_create_response_span` | `tracing` | 2971 |
| `rejected_group_request_uses_canonical_message_flow_error_and_retains_lookup_failure` | `chat_events` | 3055 |
| `bot_response_registers_state_machine_delivery_alias` | `run_aliases` | 3081 |
| `task_response_ack_from_target_worker_registers_sub_run_alias` | `run_aliases` | 3114 |
| `direct_chat_response_alias_carries_run_context_to_streaming_events` | `run_aliases` | 3165 |
| `task_response_ack_from_manager_does_not_register_sub_run_alias` | `run_aliases` | 3241 |
| `bot_chat_event_frame_unwraps_v2_session_id_group_id` | `chat_events` | 3291 |
| `bot_event_frame_restores_session_id_from_sub_run_mapping` | `chat_events` | 3321 |
| `bot_chat_event_frame_uses_explicit_bcs_session_id` | `chat_events` | 3378 |
| `connected_socket_cannot_handshake_as_another_bot` | `connection` | 3409 |

### F3

| 原测试函数 | 目标主题 | 声明行 |
| --- | --- | ---: |
| `unexpected_frontend_frames_are_ignored_with_correlated_warnings` | `diagnostics` | 553 |
| `invalid_session_authorization_context_closes_and_logs_request_id` | `diagnostics` | 577 |
| `interaction_service_failures_preserve_connect_ack_and_retryable_resolution_error` | `diagnostics` | 602 |
| `frontend_socket_malformed_binary_and_abrupt_close_logs_keep_request_id` | `diagnostics` | 638 |
| `web_connect_without_view_actor_preserves_legacy_full_subscription` | `subscriptions` | 699 |
| `web_connect_with_explicit_human_view_binds_participant_projection` | `subscriptions` | 793 |
| `web_connect_with_session_id_subscribes_session_registry_key` | `subscriptions` | 869 |
| `web_chat_send_frame_is_forwarded_to_message_flow_and_tracks_run` | `chat_send` | 935 |
| `session_chat_send_does_not_inherit_group_full_scope` | `chat_send` | 1042 |
| `web_chat_send_is_consumed_by_the_single_pending_human_input_and_emits_empty_final` | `chat_send` | 1121 |
| `web_chat_send_is_rejected_when_state_machine_has_no_pending_human_input` | `chat_send` | 1191 |
| `web_chat_send_uses_session_subscription_key_for_sender_conn_id` | `chat_send` | 1260 |
| `web_chat_send_uses_view_bound_to_the_target_session` | `chat_send` | 1327 |
| `user_bound_chat_abort_preserves_existing_response` | `abort` | 1396 |
| `user_bound_chat_abort_reports_legacy_plugin_restart_resolution` | `abort` | 1463 |
| `user_bound_legacy_run_id_resolves_to_bot_session_scope` | `abort` | 1536 |
| `user_bound_chat_abort_requires_connect` | `abort` | 1592 |
| `anonymous_user_bound_chat_abort_is_unauthorized_after_connect` | `abort` | 1636 |
| `user_bound_chat_abort_requires_target_session_subscription` | `abort` | 1697 |
| `user_bound_unknown_method_preserves_protocol_error_response` | `diagnostics` | 1760 |
| `session_bound_connect_publishes_the_workbench_speaking_mode_projection` | `bound_connection` | 1826 |
| `session_bound_connect_replays_pending_interactions_after_ack` | `interactions` | 1864 |
| `user_bound_connect_replays_pending_interactions_after_ack` | `interactions` | 1906 |
| `interaction_resolve_returns_accepted_and_structured_failures` | `interactions` | 1967 |
| `interaction_resolve_preserves_empty_array_skip` | `interactions` | 2112 |
| `session_bound_requires_connect_before_business_frames` | `bound_connection` | 2161 |
| `session_bound_scope_mismatch_closes_without_dynamic_authorization` | `bound_connection` | 2196 |
| `session_bound_connect_projects_v1_participants_into_workbench_shape` | `bound_connection` | 2238 |
| `user_bound_connect_can_subscribe_multiple_sessions` | `subscriptions` | 2279 |
| `session_bound_connect_authorizes_once_with_immutable_binding` | `bound_connection` | 2323 |
| `session_bound_chat_send_cannot_escape_bound_scope` | `bound_messages` | 2384 |
| `session_bound_chat_send_uses_bound_human_identity` | `bound_messages` | 2429 |
| `session_bound_chat_abort_passes_only_the_bound_session` | `bound_messages` | 2468 |
| `session_bound_chat_abort_scope_mismatch_closes_connection` | `bound_messages` | 2503 |
| `session_bound_revoked_access_closes_with_stable_error` | `bound_connection` | 2546 |
| `chat_send_loads_the_live_ws_upgrade_ingress_into_web_send` | `ingress_identity` | 2609 |
| `owned_bot_chat_send_does_not_carry_human_ingress` | `ingress_identity` | 2669 |
| `upgraded_login_window_does_not_expire_the_connection_identity` | `ingress_identity` | 2711 |
| `the_adapter_sets_no_expiry_and_defense_belong_to_the_seam` | `ingress_identity` | 2756 |
| `display_only_identity_without_ingress_stays_caller_less` | `ingress_identity` | 2809 |

### F4

| 原测试函数 | 目标主题 | 声明行 |
| --- | --- | ---: |
| `list_bots_filters_paginates_and_maps_dtos` | `queries` | 386 |
| `get_bot_returns_detail_dto_and_not_found_error` | `queries` | 471 |
| `get_bot_rejects_private_bot_owned_by_other_user` | `queries` | 526 |
| `get_bot_reports_effective_active_status_for_connected_online_bot` | `queries` | 553 |
| `provider_http_bot_query_views_are_active_without_ws_connection` | `queries` | 583 |
| `extended_query_methods_page_creator_and_query_by_ids` | `queries` | 642 |
| `my_bots_active_only_filters_runtime_active_and_ignores_hidden` | `queries` | 716 |
| `legacy_my_bots_expose_the_sampled_access_relation` | `queries` | 776 |
| `discover_bots_applies_visibility_and_friend_matrix` | `discovery` | 846 |
| `discover_bots_matches_one_skill_exactly_ignoring_case` | `discovery` | 917 |
| `discover_bots_requires_all_exact_skills` | `discovery` | 953 |
| `discover_bots_combines_q_and_all_skills` | `discovery` | 993 |
| `discover_provider_bots_returns_provider_metadata_and_agent_code` | `discovery` | 1037 |
| `organization_scoped_discovery_filters_effective_members_and_attaches_metadata` | `organization_discovery` | 1146 |
| `organization_scoped_discovery_rejects_role_without_org_and_nonmember_requester` | `organization_discovery` | 1264 |
| `organization_scoped_discovery_keeps_members_after_provider_state_changes` | `organization_discovery` | 1290 |
| `get_visibility_applies_read_policy_and_normalizes_visibility` | `authority_writes` | 1423 |
| `connect_bot_preserves_provided_bot_id_and_reconnect_token` | `connection` | 1484 |
| `connect_bot_rejects_unknown_reserved_human_bot_id` | `connection` | 1518 |
| `connect_bot_allows_existing_human_actor_id_to_reach_registry` | `connection` | 1539 |
| `connect_bot_preserves_registry_conflict_error_class` | `connection` | 1565 |
| `bot_runtime_connection_service_manages_streaming_lifecycle` | `connection` | 1594 |
| `streaming_client_kind_requires_server_profile_and_is_replaced_on_reconnect` | `connection` | 1649 |
| `update_status_echoes_payload_without_retaining_it` | `status` | 1723 |
| `update_status_preserves_legacy_false_for_missing_self_target` | `status` | 1769 |
| `update_status_rejects_missing_caller` | `status` | 1797 |
| `update_status_rejects_caller_mismatch` | `status` | 1828 |
| `business_writes_admit_the_current_manager` | `authority_writes` | 1860 |
| `leave_bot_allows_owner_soft_delete_for_unmanaged_bot` | `authority_writes` | 1938 |
| `leave_bot_rejects_bot_token_provider_managed_and_tc_style_deletes` | `authority_writes` | 1987 |
| `set_visibility_rejects_invalid_values` | `authority_writes` | 2049 |
| `set_visibility_updates_registry_for_valid_value` | `authority_writes` | 2083 |
| `set_visibility_rejects_non_owner_when_owner_is_known` | `authority_writes` | 2117 |
| `set_visibility_rejects_missing_caller` | `authority_writes` | 2152 |
| `set_visibility_rejects_ownerless_bot_for_non_self_caller` | `authority_writes` | 2181 |
| `discovery_ignores_heartbeat_summary_and_keeps_static_metadata` | `discovery` | 2269 |
| `authenticated_connect_cannot_switch_body_identity` | `connection` | 2287 |

### F5

| 原测试函数 | 目标主题 | 声明行 |
| --- | --- | ---: |
| `member_detail_includes_flattening_source_data_and_agent_code` | `membership` | 125 |
| `member_detail_keeps_member_when_registered_bot_is_missing` | `membership` | 171 |
| `organization_services_pass_conformance_contracts` | `conformance` | 256 |
| `organization_management_authorizes_invocable_member_for_owning_manager` | `conformance` | 276 |
| `put_member_allows_own_and_granted_provider_bots_and_restores_disabled_member` | `membership` | 307 |
| `update_member_profile_merges_allowed_fields_and_preserves_omitted_fields` | `member_profile` | 371 |
| `update_member_profile_allows_an_authorized_cross_provider_member` | `member_profile` | 428 |
| `update_member_profile_rejects_disabled_member_without_mutating_bot` | `member_profile` | 464 |
| `update_member_profile_rejects_revoked_cross_provider_grant` | `member_profile` | 503 |
| `update_member_profile_rejects_wrong_manager` | `member_profile` | 553 |
| `update_member_profile_rejects_disabled_binding_and_deleted_bot` | `member_profile` | 587 |
| `put_member_rejects_ungranted_provider_bot` | `membership` | 653 |
| `put_member_rejects_disabled_organization_before_bot_checks` | `membership` | 675 |
| `put_member_rejects_missing_and_deleted_bots` | `membership` | 705 |
| `organization_management_rejects_wrong_manager_and_bad_auth` | `membership` | 738 |
| `validation_rejects_invalid_code_invalid_role_and_patch_without_fields` | `membership` | 760 |
| `candidate_bots_include_manager_and_granted_bots_without_leaking_ungranted_provider_ids` | `candidates` | 805 |
| `candidate_bot_page_is_sorted_and_counts_before_paging` | `candidates` | 887 |
| `candidate_bot_page_excludes_active_members_before_paging` | `candidates` | 915 |
| `candidate_bot_detail_reports_active_and_disabled_membership` | `candidates` | 953 |
| `scoped_a2a_authorizes_pair_after_cross_provider_grant_revocation` | `runtime_membership` | 1003 |
| `effective_membership_rejects_disabled_org_disabled_member_and_nonmember_sender` | `runtime_membership` | 1062 |
| `runtime_member_list_keeps_active_members_after_provider_grant_revocation` | `runtime_membership` | 1117 |
| `scoped_a2a_ignores_disabled_binding_and_rejects_missing_org` | `runtime_membership` | 1188 |
| `scoped_a2a_allows_sender_to_target_itself_with_one_member_record` | `runtime_membership` | 1231 |
| `member_page_for_manager_forwards_query_to_repository_page` | `candidates` | 1253 |

## 本次文档修订验证记录

- 已核对上述 target 对应实际 Cargo metadata；静态映射覆盖四个入口的全部 161 个直接测试。
- 已验证所有相对 Markdown 链接、全部 Bash 代码块语法，以及 F1 原 public handler/DTO 的导出覆盖。
- 已实际运行尺寸脚本用例：空文件、两个合规文件合计超过 1000、1000 行边界、1001 行无尾换行、
  带空格路径、缺失路径、空目录；另在临时 Git 仓验证 staged/untracked/modified 发现及历史超限排除。
  原五文件基线检查如预期返回 1；这些是文档内工具的验证，不是产品实现完成。
- 原稿内部 workspace 方案已被本公共 BCS 方案替代，不能沿用原稿的测试通过/工具环境结论。
- 本次只修订方案；Rust 实施、实际测试发现、测试执行、覆盖率与 Singlebox 尚未进行。
  （该节为文档修订时期的验证记录；实际实施结果见下文执行记录。）

## 实施执行记录（2026-11-12）

- **状态**：实施完成；集成门禁未全部通过（unit pass-rate 99.98%、changed-line
  66.08%、Singlebox 未验收）。尺寸、测试清单、单元测试（四个目标 crate）与 Cargo
  metadata 已在本地通过；门禁明细见文末「集成门禁」。
- **基线**：`b98b580a870050eebcd9d98602d45c9553d1d1e4`。实施按 F5 → F4 → F2 → F3 → F1
  推进，每个目标单独提交，未改动 dependencies、Cargo.lock、schema、业务逻辑和测试属性。

### 提交

| 提交 | 类型 | 标题 |
| --- | --- | --- |
| `03b469c728` | docs | docs(bcs): spec+plan for splitting five 1000-line Rust files (dima 2026111200120552113) |
| `b98b580a87` | docs | docs(bcs): correct public BCS file-splitting scope and validation plan |
| `5268a2dae7` | refactor | refactor(bcs): split organization management tests by theme |
| `a5cfe9ce45` | refactor | refactor(bcs): split bot use case tests by theme |
| `50e537ae03` | refactor | refactor(bcs): split bot frame compatibility tests by theme |
| `28cc19274b` | refactor | refactor(bcs): split workbench frame compatibility tests by theme |
| `e811eac9ca` | refactor | refactor(bcs): split session route handlers by responsibility |

实施基线 `b98b580a87` 之后共 5 个 Rust 提交；`03b469c728` 是同一工单的最初文档提交。
`$SOURCE_BASE` 为 `b98b580a87`，因此范围 diff 只含 5 个 Rust 提交。

### 最终行数

`python3 "$EVIDENCE/check_size.py"` 默认模式（五个入口 + 相对 `$SOURCE_BASE` 的全部
新增/修改 `.rs`）返回 0，共 40 个文件，全部 ≤1000。`$EVIDENCE` 为 Task 0 的临时证据目录
`/tmp/avernet-bcs-split.TLdfXs`，不进入仓库。

**F1 `/` 根**（原入口保留为兼容入口，子模块按职责拆分）

| 文件（相对仓库根） | 行数 |
| --- | ---: |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs` | 245 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/creation.rs` | 198 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/queries.rs` | 293 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/lifecycle.rs` | 277 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/participants.rs` | 668 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/chat.rs` | 241 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/history.rs` | 347 |
| `apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/collections.rs` | 140 |

**F2 `frame_compat`**

| 文件（相对仓库根） | 行数 |
| --- | ---: |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs` | 809 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/connection.rs` | 201 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/diagnostics.rs` | 98 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/v3_events.rs` | 374 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/interactions.rs` | 635 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/task_dispatch.rs` | 145 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/coordination_echo.rs` | 551 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/chat_events.rs` | 239 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/tracing.rs` | 191 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat/run_aliases.rs` | 211 |

根 809 行高于建议的 800 行目标，属于计划已明确接受的例外（设计估算约 880 行，
实际更小），仍低于 1000 行硬上限；根保留 `task_rpc_scope` 声明与其他共享设施。

计划指定的主题名 `tracing` 会遮蔽 bcs-ws 测试根中的同名 extern crate，因此相对纯移动
恰有 2 行改为绝对路径：根的 `use ::tracing::instrument::WithSubscriber;` 和
`frame_compat/diagnostics.rs` 的 `::tracing::dispatcher::get_default`（同一 crate、
同一符号）；另在 `mod tracing;` 挂载处加 2 行注释说明该遮蔽风险。

**F3 `web_frame_compat`**

| 文件（相对仓库根） | 行数 |
| --- | ---: |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat.rs` | 624 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/diagnostics.rs` | 176 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/subscriptions.rs` | 281 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/chat_send.rs` | 462 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/abort.rs` | 365 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/interactions.rs` | 298 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/bound_connection.rs` | 262 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/bound_messages.rs` | 163 |
| `apps/bcs/crates/adapters/ws/bcs-ws/tests/web_frame_compat/ingress_identity.rs` | 235 |

**F4 `bot_use_cases`**

| 文件（相对仓库根） | 行数 |
| --- | ---: |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases.rs` | 450 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/queries.rs` | 461 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/discovery.rs` | 318 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/organization_discovery.rs` | 278 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/connection.rs` | 255 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/status.rs` | 138 |
| `apps/bcs/crates/services/bcs-bot/tests/bot_use_cases/authority_writes.rs` | 417 |

**F5 `management`**

| 文件（相对仓库根） | 行数 |
| --- | ---: |
| `apps/bcs/crates/services/bcs-organization/tests/management.rs` | 190 |
| `apps/bcs/crates/services/bcs-organization/tests/management/membership.rs` | 291 |
| `apps/bcs/crates/services/bcs-organization/tests/management/conformance.rs` | 52 |
| `apps/bcs/crates/services/bcs-organization/tests/management/member_profile.rs` | 283 |
| `apps/bcs/crates/services/bcs-organization/tests/management/candidates.rs` | 242 |
| `apps/bcs/crates/services/bcs-organization/tests/management/runtime_membership.rs` | 251 |

挂载位置的既有约定：F2/F3 的 `#[path]` 挂载块放在既有 `mod task_rpc_scope;` /
`mod support;` 声明旁；F4/F5 根文件没有既有 mod 声明，挂载块位于文件末尾（机械插入
锚点，已在 Task 1 记录中说明）。

### 测试清单与映射

- 161 个直接测试一一映射：F2 58、F3 40、F4 37、F5 26；映射表逐行记录
  `old_name -> new_name`，见 `$EVIDENCE/mapping-161.txt`。
- F2 的 3 个 `task_rpc_scope::*` 测试未迁移、名称逐字节不变。
- ignored 集合保持同一 14 个测试（全部在 F2 `coordination_echo` 主题），迁移后仍为 ignored。
- 其余测试目标名称集合逐个 suite 对比完全一致；bcs-http 的 666 个测试名逐字节相同
  （F1 不移动测试）。
- 证据：四个 crate 的 `*.before.json` / `*.after.json`、`mapping-161.txt`、
  `task6.mapping-audit.log`（`RESULT: PASS`）。

### 单元测试结果

| crate | 结果 |
| --- | --- |
| `bcs-http` | 666 passed / 0 failed / 0 ignored |
| `bcs-ws` | 287 passed / 0 failed / 14 ignored（共 301） |
| `bcs-bot` | 299 passed / 0 failed / 0 ignored |
| `bcs-organization` | 26 passed / 0 failed / 0 ignored |

以上为**四个目标 crate 的范围**：合计 1278 passed、0 failed，与 Task 0 基线完全一致
（基线记录见 `$EVIDENCE/cargo-test-totals.txt`）。workspace 全量 unit gate 为
8237 tests / 8235 passed / 2 failed = **99.98%**；两个预存失败与本工单无关，
详见「集成门禁 / unit 与覆盖率」。不能把四个 crate 的 100% 当作 workspace 门禁结论。

### Cargo metadata 与 targets

- 四个受影响 crate 的 `targets[]`（name/kind/src_path）before/after 完全一致：
  `bcs-http` 11、`bcs-ws` 15、`bcs-bot` 35、`bcs-organization` 2。
- workspace 113 个 member 前后一致；全 workspace 526 个 test/bench target 前后一致，
  没有新增 integration test binary。
- 四个 crate 的依赖列表（name/req/kind/optional/default-features）前后一致。
- 证据：`$EVIDENCE/metadata.before.json`、`metadata.after.json`、
  `task6.metadata-compare.txt`。

### 豁免处理

- 从 `apps/bcs/docs/waivers/agentpass-subbots-source-size.md` 的活动表移除
  F1 `sessions.rs`、F2 `frame_compat.rs`、F4 `bot_use_cases.rs`、F5 `management.rs`
  四行，并在文末追加完成记录（最终行数 + 拆分提交）。
- header 字段、`expiry: 2026-11-12`、其他活动条目和 client waiver 均未改动；
  该 waiver 未被整体关闭。
- `bash scripts/ci/check-waivers.sh`（CWD=`apps/bcs`）exit 0；从仓库根直接运行会
  exit 77=SKIP，SKIP 不计为通过。证据：`$EVIDENCE/task6.check-waivers.log`、
  `task6.check-waivers.root-cwd.log`。

### 观察（不在本工单范围，未修改）

- waiver 活动表中 `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` 行指向的路径已不存在，
  实际文件为 `src/web/dispatcher.rs`（1010 行）。
- 若干活动条目在本次基线时已 ≤1000 行（`providers.rs` 956、`server.rs` 985、
  `bcs-bot-store/src/lib.rs` 687、`memory.rs` 933、`onboarding.rs` 469、
  `provider_core.rs` 854），而 `removal_plan` 要求 ≤1000 后移除。
- 全仓另有历史超限文件（如 `ws/src/gateway/chat_handler.rs` 1045、
  `ws/src/web/dispatcher.rs` 1010）不属本计划引入，未扩大清理范围。

### 集成门禁

门禁在文档提交 `68a6e84af7` 上执行；最终提交只追加本节记录，不改 Rust、配置或
验收范围，因此按计划无需重跑受影响检查。证据日志均位于 Task 0 的 `$EVIDENCE`。

#### arch-check

`BCS_BASE_REF="$SOURCE_BASE" PR_BODY_FILE="$EVIDENCE/pr-body.md" bash apps/bcs/scripts/ci/arch-check.sh`
在 HEAD 上 **exit 5**：`Total: 15 Pass: 9 Skip: 1 Fail: 5`，`Score: 55/100`（skip 不计分）。
同一命令在 `$SOURCE_BASE` 临时 worktree（`b98b580a87`）上也是 **exit 5**，逐子检查状态与
失败清单一致：

| 检查 | HEAD | 基线 `b98b580a87` | 说明 |
| --- | --- | --- | --- |
| DEP-1~8 | FAIL | FAIL | DEP-1 ×2、DEP-6；DEP-8 SKIP（cargo-machete 未安装） |
| LINT-1 | FAIL | FAIL | 4 处匹配集合相同（adapters/services 源码，非本次文件） |
| LINT-2 | PASS | PASS | — |
| LINT-3 | PASS | PASS | — |
| CFG-1 | PASS | PASS | 159 个 bootstrap config 测试通过 |
| R10 | PASS | PASS | — |
| R12 | PASS | PASS | — |
| LINT-4 | FAIL | FAIL | 10 处命名匹配相同 |
| TEST-1 | FAIL | FAIL | R25.1/R25.2 清单 194 行，集合相同 |
| TEST-2 | PASS | PASS | — |
| PR-1 | PASS | PASS | 未修改契约 crate |
| WAIVER-1 | PASS | PASS | 从 `apps/bcs` 运行，真正校验而非 SKIP |
| API-1 | SKIP | SKIP | cargo-public-api 未安装；SKIP 不计为通过 |
| R25 | FAIL | FAIL | R25.1/R25.2/R25.3，清单集合相同 |
| BASELINE | PASS | PASS | base=1/1/9，head=1/1/9 |

逐子检查清单按多重集比较：除 `rg` 在另一 worktree 的文件枚举顺序、以及 CFG-1 的
cargo test 进度/耗时文本外，**没有任何新增失败或新增条目**。聚合 55 分与 exit 5 来自
既有问题；本次未把 SKIP 当覆盖，也未通过路径排除或豁免判绿。

#### unit 与覆盖率

| 命令 | 退出码 | 结果 |
| --- | ---: | --- |
| `ci_test.sh --fast-fail --coverage --base $SOURCE_BASE --head HEAD` | 100 | 跑到 2550/8237 因 2 个失败 fail-fast 中止 |
| `ci_test.sh --coverage --base $SOURCE_BASE --head HEAD` | 0（脚本设计：非 fast-fail 不阻断） | 8237 tests：8235 passed、2 failed、67 skipped（99.98%） |
| `python3 scripts/cov_gate.py --base-ref $SOURCE_BASE ... --pass-rate-min 100 --overall-line-min 87 --changed-line-min 85 --changed-line-min-lines 51` | 1 | 见下 |

`cov_gate.py` 实测：pass-rate 99.98%（<100%，FAIL）；overall line **87.26%**（>87%，OK）；
changed-line **931/1409 = 66.08%**（<85%，FAIL）。changed 行全部落在 F1 的 8 个生产文件
（测试文件无 instrumentable 行），是这些既有 handler 的历史覆盖水平被“纯移动”计入
changed 后暴露出来，不是本次新增逻辑；本次未新增、未删除任何测试。

两个失败均为 `bcs-bot-store::conformance_bot_control_plane_repo` 的
`cases_2::persistent_capability_save_preserves_patched_internal_attributes` 与
`cases_3::memory_control_plane_list_by_task_modes_filters_visibility_status_user_visibility`。
在 `$SOURCE_BASE` worktree 用同一 test target 复现（22 passed / 2 failed，同样两项），
确认是**基线既有失败**，与本次五个目标 crate 无关；四个受影响 crate 本身仍是
bcs-http 666、bcs-ws 287+14 ignored、bcs-bot 299、bcs-organization 26，全部通过。

结论：unit 100% 与 changed-line >85% 两项门槛**未通过**；不降低门槛，不把既有失败
算作本次通过。changed-line 是**确定性的 diff 计算**：PR CI（`unit-tests.yml`）运行
同一 `cov_gate.py`、同一阈值（>85%、≥51 行），会在**同一提交**上同样失败；要达到 85%
需再覆盖约 267 行（1409 × 0.85 − 931 ≈ 267），或由 owner 给出显式例外，不能靠“CI
复验”消除。相较之下，workspace unit pass-rate 的 2 个失败属既有测试问题，仍须在同一
提交上补齐通过证据，但结果可能随环境不同。Singlebox 才适合“CI 对同一提交补验”。
另注：E2E changed-line 门槛是 >65%；若其 scope 同为这 1409 行，66.08% 仅高出约 1.08pp。
本工单不做超出“纯拆分”范围的改动。

#### Singlebox

- 默认 coverage root：`stack_start` exit=1。原因：既有 OpenClaw bot profiles
  （`ceo`、`product-manager`、`engineering`、`verification`、`customer-service`）属于
  另一个本地栈，端口/BCS URL/workspace 与本次预期不符；脚本拒绝覆盖。
- 改用隔离 root（`--coverage-root $EVIDENCE/singlebox-coverage`）后：`stack_start`
  exit=1，原因是 8000 端口被**本仓库之外另一 checkout 的前端进程**占用；
  singlebox 脚本拒绝停止外部进程。
- `verify_singlebox_coverage_artifacts.py` 在两个 root 上均 exit 1（缺少
  `summary.json` 等必需产物）。

Singlebox 因此列为**未验收（NOT passed）**：属本机环境冲突（陈旧本地栈 + 外部端口占用），
不是工具缺失或代码失败。需要干净环境（或由用户确认清理隔离 bots 并释放 8000 端口）
重跑，或由 CI 对同一最终提交补验；未使用任何陈旧（2026-11-06）产物冒充结果。

### 回滚与范围

回滚按目标文件单元（入口 + 主题文件 + 接线）进行，无 schema/config/数据迁移。
本计划完成只覆盖这五个文件；其他豁免条目和全仓历史超限文件不受影响。

### 交付清单与最终状态

- 相对 `$SOURCE_BASE` 的范围：**43 个文件 = 40 个 `.rs`（35 新增 + 5 修改）+ 3 个
  Markdown**（server waiver、plan、spec）。清单见 `$EVIDENCE/task6.inventory.after-docs.txt`。
- 文档提交：`68a6e84af7`（执行记录与豁免清理）；其后的证据提交只追加本节及门禁结果，
  不改 Rust、配置或验收范围。
- **最终状态：实施完成，但集成门禁未全部通过**——unit pass-rate 99.98%（2 个基线既有
  失败）、changed-line 66.08%（<85%）、Singlebox 未验收。因此本计划不标记为“完成”，
  也未声明可合并。
- 未执行 push、未创建 PR（由 controller 在用户确认后处理）；未绕过 hooks，未设置任何
  门禁旁路环境变量。pushed 之前的最终裁决需要：处理 2 个既有失败与 changed-line 门槛，
  并在干净环境补 Singlebox 证据。
