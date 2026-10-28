# BCS Bot Event Contract Test Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `contract_bot_event.rs` 的 108 个既有测试与 fixture 按职责拆分，保留测试行为和 Cargo target，使每个新增、修改 Rust 文件均不超过 1,000 物理行。

**Architecture:** 保留原集成测试入口和两个外部 support 模块身份，新增 14 个场景子模块及套件私有 fixture 子树。以 AST 精确搬运既有声明和属性；仅为跨测试模块实际访问的 helper 增加祖先范围可见性，不修改生产实现或新增公共 API。

**Tech Stack:** Rust 2024、Cargo、Tokio tests、既有 MessageFlow contract fixtures；临时 Python 3.10+ / tree-sitter 工具仅用于源码核对与搬运，不进入 Cargo 或仓库依赖。

---

## 0. 授权范围、依据与完成定义

- 设计：[BCS 超长 Rust 文件拆分设计](../specs/2026-10-28-bcs-large-rust-files-decomposition-design.md)，第 5、9、10 节。
- 当前计划只覆盖设计的 **第 1 单元**；不顺带拆 CLI、Runtime、Runtime Progression 或 Store。
  后四个单元分别编写实施计划，不把五个独立 PR 堆成一个执行任务。
- 源码基准：`dd316eb35`；规划时 HEAD 为文档提交 `d5cc8a5d0`，Rust 源码未变。
- 原文件：`apps/bcs/crates/services/bcs-message-flow/tests/contract_bot_event.rs`，7,186 行。
- 原文件 SHA-256：`ab4b5165b6de012fbe5e4c7088c5e833dcc5a0e4620d88fa3a1ea421d41d45ab`。
- 当前源码有 **108** 个测试，包含 `#[tokio::test(start_paused = true)]`。
  不能以只匹配 `#[tokio::test]` 的正则生成测试清单。
- 本计划的代码块是未来执行用的完整工具与命令，**编写计划不等于已执行重构或回归**。

完成必须同时满足：

1. 108 个测试叶名称不变，旧名称到 `场景模块::叶名称` 是一一映射，实际发现与执行均完整。
2. 原测试的函数体、属性、doc/comment 不变；保留暂停时钟等运行条件，不能新增 ignore。
3. helper/trait impl 的函数体和状态不变；仅增加本文列明的受限可见性。
4. 原入口、所有新 Rust 文件 <=1,000 行；目标、依赖、生产代码和其他 fixture 不变。
5. 目标套件及受影响 crate 通过，默认与 all-feature workspace 编译无新增 warning。
6. 适用架构/统一 Singlebox gate 结果可追踪；未运行或已知基线失败如实记录，不称为通过。

## 1. 文件职责和写入边界

以下代码路径相对 `apps/bcs/`。本节表格的目录是固定写入边界，不是待决定的建议。

| 操作 | 路径 | 职责 |
| --- | --- | --- |
| Modify | `crates/services/bcs-message-flow/tests/contract_bot_event.rs` | 仅原有导入、外部 support 装配、新场景/fixture 装配 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/publication.rs` | 6 个 Workbench/终态 observer/audience 测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/im_reaction.rs` | 3 个排队 reaction、重启防重和每 tick 成本测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/channel_delivery.rs` | 5 个 Channel final/delta/fallback 测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/thinking_stream.rs` | 6 个 thinking 缓冲及工具边界测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/group_relay.rs` | 10 个群/DM/私有路由、Provider tag 和 relay limit 测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_lifecycle.rs` | 9 个 ledger/alias/去重/任务状态测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/coordination_echo.rs` | 9 个 tool/MCP 映射和解析缓存测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_routing.rs` | 8 个 Manager/Worker、muted/membership/拦截器测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_result.rs` | 9 个 Manager 结果、上下文、去重与失败重试测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_stream.rs` | 9 个 SSE、工具分界、response mode 与 tags 测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_completion.rs` | 9 个完成 guard、跨 Session 和 Service Session 测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/task_history.rs` | 13 个任务消息、任务错误/终态及落库顺序测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/history_persistence.rs` | 7 个 owner、tool start 与非任务 segment 落库测试 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/human_notify.rs` | 5 个 Human 通知策略测试；保留两个仅此处使用的辅助函数 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support.rs` | 私有 fixture 装配与具名重导出，不新增状态 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support/channel.rs` | Channel/System/terminal observer 记录器 |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support/session.rs` | `test_session` 与 RecordingSessionManagement |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support/messages.rs` | RecordingMessageRepo、BlockingMessageRepo |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support/task_flow.rs` | ManagerWorker 组合工厂、Provider 配置、超时常量及共享 BlockingInterceptor |
| Create | `crates/services/bcs-message-flow/tests/contract_bot_event/support/frames.rs` | payload、cached start、frame 与 ledger 断言辅助 |

共修改 1 个、创建 20 个 Rust 文件。`task_history.rs` 是对 design 的职责细化，
避免把全部 history 场景集中回一个大文件，不新增测试或扩大业务范围。

保持只读：

- `crates/services/bcs-message-flow/src/**`、`Cargo.toml`、`Cargo.lock`。
- `crates/services/bcs-message-flow/tests/support/session.rs`。
- `crates/test-support/message_flow_contract_support.rs`。
- `crates/test-support/fixtures/baas_command_output_end_without_name.json`。
- 所有迁移、API、coverage 脚本/阈值及其他四个超长文件。

### 1.1 可见性与 fixture 生命周期

新 `support.rs` 在原 target 内命名为 `fixtures`，不占用既有 `support` 模块名。
其子模块相对 target 根深度为 2：需要从场景访问的类型、free helper 和 inherent method
使用 `pub(in super::super)`；`support.rs` 具名 `pub(super) use` 给 target 根。
不能错用仅对 fixture 父级可见的 `pub(super)`，然后靠扩大为 `pub(crate)` 解决编译错误。

只开放下列字段，其他记录器内部字段仍私有：

| 类型 | 原测试必须直接访问的字段 |
| --- | --- |
| RecordingSystemMessage | `notifications` |
| RecordingSystemNotification | `group_id`、`event`、`session_id`、`participants` |
| RecordingBotTerminalObserver | `events` |
| RecordingSessionManagement | `completed` |

`BlockingInterceptor` 同时用于 `task_routing::task_dispatch_blocking_interceptor_prevents_bot_delivery`
和 `human_notify::bot_event_policy_blocked_message_does_not_notify`，不是场景私有类型。
类型及唯一 trait impl 归入 `support/task_flow.rs`，类型使用 `pub(in super::super)`，
由 `fixtures` 具名重导出给两个场景；trait impl 的方法不加可见性。
`group_with_present_human`、`bot_event_with_text` 只在 `human_notify.rs` 内私有。
不复制 Arc、Barrier、Notify 或状态记录器；所有创建时刻、等待和释放操作不变。

## Task 1: 准备执行工作区并保存真实基线

**Files:** 只读原 Rust 文件、root/local AGENTS、CLAUDE、MessageFlow CONTEXT、design；
临时产物全部放 `$BCS_PLAN_WORKDIR`，不提交机器路径、日志或工具环境。

- [ ] **Step 1: 按 using-git-worktrees 技能检查已有隔离与用户偏好，选定执行目录。**

若创建 worktree，先按平台原生工具的返回结果完成附加；不要在当前规划分支直接执行。
在选定工作区确认无他人未提交修改；新分支由 Step 2 的命令创建，不在两处重复创建。
本计划不授权自动 push/建 PR；需要发布时遵循用户届时明确的流程。

- [ ] **Step 2: 在 Bash 会话建立可恢复的执行环境并固定原文件。**

以下代码块均在同一 Bash 会话运行。若工具调用不共享 shell，后续调用先 source
本步骤打印的环境文件。该文件同时恢复 `set -euo pipefail` 和 `cd "$BCS_ROOT"`，
不能只恢复变量，否则相对路径可能失效，`cargo test | tee` 也可能吞掉测试失败。
不重新生成 WORKDIR，不把其他任务的临时环境覆盖掉。

```bash
set -euo pipefail
export BCS_ROOT="$(git rev-parse --show-toplevel)/apps/bcs"
cd "$BCS_ROOT"
test -z "$(git status --porcelain)"
git switch -c refactor/bcs-bot-event-contract-modules
export BCS_PLAN_BASE="$(git rev-parse HEAD)"
export BCS_PLAN_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/bcs-bot-event-split.XXXXXX")"
{
  printf 'set -euo pipefail\n'
  printf 'export BCS_ROOT=%q\nexport BCS_PLAN_BASE=%q\nexport BCS_PLAN_WORKDIR=%q\n' \
    "$BCS_ROOT" "$BCS_PLAN_BASE" "$BCS_PLAN_WORKDIR"
  printf 'cd "$BCS_ROOT"\n'
} > "$BCS_PLAN_WORKDIR/env.sh"
printf 'Resume with: source %q\n' "$BCS_PLAN_WORKDIR/env.sh"
git show "$BCS_PLAN_BASE:apps/bcs/crates/services/bcs-message-flow/tests/contract_bot_event.rs" \
  > "$BCS_PLAN_WORKDIR/original.rs"
python3 - "$BCS_PLAN_WORKDIR/original.rs" <<'PY'
import hashlib, pathlib, sys
value = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
assert value == "ab4b5165b6de012fbe5e4c7088c5e833dcc5a0e4620d88fa3a1ea421d41d45ab", value
print("Baseline source fingerprint matches approved plan")
PY
```

如果 source fingerprint 不匹配，停止自动搬运，重新核对 108 项清单和设计后再更新计划。
不要仅修改 hash 让检查通过。

恢复文件生成后，在新的 Bash 进程中验证工作目录、严格选项及失败传播。
第二个进程故意返回 101；只有确认该退出码且后续标记未执行，才能继续保存基线。

```bash
python3 - "$BCS_PLAN_WORKDIR/env.sh" <<'PYTHON'
import subprocess, sys
resume = sys.argv[1]
subprocess.run([
    "bash", "--noprofile", "--norc", "-c",
    'source "$1"; [[ "$PWD" -ef "$BCS_ROOT" ]]; '
    '[[ -o errexit && -o nounset && -o pipefail ]]',
    "bash", resume,
], cwd="/", check=True)
failed = subprocess.run([
    "bash", "--noprofile", "--norc", "-c",
    'source "$1"; (exit 101) | tee /dev/null; echo SHOULD_NOT_RUN',
    "bash", resume,
], cwd="/", capture_output=True, text=True)
assert failed.returncode == 101, failed.returncode
assert "SHOULD_NOT_RUN" not in failed.stdout, failed.stdout
print("PASS: fresh Bash restores cwd/options and propagates pipeline failure")
PYTHON
```

- [ ] **Step 3: 保存工具链、测试清单和完整目标基线。**

```bash
cd "$BCS_ROOT"
{ git rev-parse HEAD; rustc --version; cargo --version; } > "$BCS_PLAN_WORKDIR/environment.txt"
cargo test -p bcs-message-flow --test contract_bot_event -- --list \
  > "$BCS_PLAN_WORKDIR/baseline-list.txt" 2> "$BCS_PLAN_WORKDIR/baseline-list.stderr"
cargo test -p bcs-message-flow --test contract_bot_event \
  2>&1 | tee "$BCS_PLAN_WORKDIR/baseline-target.log"
cargo test -p bcs-message-flow --test contract_coordination_tool_identity \
  2>&1 | tee "$BCS_PLAN_WORKDIR/baseline-identity.log"
```

预期目标套件发现和运行 108 项，0 failed、0 ignored、无编译 warning；
identity 套件包含 nameless-result compatibility。任何实际差异先归因并记录，不能跳过失败。
本步骤不提交；原文件仍未改动。

## Task 2: 建立先红后绿的结构与行为保持检查

**Files:** 临时创建 `$BCS_PLAN_WORKDIR/moves.json`、`move_bot_event.py`；
不向仓库添加 AST 依赖、生成器或为清理服务的生产测试接口。

- [ ] **Step 1: 安装固定版本的临时 AST 工具。**

```bash
python3 -m venv "$BCS_PLAN_WORKDIR/tools"
"$BCS_PLAN_WORKDIR/tools/bin/python" -m pip install \
  'tree-sitter==0.26.0' 'tree-sitter-rust==0.24.2'
```

使用 Python 3.10+；依赖安装失败时报告工具阻塞，不改成用括号正则裁剪 Rust 函数。
后文完整附录 A 保存为 `moves.json`，完整附录 B 保存为 `move_bot_event.py`。
保存时去掉 Markdown fence，不改变代码内容；两者都只放临时目录。

- [ ] **Step 2: 先验证结构 gate 在原文件上为红灯。**

```bash
set +e
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify \
  > "$BCS_PLAN_WORKDIR/red-gate.log" 2>&1
red_status=$?
set -e
test "$red_status" -ne 0
grep 'Root exceeds 1000 physical lines' "$BCS_PLAN_WORKDIR/red-gate.log"
```

必须是超出 1,000 行的预期失败；ImportError、解析错误或清单遗漏不是合格红灯。
现有行为测试本来应是绿灯；本次结构重构不伪造一个业务失败，也不改断言制造红灯。

- [ ] **Step 3: 仅预览模块归属和行数，核对 fixture 访问边界。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" preview \
  | tee "$BCS_PLAN_WORKDIR/layout-preview.txt"
git status --short
```

该命令只在内存组装字节、输出行数，不写 Rust 文件。
规划基准预览：原入口 87 行，最大的初始模块 `task_stream.rs` 为 736 行，
全部 21 个文件低于 1,000 行。这里只是带初始 imports 的排布验证，不是编译已通过。

## Task 3: 一次搬运全部声明，保留精确函数体与属性

**Files:** 第 1 节固定的 21 个 Rust 文件。

- [ ] **Step 1: 检查 WORKDIR、原文件和目标目录，执行完整搬运。**

```bash
cd "$BCS_ROOT"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" apply
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify
```

工具拒绝覆盖已有目标目录或已经改变的入口；先完成所有 AST/清单/行数检查后再写文件。
它不使用 `include!` 或生成代码留在最终生产路径；最终提交的是正常手写可维护 Rust 模块。
这里的机械搬运工具不属于产品构建链，不提交。

原有 `support` 和 `session_support` 声明及其 `#[path]` 原样留在 target 根；
新场景通过根模块导入原类型、trait、macro 和 fixture 名称。
每个原声明有唯一新拥有者，工具拒绝未归类声明和重复测试名。

- [ ] **Step 2: 仅清理新 imports，不格式化或重写测试。**

初始搬运使用具名 imports 的保守全集，确保 trait method 和 macro 的可见性不因推断遗漏；
随后由 rustc 的机器可应用建议删除未使用导入，最终不保留宽泛 `use super::*`。

```bash
cargo fix -p bcs-message-flow --test contract_bot_event --allow-dirty --allow-staged \
  2>&1 | tee "$BCS_PLAN_WORKDIR/import-fix.log"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify
cargo test -p bcs-message-flow --test contract_bot_event --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/compile-target.jsonl" 2> "$BCS_PLAN_WORKDIR/compile-target.stderr"
python3 - "$BCS_PLAN_WORKDIR/compile-target.jsonl" <<'PY'
import json, pathlib, sys
messages = [json.loads(line) for line in pathlib.Path(sys.argv[1]).read_text().splitlines()]
warnings = [row["message"] for row in messages
            if row.get("reason") == "compiler-message" and row["message"]["level"] == "warning"]
assert not warnings, warnings
print("PASS: contract_bot_event compiled with zero compiler warnings")
PY
```

如果首轮只因删除子模块 imports 后使父 imports 变成 unused，再执行一次同一目标的
cargo fix 并复核。其他 warning 或非 import 修改必须先诊断，不能加 allow 或更改函数体。
不得运行 cargo fmt，也不得对整个 workspace 使用 cargo fix。

- [ ] **Step 3: 检查裸分支、记录器状态和访问范围，暂停进入提交。**

```bash
git diff -- crates/services/bcs-message-flow/tests/contract_bot_event.rs
git diff --check
```

逐个看新 `support/*.rs` 的所有可见性：类型、14 个共享 free helper、1 个常量、
被调用的 inherent 方法和第 1.1 节列出的字段之外，不扩大其他项。
trait impl 方法不允许加可见性修饰；BlockingInterceptor 保持唯一共享实现，
Human 专用 helper 保持场景私有。确认 task_routing 和 human_notify 均能访问共享拦截器；
AST 字节核对不检查 Rust 名称解析，必须同时通过上一阶段的实际编译。
原记录器的 Arc/RwLock/Barrier/Notify、方法体、超时值与 clone 次数必须一致。

此时不提交不完整中间态；先完成 Task 4 的真实发现和执行检查，再提交整个结构变更。

## Task 4: 证明测试发现完整、属性不变且单独/并发执行均有效

**Files:** 只执行新 target 与现有 identity 套件；日志放 WORKDIR。

- [ ] **Step 1: 比较实际 Rust 测试清单的一一映射。**

```bash
cargo test -p bcs-message-flow --test contract_bot_event -- --list \
  > "$BCS_PLAN_WORKDIR/after-list.txt" 2> "$BCS_PLAN_WORKDIR/after-list.stderr"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" list
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify
```

预期：108 ->108，原叶名称全部保留，每个 qualified name 与附录 A 的所属模块一致。
不仅比较总数；逐声明字节核对同时检查 `start_paused = true` 和所有原属性。

- [ ] **Step 2: 单独执行暂停时钟与持久化失败场景，防止过滤器零命中。**

```bash
cargo test -p bcs-message-flow --test contract_bot_event \
  im_reaction::reaction_delivery_is_bounded_to_one_provider_call_per_tick \
  -- --exact --nocapture 2>&1 | tee "$BCS_PLAN_WORKDIR/paused-clock.log"
cargo test -p bcs-message-flow --test contract_bot_event \
  history_persistence::agent_tool_result_retains_cached_start_when_persistence_fails \
  -- --exact --nocapture 2>&1 | tee "$BCS_PLAN_WORKDIR/failed-persistence.log"
grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$BCS_PLAN_WORKDIR/paused-clock.log"
grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$BCS_PLAN_WORKDIR/failed-persistence.log"
```

必须各运行 1 项，不接受只有退出码为 0。

- [ ] **Step 3: 分别执行单线程和默认并发的完整套件。**

```bash
cargo test -p bcs-message-flow --test contract_bot_event -- --test-threads=1 \
  2>&1 | tee "$BCS_PLAN_WORKDIR/after-serial.log"
cargo test -p bcs-message-flow --test contract_bot_event \
  2>&1 | tee "$BCS_PLAN_WORKDIR/after-parallel.log"
cargo test -p bcs-message-flow --test contract_coordination_tool_identity \
  2>&1 | tee "$BCS_PLAN_WORKDIR/after-identity.log"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import pathlib, sys
work = pathlib.Path(sys.argv[1])
for name in ["after-serial.log", "after-parallel.log"]:
    text = (work / name).read_text()
    assert "test result: ok. 108 passed; 0 failed; 0 ignored;" in text, name
    assert "warning:" not in text, name
print("PASS: 108 tests pass in serial and default parallel execution")
PY
```

不添加 sleep 稳定测试，不更换真实 port/fake，不降低断言或重新布置数据来修复差异。
若出现失败，对照 baseline 与字节检查定位，再修复模块装配/访问错误。

- [ ] **Step 4: 保存唯一完整重构提交。**

```bash
git add -- crates/services/bcs-message-flow/tests/contract_bot_event.rs \
  crates/services/bcs-message-flow/tests/contract_bot_event
git diff --cached --check
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify
git commit -m "refactor(bcs): split bot event contract tests by responsibility"
```

提交前确认 staged 文件仅为第 1 节固定的 21 项；不提交 WORKDIR、日志、缓存和临时工具。
所有原/新源文件已低于上限，不创建“原入口仍然超限、下个 PR 再拆”的半成品。

## Task 5: crate、workspace、架构和统一 E2E 回归

**Files:** 不再新增源码；只有经证据确认的装配修正可以回到 Task 3，并完整重跑 Task 4。
命令可能超过 5 分钟；作为验证检查点执行，不反复忙轮询。

- [ ] **Step 1: 运行受影响 crate 和默认/all-feature workspace 编译。**

```bash
cargo test -p bcs-message-flow 2>&1 | tee "$BCS_PLAN_WORKDIR/crate-tests.log"
cargo build 2>&1 | tee "$BCS_PLAN_WORKDIR/build.log"
cargo test --workspace --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/workspace-default.jsonl" 2> "$BCS_PLAN_WORKDIR/workspace-default.stderr"
cargo test --workspace --all-features --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/workspace-all-features.jsonl" 2> "$BCS_PLAN_WORKDIR/workspace-all-features.stderr"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json, pathlib, sys
work = pathlib.Path(sys.argv[1])
for name in ["workspace-default.jsonl", "workspace-all-features.jsonl"]:
    rows = [json.loads(line) for line in (work / name).read_text().splitlines()]
    warnings = [row["message"] for row in rows
                if row.get("reason") == "compiler-message" and row["message"]["level"] == "warning"]
    assert not warnings, (name, warnings)
print("PASS: default and all-feature workspace compilation have zero compiler warnings")
PY
```

- [ ] **Step 2: 跑现有架构入口和完整 workspace 测试，记录真实退出码。**

```bash
set +e
bash scripts/ci/arch-check.sh > "$BCS_PLAN_WORKDIR/architecture.log" 2>&1
arch_status=$?
cargo test --workspace --no-fail-fast > "$BCS_PLAN_WORKDIR/workspace-tests.log" 2>&1
workspace_status=$?
set -e
printf 'architecture_exit=%s\nworkspace_exit=%s\n' "$arch_status" "$workspace_status" \
  | tee "$BCS_PLAN_WORKDIR/final-status.txt"
```

这里只是保留失败证据，不把非零转换成通过。若存在既有失败，必须在同工具链的干净
baseline worktree 复现并对比；不能凭 #3209 的历史两个失败直接认定当前失败也是基线。
不得改无关代码、基线 allowlist 或 nextest 重试配置。

- [ ] **Step 3: 从仓库根执行统一 Singlebox coverage 并校验当前工件。**

```bash
cd "$(git rev-parse --show-toplevel)"
git fetch origin dev
export SINGLEBOX_BASE_REF="$(git merge-base origin/dev HEAD)"
export BCS_E2E_CHANGED_BASE_REF="$SINGLEBOX_BASE_REF"
export BCS_E2E_CHANGED_LINE_MIN="$(python3 -c 'import os; print(max(60.0, float(os.environ.get("BCS_E2E_CHANGED_LINE_MIN", "60"))))')"
export BCS_E2E_CHANGED_LINE_MIN_LINES="$(python3 -c 'import os; print(min(20, int(os.environ.get("BCS_E2E_CHANGED_LINE_MIN_LINES", "20"))))')"
printf 'Singlebox merge-base: %s\n' "$SINGLEBOX_BASE_REF"
bash singlebox/ci/singlebox_coverage.sh \
  2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports \
  2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox-artifacts.log"
cd "$BCS_ROOT"
```

必须使用执行时真实 PR merge-base 作为 changed-line 对比基线，核对 runner 的解析结果；
不能靠上面两个环境变量替代基线设置或覆盖已有更严格门槛。
本单元仅移动测试，正常 E2E instrumentation 不应凭搬运就产生生产 changed-line 分母；
若报告出现，先排查范围/旧工件/意外生产 diff，不通过新排除项消除。
Line >=41%、Method >=36%、HTTP/CLI coverage 100%，及适用的严格 changed-line 要求均保留。

无可用端口或外部工具时，记录具体未运行原因，不能杀其他 worktree 的服务。
本地未能执行的必需门禁须有可追踪远端结果后才能称为最终验收通过。

## Task 6: 自检、证据汇总与交接

**Files:** 只读 source/diff；验证证据保存在 WORKDIR，并写入最终 PR 的 Validation。

- [ ] **Step 1: 运行最终不变性和源码范围检查。**

```bash
cd "$BCS_ROOT"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" verify
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_bot_event.py" list
git diff "$BCS_PLAN_BASE" HEAD --check
python3 - <<'PY'
import os, pathlib, subprocess
base = os.environ["BCS_PLAN_BASE"]
files = subprocess.check_output(
    ["git", "diff", "--name-only", "--relative", base, "HEAD"], text=True
).splitlines()
root = "crates/services/bcs-message-flow/tests/contract_bot_event.rs"
directory = root[:-3] + "/"
assert len(files) == 21, files
assert all(path == root or path.startswith(directory) for path in files), files
assert all(len(pathlib.Path(path).read_bytes().splitlines()) <= 1000 for path in files)
assert not subprocess.check_output(["git", "status", "--porcelain"], text=True)
print("PASS: 21 scoped Rust files, no unrelated diff, all <=1000 lines, clean tree")
PY
```

- [ ] **Step 2: 审查结构与契约，不重复机械搬运。**

检查 import/visibility、保留的外部 support 路径、108 项一一映射、暂停时钟属性、
所有 trait impl 与 helper 的字节比对、fixture 生命周期和无 production diff。
遵循 `/tmp/CODE_REVIEW.md`（若存在）及 requesting-code-review 技能。
不得仅以测试数量相等或“重构不改逻辑”替代审查。

- [ ] **Step 3: 汇总真实结果，等待发布指令。**

Validation 必须列：环境/BASE、原文件 hash、108 项实际测试发现、单项命中数、
单线程/并发结果、fixture 访问范围、逐文件行数、Cargo diagnostics、crate/workspace
失败情况、架构结果、Singlebox run/artifact 位置、明确未验证项。

遇到任何失败，保留其日志和退出码；不替换为旧 run、重试后的“最终绿灯”或 report-only 成功。
本单元可通过 revert 自己的重构提交回滚；不要 hard reset/清理他人的工作区。

---

## 附录 A：108 项测试的唯一迁移清单

将下面完整 JSON 保存到 `$BCS_PLAN_WORKDIR/moves.json`。
键为新模块名，值为原测试叶名称；每项只出现一次。

```json
{
  "publication": [
    "websocket_chat_events_notify_terminal_observer_only_for_terminal_states",
    "bot_event_publish_preserves_workbench_event_names",
    "bot_event_publish_carries_legacy_run_fallback",
    "bot_event_with_bcs_session_id_publishes_to_session_target",
    "manager_worker_bot_events_publish_with_participant_safe_audiences",
    "state_machine_group_manager_chat_is_public_in_realtime_and_history"
  ],
  "im_reaction": [
    "queued_im_reaction_targets_source_message_and_does_not_replay_on_restart",
    "expired_im_reaction_waits_until_no_target_remains_queued",
    "reaction_delivery_is_bounded_to_one_provider_call_per_tick"
  ],
  "channel_delivery": [
    "unmanaged_final_preserves_publication_and_legacy_group_fallback",
    "queue_reconstruction_never_rewrites_channel_or_frontend_final",
    "bot_delta_channel_outbound_uses_delta_text_not_synthesized_snapshot",
    "bot_final_channel_outbound_resolves_missing_sender_name",
    "bot_terminal_failures_emit_safe_system_channel_feedback"
  ],
  "thinking_stream": [
    "agent_thinking_delta_self_accumulates_and_resets_after_tool_start",
    "agent_thinking_delta_resets_after_tool_end",
    "agent_thinking_delta_resets_after_non_tool_block",
    "agent_thinking_delta_buffers_are_isolated_by_run_id",
    "non_agent_stream_event_does_not_reset_thinking_delta_buffer",
    "terminal_cleanup_clears_thinking_delta_buffer_for_reused_run_id"
  ],
  "group_relay": [
    "bot_final_event_for_synthetic_session_skips_group_relay",
    "bot_final_event_in_human_bot_dm_does_not_self_relay_to_sender_bot",
    "bot_final_event_relays_through_bot_delivery_port",
    "failed_provider_bot_event_send_uses_unified_system_message",
    "bot_final_event_relay_uses_each_provider_targets_session_tags",
    "bot_final_event_relay_preserves_session_id_for_legacy_target",
    "bot_final_event_relay_keeps_group_id_for_legacy_default_session_target",
    "private_bot_final_event_relays_without_hidden_prompt",
    "bot_final_event_relays_to_private_group_targets",
    "relay_limit_marks_group_inactive_and_publishes_frontend_event"
  ],
  "task_lifecycle": [
    "task_bot_final_event_is_forwarded_to_driver_and_task_replied",
    "duplicate_terminal_task_bot_event_is_ignored_after_reply",
    "terminal_task_bot_event_from_non_target_bot_is_ignored",
    "task_run_alias_requires_target_bot_and_dispatched_task",
    "channel_source_message_can_be_rebound_to_accepted_run_id",
    "task_ledger_notifications_are_sent_to_driver_after_dispatch_and_reply",
    "dispatch_transport_error_remains_pending_until_confirmed",
    "non_terminal_bot_event_does_not_mark_replied",
    "task_dispatch_uses_manager_session_for_worker_context"
  ],
  "coordination_echo": [
    "agent_tool_result_coordination_echo_dispatches_task",
    "agent_tool_result_coordination_requires_explicit_success",
    "duplicate_agent_tool_result_coordination_echo_dispatches_once",
    "agent_tool_result_coordination_echo_rejects_unsupported_tool_name",
    "native_mcp_tool_result_coordination_echo_dispatches_from_exact_provider_mapping",
    "native_mcp_assign_task_echo_forwards_provider_participant_tags",
    "native_mcp_coordination_rejects_unmapped_or_mismatched_results_and_resolves_once_per_run",
    "ordinary_tool_result_does_not_resolve_coordination_surface",
    "coordination_surface_resolution_failure_is_cached_and_fails_closed"
  ],
  "task_routing": [
    "manager_worker_task_dispatch_authorizes_manager_role_not_driver_bot_field",
    "manager_worker_unknown_target_emits_public_group_notice",
    "task_dispatch_rejects_muted_target_without_delivery",
    "task_dispatch_rejects_target_muted_in_session_participants",
    "task_dispatch_uses_legacy_wire_group_id_to_load_session_participants",
    "task_message_from_worker_is_forwarded_to_manager_session",
    "task_message_requires_session_id",
    "task_dispatch_blocking_interceptor_prevents_bot_delivery"
  ],
  "task_result": [
    "task_result_is_forwarded_back_to_manager_session",
    "manager_worker_task_result_preserves_manager_session_context",
    "manager_worker_task_result_records_independent_manager_run_context",
    "manager_worker_task_final_suffix_snapshot_persists_only_after_tool_result",
    "duplicate_manager_worker_task_final_does_not_append_duplicate_result_history",
    "manager_worker_task_final_from_non_target_bot_does_not_append_result_history",
    "manager_worker_task_result_not_delivered_defers_history_until_retry_success",
    "manager_worker_task_result_retry_uses_only_successful_terminal_text",
    "manager_worker_task_result_delivery_error_does_not_persist_or_mark_replied"
  ],
  "task_stream": [
    "manager_worker_task_final_delta_mode_flushes_only_open_worker_segment",
    "manager_worker_sse_task_response_appends_raw_deltas_across_thinking_boundary",
    "manager_worker_task_result_defaults_to_text_after_last_tool_call",
    "manager_worker_task_result_uses_agent_tool_boundary_by_default",
    "manager_worker_task_result_uses_final_when_agent_tool_has_no_followup_text",
    "manager_worker_task_dispatch_uses_sse_for_eligible_provider_worker",
    "manager_worker_task_message_uses_sse_for_eligible_provider_manager",
    "manager_worker_task_result_uses_sse_for_eligible_provider_manager",
    "manager_worker_provider_task_flow_uses_session_participant_tags"
  ],
  "task_completion": [
    "task_complete_updates_group_status_without_legacy_service_group_callback",
    "ws_task_complete_blocked_returns_err_when_pending",
    "echo_task_complete_blocked_returns_ok_guarded_when_pending",
    "task_complete_allowed_when_no_pending",
    "task_complete_allowed_when_dispatched_task_timed_out",
    "task_complete_pending_in_other_session_does_not_block",
    "task_complete_with_session_id_completes_service_session_without_closing_group",
    "task_complete_with_legacy_session_group_id_completes_session_without_closing_group",
    "manager_worker_task_complete_authorizes_manager_role_not_driver_bot_field"
  ],
  "task_history": [
    "manager_worker_task_dispatch_persists_dispatch_to_worker_history_after_delivery",
    "manager_worker_task_dispatch_records_context_before_history_persistence",
    "manager_worker_rejected_task_dispatch_persists_error_and_sends_result",
    "manager_worker_task_dispatch_does_not_persist_when_delivery_errors",
    "non_manager_worker_task_dispatch_does_not_persist_owner_tagged_dispatch_history",
    "manager_worker_task_message_persists_worker_message_to_manager_history_after_delivery",
    "manager_task_error_storage_retry_does_not_redeliver_result",
    "bot_error_terminal_persists_display_without_bot_delivery",
    "manager_worker_task_message_records_context_before_history_persistence",
    "task_run_error_terminal_flushes_buffered_chat_segment",
    "task_run_final_terminal_flushes_buffered_chat_segment",
    "duplicate_task_final_does_not_append_second_history_row",
    "task_final_from_non_target_bot_does_not_append_history"
  ],
  "history_persistence": [
    "bot_final_chat_persists_worker_owner_and_public_manager_owner_for_manager_worker",
    "bot_self_output_uses_session_worker_role_for_private_owner",
    "agent_tool_result_persists_worker_owner_and_public_manager_owner_for_manager_worker",
    "agent_tool_result_clears_cached_start_after_successful_persist",
    "agent_tool_result_retains_cached_start_when_persistence_fails",
    "bot_error_terminal_flushes_buffered_chat_segment",
    "direct_chat_segment_boundaries_do_not_persist_group_history"
  ],
  "human_notify": [
    "bot_event_legacy_mention_notifies_mentioned_human",
    "bot_event_structured_routing_does_not_notify",
    "bot_event_sender_routes_do_not_notify",
    "bot_event_structured_compat_mentions_do_not_notify",
    "bot_event_policy_blocked_message_does_not_notify"
  ]
}

```

## 附录 B：完整临时搬运与核对工具

将下面完整 Python 保存到 `$BCS_PLAN_WORKDIR/move_bot_event.py`。
仅 `apply` 会写第 1 节固定的 Rust 路径；`preview` 不写源码；
`verify` 检查声明、函数体、属性、模块身份与行数；`list` 检查真实 Rust 发现。

```python
import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_rust

WORK = Path(os.environ["BCS_PLAN_WORKDIR"])
PARSER = Parser(Language(tree_sitter_rust.language()))
EXPECTED_HASH = "ab4b5165b6de012fbe5e4c7088c5e833dcc5a0e4620d88fa3a1ea421d41d45ab"
TARGET = Path("crates/services/bcs-message-flow/tests/contract_bot_event.rs")
MODULE = "contract_bot_event"
TEST_ATTRIBUTE = re.compile(rb"#\[(?:tokio::)?test(?:\s*[\](])")
FIXTURE_TYPES = {
    "RecordingSystemMessage": "channel",
    "RecordingSystemNotification": "channel",
    "RecordingBotTerminalObserver": "channel",
    "RecordingChannelService": "channel",
    "RecordingSessionManagement": "session",
    "RecordingMessageRepo": "messages",
    "BlockingMessageRepo": "messages",
    "BlockingInterceptor": "task_flow",
}
HELPERS = {
    "state_payload_name": "frames",
    "assert_ledger_notification": "frames",
    "request_params": "frames",
    "request_id": "frames",
    "coordination_echo": "frames",
    "agent_tool_result_payload": "frames",
    "record_matching_tool_start": "frames",
    "test_session": "session",
    "manager_worker_flow_with_repo": "task_flow",
    "assert_frame_tags": "frames",
    "configure_provider_v2_target": "task_flow",
    "manager_worker_flow_with_provider_stream": "task_flow",
    "manager_worker_flow_with_blocking_repo": "task_flow",
    "register_manager_worker_task": "task_flow",
}
LOCAL_HELPERS = {
    "group_with_present_human": "human_notify",
    "bot_event_with_text": "human_notify",
}
EXPOSE_FIELDS = {
    "RecordingSystemMessage": {"notifications"},
    "RecordingSystemNotification": {"group_id", "event", "session_id", "participants"},
    "RecordingBotTerminalObserver": {"events"},
    "RecordingSessionManagement": {"completed"},
}


def parse(source):
    tree = PARSER.parse(source)
    assert not tree.root_node.has_error, "Rust AST parse error"
    return tree.root_node


def units(source):
    result = []
    prefix = []
    for node in parse(source).named_children:
        if node.type in {"attribute_item", "line_comment", "block_comment"}:
            prefix.append(node)
            continue
        start = prefix[0].start_byte if prefix else node.start_byte
        end = node.end_byte
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node else ""
        key = f"{node.type}:{name}"
        if node.type == "impl_item":
            target = node.child_by_field_name("type").text.decode()
            trait = node.child_by_field_name("trait")
            key = f"impl:{target}:{trait.text.decode() if trait else 'inherent'}"
            name = target
        result.append({
            "node": node, "start": start, "end": end, "name": name, "key": key,
            "raw": source[start:end],
            "test": node.type == "function_item" and any(
                TEST_ATTRIBUTE.search(item.text) for item in prefix
                if item.type == "attribute_item"
            ),
        })
        prefix = []
    assert not prefix, "Unexpected trailing unattached comment; review ownership"
    return result


def bindings(node):
    if node.type in {"identifier", "type_identifier"}:
        return [node.text.decode()]
    if node.type == "scoped_identifier":
        return [node.child_by_field_name("name").text.decode()]
    if node.type == "use_as_clause":
        return [node.child_by_field_name("alias").text.decode()]
    if node.type == "scoped_use_list":
        return bindings(node.child_by_field_name("list"))
    if node.type == "use_list":
        return [name for child in node.named_children for name in bindings(child)]
    raise AssertionError(f"Unsupported import shape: {node.type}")


def manifest():
    data = json.loads((WORK / "moves.json").read_text())
    names = [name for values in data.values() for name in values]
    assert len(names) == len(set(names)) == 108
    return data, {name: group for group, values in data.items() for name in values}


def original():
    source = (WORK / "original.rs").read_bytes()
    assert hashlib.sha256(source).hexdigest() == EXPECTED_HASH, "Baseline source drift"
    return source


def destination(unit, moves):
    kind, name = unit["node"].type, unit["name"]
    if unit["test"]:
        return f"{MODULE}/{moves[name]}.rs"
    if kind in {"struct_item", "impl_item"}:
        return f"{MODULE}/support/{FIXTURE_TYPES[name]}.rs"
    if kind == "function_item":
        if name in LOCAL_HELPERS:
            return f"{MODULE}/{LOCAL_HELPERS[name]}.rs"
        return f"{MODULE}/support/{HELPERS[name]}.rs"
    if kind == "const_item":
        assert name == "CONFIGURED_TASK_RUN_TIMEOUT_MS"
        return f"{MODULE}/support/task_flow.rs"
    raise AssertionError(f"Unassigned source item: {unit['key']}")


def add_visibility(unit):
    node, raw = unit["node"], unit["raw"]
    positions = []
    if node.type in {"function_item", "struct_item", "const_item"}:
        positions.append(node.start_byte)
    if node.type == "struct_item":
        fields = EXPOSE_FIELDS.get(unit["name"], set())
        body = node.child_by_field_name("body")
        if body:
            for field in body.named_children:
                if field.type == "field_declaration":
                    if field.child_by_field_name("name").text.decode() in fields:
                        positions.append(field.start_byte)
    if node.type == "impl_item" and node.child_by_field_name("trait") is None:
        positions.extend(
            child.start_byte for child in node.child_by_field_name("body").named_children
            if child.type == "function_item"
        )
    for absolute in sorted(positions, reverse=True):
        offset = absolute - unit["start"]
        raw = raw[:offset] + b"pub(in super::super) " + raw[offset:]
    return raw


def build():
    source = original()
    groups, moves = manifest()
    all_units = units(source)
    actual_tests = {item["name"] for item in all_units if item["test"]}
    assert actual_tests == set(moves), "Test manifest does not cover source exactly"
    keep = {"use_declaration", "mod_item"}
    exported = set(FIXTURE_TYPES) | set(HELPERS) | {"CONFIGURED_TASK_RUN_TIMEOUT_MS"}
    root_names = set(exported) | {"support", "session_support"}
    root_items, payloads, local_names = [], defaultdict(list), defaultdict(set)
    for item in all_units:
        node = item["node"]
        if node.type in keep:
            root_items.append(item["raw"])
            if node.type == "use_declaration":
                root_names.update(bindings(node.child_by_field_name("argument")))
            continue
        output = destination(item, moves)
        raw = add_visibility(item) if "/support/" in output else item["raw"]
        payloads[output].append(raw)
        if node.type != "impl_item":
            local_names[output].add(item["name"])
    assert len(root_items) == 13, "Original imports or module declarations changed"
    files = {}
    for output, body in payloads.items():
        parent = "super::super" if "/support/" in output else "super"
        imports = sorted(root_names - local_names[output])
        header = "use " + parent + "::{\n    " + ",\n    ".join(imports) + "\n};\n\n"
        files[output] = header.encode() + b"\n\n".join(body) + b"\n"
    root = b"\n\n".join(root_items).rstrip() + b"\n\n"
    root += b'#[path = "contract_bot_event/support.rs"]\nmod fixtures;\n'
    root += ("use fixtures::{" + ", ".join(sorted(exported)) + "};\n\n").encode()
    for group in groups:
        root += f'#[path = "{MODULE}/{group}.rs"]\nmod {group};\n\n'.encode()
    files[TARGET.name] = root.rstrip() + b"\n"
    fixture_root = []
    for group in sorted(set(FIXTURE_TYPES.values()) | set(HELPERS.values())):
        fixture_root.append(f'#[path = "support/{group}.rs"]\nmod {group};\n')
        exports = sorted(
            [name for name, owner in FIXTURE_TYPES.items() if owner == group]
            + [name for name, owner in HELPERS.items() if owner == group]
            + (["CONFIGURED_TASK_RUN_TIMEOUT_MS"] if group == "task_flow" else [])
        )
        fixture_root.append("pub(super) use " + group + "::{" + ", ".join(exports) + "};\n")
    files[f"{MODULE}/support.rs"] = "\n".join(fixture_root).encode()
    for name, data in files.items():
        assert len(data.splitlines()) <= 1000, (name, len(data.splitlines()))
        parse(data)
    return files


def canonical(raw):
    # Only remove the exact visibility prefix this script introduces.
    return raw.replace(b"pub(in super::super) ", b"").strip()


def verify():
    groups, moves = manifest()
    before = {
        item["key"]: item for item in units(original())
        if item["node"].type not in {"use_declaration", "mod_item"}
    }
    candidates = [TARGET, *sorted(TARGET.with_suffix("").rglob("*.rs"))]
    assert len(TARGET.read_bytes().splitlines()) <= 1000, "Root exceeds 1000 physical lines"
    actual, discovered = {}, {}
    for path in candidates:
        data = path.read_bytes()
        assert len(data.splitlines()) <= 1000, path
        for item in units(data):
            if item["node"].type in {"use_declaration", "mod_item"}:
                continue
            assert item["key"] not in actual, ("Duplicate declaration", item["key"])
            actual[item["key"]] = item
            expected = TARGET.parent / destination(item, moves)
            assert path == expected, (path, expected)
            if item["test"]:
                discovered[item["name"]] = path.stem
    assert set(actual) == set(before), (set(before) - set(actual), set(actual) - set(before))
    for key, old in before.items():
        assert canonical(old["raw"]) == canonical(actual[key]["raw"]), ("Body/attribute changed", key)
    assert discovered == moves
    # External fixture modules must keep their original root identity and paths.
    root_modules = {
        item["name"]: item["raw"] for item in units(TARGET.read_bytes())
        if item["node"].type == "mod_item"
    }
    for old in units(original()):
        if old["node"].type == "mod_item":
            assert root_modules[old["name"]] == old["raw"]
    print(f"PASS: {len(discovered)} tests, all declaration bodies/attributes preserved, all source <=1000 lines")


def check_runtime_list():
    _, moves = manifest()
    expected_before = set(moves)
    expected_after = {f"{group}::{name}" for name, group in moves.items()}
    def names(path):
        return re.findall(r"^(.+): test$", path.read_text(), re.M)
    before = names(WORK / "baseline-list.txt")
    after = names(WORK / "after-list.txt")
    assert len(before) == len(set(before)) == 108
    assert len(after) == len(set(after)) == 108
    assert set(before) == expected_before
    assert set(after) == expected_after
    print("PASS: actual Rust test discovery is a 108-to-108 bijection")


def main():
    command = argparse.ArgumentParser()
    command.add_argument("mode", choices=["preview", "apply", "verify", "list"])
    mode = command.parse_args().mode
    if mode == "verify":
        verify()
    elif mode == "list":
        check_runtime_list()
    else:
        files = build()
        for name, data in sorted(files.items()):
            print(len(data.splitlines()), name)
        if mode == "apply":
            assert TARGET.read_bytes() == original(), "Working source drift"
            assert not TARGET.with_suffix("").exists(), "Destination directory already exists"
            for name, data in files.items():
                path = TARGET.parent / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)


if __name__ == "__main__":
    main()
```

## 计划自检与验收追踪

| Design 目标 | 本计划的具体落实 |
| --- | --- |
| G1 行数上限 | Task 2 preview、Task 3/4/6 的 AST gate，覆盖全部 21 个 Rust 文件 |
| G2 按职责拆分 | 第 1 节文件职责、附录 A 精确 108 项映射，task history 独立 |
| G3 对外兼容 | Cargo target/外部 support 路径不变，不改生产实现/依赖/协议 |
| G4 测试不丢失 | 原函数/属性字节核对、实际发现映射、单项精确命中、串并行 108 项回归 |
| G5 warning/门禁 | 限定 cargo fix 目标、JSON diagnostics、架构与统一 coverage 工件核验 |
| G6 独立交付回滚 | 单一完整代码提交，范围检查与普通 revert；不依赖后四个单元 |

初版规划完成了源码清单、AST 工具 Python 语法与只读内存预览检查。
审阅修订后，在仓库外临时源码副本执行了附录 B 的 `apply` / `verify`：
108 项测试的声明、属性和函数体保持不变，21 个文件均低于 1,000 行；
使用现有 Cargo 依赖产物对临时副本执行 `rustc --test --emit=metadata`，
跨模块名称解析和类型检查通过，原 `BlockingInterceptor` 编译错误已消除。
该初始副本仍有 48 个未使用导入 warning，需在正式实施的 Task 3 中清理，
本次不宣称完成零 warning 验收或测试运行。新的 Bash 恢复检查已实际通过，
包含从不同目录恢复及失败管道返回 101、后续命令不执行的验证。

未在工作区执行 Rust 搬运，未增加仓库依赖；未执行本计划完整的
Cargo/数据库/Singlebox 验证，因此没有新的业务测试通过声明。

**执行选择：** 用户选择 Subagent-Driven 或 Inline Execution 后，才进入实际实施。
前者由用户授权代理任务并按依赖串行集成；当前单元共享一个 target/装配根，
不能让多个代理并发修改同一个入口或运行多个搬运脚本。
