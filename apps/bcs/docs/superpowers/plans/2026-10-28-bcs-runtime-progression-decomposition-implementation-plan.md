# BCS Runtime Progression Test Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成超长文件拆分设计的第 2 单元：将 `runtime_progression.rs` 的 49 个顶层测试及共享 fixture 按职责拆分，保留同一 Cargo target 的全部 266 项测试、既有恢复树及子进程测试身份，使所有新增、修改 Rust 文件不超过 1,000 物理行。

**Architecture:** 保留原测试入口、`test_runtime!` 宏和三个既有直接子模块，增加 11 个场景模块及套件私有 fixture 子树。既有 26 个后代 Rust 文件不改动；原父级私有导入维持后代 `super::*` 名称解析。仅搬运完整声明、开放实际必要的祖先范围可见性，并修正一个 `include_str!` 路径。

**Tech Stack:** Rust 2024、Cargo、Tokio、SQLite recovery fixtures；临时 Python 3.12、`tree-sitter==0.25.2`、`tree-sitter-rust==0.24.2`。临时工具不进入仓库依赖或构建链。

---

## 0. 授权范围与源码基准

- 依据：[拆分设计](../specs/2026-10-28-bcs-large-rust-files-decomposition-design.md) §7、§9、§10。
- 第 1 单元已通过 #3240 合入；本计划不重新执行 Bot Event 拆分，不涉及 CLI、Store 或 Runtime 生产文件。
- 规划分支：`docs/bcs-large-rust-files-design`，已 rebase 到 `origin/dev` 的 `b57b8e2d7`；文档 HEAD 为 `b6d864da0`。下面的源码 hash 是执行指纹，不以历史行号裁剪文件。
- 原文件：`apps/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs`，**5,789 行**。
- SHA-256：`5b4ac6c6f39dce6aa8520b67901c59c08795cb044148c0675ced3073547556a9`。
- Cargo 实际发现 **266 项**：49 个顶层测试迁移到新场景，217 个既有后代测试保持完整名称。既有后代物理文件 26 个，`fixed_loop.rs` 当前 997 行（设计最初基准为 1,000）。
- 本轮授权为生成计划，不在工作区执行 Rust 重构、自动 commit、push 或建 PR。未来实施需另行指令。

完成定义：20 个受控 Rust 文件（修改 1、创建 19），266 项发现一一对应，串行/并发均无新增失败；原声明体和属性保持，既有后代内容及 module 身份保持；7 个重启入口、38 组 SQLite case、76 次直接 child phase 均精确命中；零新增 warning；适用架构及统一 coverage 门禁结果可追踪。

## 1. 固定文件职责与边界

下面路径相对 `apps/bcs/crates/services/bcs-collaboration-runtime/tests/`。

| 操作 | 路径 | 职责 / 顶层测试数 |
| --- | --- | --- |
| Modify | `runtime_progression.rs` | 保留 imports、宏、旧子树挂载；新增场景和 fixtures 装配 |
| Create | `runtime_progression/session_permission.rs` | Session permission、legacy Chat、Human cancel：3 |
| Create | `runtime_progression/one_shot.rs` | transient bindings、initiator 发布、ManagerWorker Human reply：2 |
| Create | `runtime_progression/result_publication.rs` | 结果发布失败、Run 失败与 rerun：1 |
| Create | `runtime_progression/human_input_activation.rs` | 有效 Human、恢复成员、frontend/IM assignee：5 |
| Create | `runtime_progression/human_input_response.rs` | pending/response 校验、自然语言完成、Judge 与先落库：6 |
| Create | `runtime_progression/timeouts.rs` | Human/Provider timeout、失效候选、拒绝投递：6 |
| Create | `runtime_progression/completion.rs` | 完成、Run context、空 final 重试、无 callback、日志：5 |
| Create | `runtime_progression/resource_cleanup.rs` | 删除 Session/Group、孤儿运行与清理：4 |
| Create | `runtime_progression/definition_binding.rs` | YAML/preview、默认定义、模板 bindings、不额外 upsert：8 |
| Create | `runtime_progression/opening.rs` | opening 历史复用及写失败先于 dispatch：2 |
| Create | `runtime_progression/graph_and_judge.rs` | graph、fan-out/join、Judge 分支/失败/shared merge：7 |
| Create | `runtime_progression/support.rs` | 在 target 内命名为 `fixtures`，只装配及具名重导出 |
| Create | `runtime_progression/support/delivery.rs` | Delivery/Frontend/Result 记录器、拒绝发布、Run context、frame/terminal helper |
| Create | `runtime_progression/support/judge.rs` | Recording/Blocking/Sequenced Judge、noop_judge |
| Create | `runtime_progression/support/history.rs` | FailingAppendMessageRepo，保留持久化失败语义 |
| Create | `runtime_progression/support/channel.rs` | Session Channel 与 event factory 记录器 |
| Create | `runtime_progression/support/definitions.rs` | 所有 YAML 工厂、requires 断言、CountingDefinitionRepo |
| Create | `runtime_progression/support/groups.rs` | Group、Session 工厂，无新全局状态 |
| Create | `runtime_progression/support/logging.rs` | SharedLogBuffer/Writer 与唯一 capture_tracing_logs/OnceLock |

### 1.1 保持只读

- 全部生产 `src/**`、Cargo manifest/lock、迁移、coverage 配置与其他超长文件。
- 既有 `runtime_progression/*.rs` 的 26 个文件，包括 `fixed_loop.rs`、`history_identity.rs`、`history_persistence.rs` 及其全部后代。
- `tests/fixtures/**`、`apps/bcs/tests/fixtures/fixed_loop_api.json`、`apps/bcs/seeds/collaboration-templates/zh-CN/research-writing-loops.yaml`。
- 不以重命名旧 module、修改子进程 `--exact` 字符串、增加 ignore 或复制 fixture 解决编译问题。

### 1.2 祖先范围可见性与宏

`fixtures` 下各 support 模块的直接使用者包括新场景及未移动的多级后代。
类型、共享 free helper、被调用的 inherent 方法使用 `pub(in super::super)`；
`fixtures` 使用 `pub(super) use`，target 根仅私有 `use`，不创建生产 API。
trait impl 方法不加可见性。字段白名单如下，其他字段仍私有：

| 类型 | 开放字段 |
| --- | --- |
| `RecordingSessionChannelOutbound` | `fail_human_publish`、`human_publish_calls`、`events`、`terminal_events`、`validation_calls`、`validation_error` |
| `RecordingEventFactory` | `events` |
| `RecordingDelivery` / `RejectingDelivery` / `RecordingFrontendDelivery` / `RecordingResultPublisher` | 各自 `commands` |
| `RecordingJudge` / `SequencedJudge` | 各自 `requests` |
| `BlockingJudge` | `requests`、`started`、`release` |
| `SharedLogBuffer` | tuple field `.0`（旧 observability 子树直接读取） |

`SharedLogWriter` 必须具有与 Buffer 相容的受限可见性，因为它是 `MakeWriter::Writer` 关联类型；
但无需在 fixtures/target 根重导出这个名称。`CountingDefinitionRepo` 内部状态只通过既有方法访问，
`RecordingBotRunContext.contexts`、`FailingAppendMessageRepo.inner` 和 Writer 的 buffer 不开放。

`test_runtime!` 原文留在 target 根，位于使用它的子模块声明之前。新场景显式导入宏展开使用的
`CollaborationRuntime`、`Arc`、`MemoryMessageRepo`，不能只扫描函数体判断 imports。
旧子树的 `use super::*` 不改动；新模块最终采用具名 imports。

## Task 1: 准备隔离工作区并固化可恢复基线

**Files:** 仅读取 root/local AGENTS、CLAUDE、CONTEXT-MAP、架构规则及本设计；所有工具/日志在 WORKDIR。

- [ ] **Step 1: 按 using-git-worktrees 选择执行工作区并安装该 worktree 的 hooks。**

复用合适的隔离工作区或按用户流程创建；不要在文档分支实施。本计划只有一个相互依赖的 target，
不并发运行搬运工具或让代理同时修改入口。工作区到位后，从其根执行：

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
bash scripts/install_git_hooks.sh
git switch -c refactor/bcs-runtime-progression-modules
export BCS_ROOT="$(git rev-parse --show-toplevel)/apps/bcs"
export BCS_PLAN_BASE="$(git rev-parse HEAD)"
export BCS_PLAN_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/bcs-runtime-split.XXXXXX")"
{
  printf 'set -euo pipefail\n'
  printf 'export BCS_ROOT=%q\nexport BCS_PLAN_BASE=%q\nexport BCS_PLAN_WORKDIR=%q\n' \
    "$BCS_ROOT" "$BCS_PLAN_BASE" "$BCS_PLAN_WORKDIR"
  printf 'cd "$BCS_ROOT"\n'
} > "$BCS_PLAN_WORKDIR/env.sh"
printf 'Resume in Bash with: source %q\n' "$BCS_PLAN_WORKDIR/env.sh"
source "$BCS_PLAN_WORKDIR/env.sh"
git show "$BCS_PLAN_BASE:apps/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs" \
  > "$BCS_PLAN_WORKDIR/original.rs"
```

每个独立工具调用先在 Bash 中 source 上面输出的**同一环境文件**，不要重新生成 WORKDIR。
若隔离工具已创建目标分支，不重复 `git switch -c`，先确认它确为本任务分支。

- [ ] **Step 2: 验证恢复环境、源码指纹，并记录保护树。**

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import hashlib, json, pathlib, subprocess, sys
work = pathlib.Path(sys.argv[1])
resume = str(work / "env.sh")
subprocess.run(["bash", "--noprofile", "--norc", "-c",
    'source "$1"; [[ "$PWD" -ef "$BCS_ROOT" ]]; [[ -o errexit && -o nounset && -o pipefail ]]',
    "bash", resume], cwd="/", check=True)
failed = subprocess.run(["bash", "--noprofile", "--norc", "-c",
    'source "$1"; (exit 101) | tee /dev/null; echo SHOULD_NOT_RUN', "bash", resume],
    cwd="/", capture_output=True, text=True)
assert failed.returncode == 101 and "SHOULD_NOT_RUN" not in failed.stdout
assert hashlib.sha256((work / "original.rs").read_bytes()).hexdigest() == \
    "5b4ac6c6f39dce6aa8520b67901c59c08795cb044148c0675ced3073547556a9"
root = pathlib.Path("crates/services/bcs-collaboration-runtime/tests")
files = list((root / "runtime_progression").rglob("*.rs"))
assert len(files) == 26, "Existing recovery tree changed: re-plan"
files += [p for p in (root / "fixtures").rglob("*") if p.is_file()]
files += [pathlib.Path("tests/fixtures/fixed_loop_api.json"),
          pathlib.Path("seeds/collaboration-templates/zh-CN/research-writing-loops.yaml")]
(work / "protected.json").write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in sorted(files)}, indent=2) + "\n")
print("PASS: resumable shell, exact source fingerprint and protected fixtures")
PY
```

指纹/数量漂移先重新核对计划，不能仅更新 hash 或硬编码数量使之通过。
恢复树已含新行为时，按当前基线重建发现映射，不把它当成本次新增测试。

- [ ] **Step 3: 保存工具链、实际发现、完整目标执行与编译产物。**

```bash
{ git rev-parse HEAD; rustc --version; cargo --version; python3 --version; } \
  > "$BCS_PLAN_WORKDIR/environment.txt"
cargo test -p bcs-collaboration-runtime --test runtime_progression --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/baseline-compile.jsonl" 2> "$BCS_PLAN_WORKDIR/baseline-compile.stderr"
cargo test -p bcs-collaboration-runtime --test runtime_progression -- --list \
  > "$BCS_PLAN_WORKDIR/baseline-list.txt" 2> "$BCS_PLAN_WORKDIR/baseline-list.stderr"
cargo test -p bcs-collaboration-runtime --test runtime_progression \
  2>&1 | tee "$BCS_PLAN_WORKDIR/baseline-target.log"
```

预期实际发现并执行 266 项，0 failed、0 ignored。任何失败先诊断并记录；
#3209/#3240 历史记录不能替代当前 baseline。未跑的完整回归不标记完成。

## Task 2: 建立结构红灯、迁移清单与重启证据

**Files:** 临时 `moves.json`（附录 A）、`move_runtime.py`（附录 B）、`restart_probe.py`（附录 C）。

- [ ] **Step 1: 保存附录原文并准备固定版本 AST 环境。**

```bash
uv venv --python 3.12 "$BCS_PLAN_WORKDIR/tools"
uv pip install --python "$BCS_PLAN_WORKDIR/tools/bin/python" \
  --default-index https://pypi.org/simple \
  'tree-sitter==0.25.2' 'tree-sitter-rust==0.24.2'
"$BCS_PLAN_WORKDIR/tools/bin/python" -m py_compile \
  "$BCS_PLAN_WORKDIR/move_runtime.py" "$BCS_PLAN_WORKDIR/restart_probe.py"
```

三个附录都去除 fence 后存入 WORKDIR，不提交。工具版本只用于临时核对；不新增 Cargo/Python 仓库依赖。
规划验证使用 0.25.2；0.26.0 在本机遍历该文件时出现原生崩溃，不把它作为本单元执行前提。

- [ ] **Step 2: 证明未拆分入口触发结构红灯，再预览全部新文件。**

```bash
set +e
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify \
  > "$BCS_PLAN_WORKDIR/red-gate.log" 2>&1
red_status=$?
set -e
test "$red_status" -ne 0
grep -F 'Root exceeds 1000 physical lines' "$BCS_PLAN_WORKDIR/red-gate.log"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" preview \
  | tee "$BCS_PLAN_WORKDIR/layout-preview.txt"
```

必须因文件大小红灯，不接受 ImportError/解析失败。预览不写源码：初始具名导入全集下入口
120 行、最大 `human_input_response.rs` 928 行、共 20 个文件，全部 <=1,000。
清理 imports 后重新实测，不把初始统计作为最终结果。

- [ ] **Step 3: 验证旧重启测试的每个 child 分支，而不只检查父进程退出码。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/restart_probe.py" \
  baseline "$BCS_PLAN_WORKDIR/baseline-compile.jsonl" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/restart-baseline.log"
```

附录 C 使用 Cargo 本次输出的真实 binary，保留 7 个完整名称；按 38 个 case 分别启动
prepare/recover 两个进程，每次断言 **1 passed**，检查 SQLite 文件及持久化 Run 行存在，
recover 分支继续执行原测试的状态/去重/租约断言。保存 76 份日志、数据库和 summary.json。
不修改原测试、不绕过断言、不连接真实 MySQL；重跑使用新 label，不能复用旧数据库冒充证据。

## Task 3: 搬运声明并恢复最小 imports

**Files:** 第 1 节 20 个 Rust 文件；原 26 个后代和所有 fixture 保持 byte-identical。

- [ ] **Step 1: 执行完整搬运，再核对结构不变量。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" apply
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify
```

工具预先检查所有目标文件不存在，允许父目录包含已有 26 个文件，不整目录删除或覆盖。
仅允许一个路径修正：`fixed_loop_preview_honors_server_limits_and_cannot_start_or_configure_v2`
中的 `include_str!("fixtures/fixed_loop.yaml")` 改为 `include_str!("../fixtures/fixed_loop.yaml")`；
两者必须解析为同一个 fixture。宏、旧 module 声明和其 path 属性原样保留。

- [ ] **Step 2: 在限定 target 清理 unused imports，实际编译检查名称解析。**

```bash
cargo fix -p bcs-collaboration-runtime --test runtime_progression --allow-dirty --allow-staged \
  2>&1 | tee "$BCS_PLAN_WORKDIR/import-fix.log"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify
cargo test -p bcs-collaboration-runtime --test runtime_progression --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/after-compile.jsonl" 2> "$BCS_PLAN_WORKDIR/after-compile.stderr"
python3 - "$BCS_PLAN_WORKDIR/after-compile.jsonl" <<'PY'
import json, pathlib, sys
rows = [json.loads(line) for line in pathlib.Path(sys.argv[1]).read_text().splitlines()]
assert not [r for r in rows if r.get("reason") == "compiler-message"
            and r["message"]["level"] in {"warning", "error"}]
assert any(r.get("reason") == "build-finished" and r["success"] for r in rows)
print("PASS: target compiled without warnings/errors")
PY
```

初始脚本为防 trait/macro 漏导入使用具名全集，预览编译有 41 个 unused-import diagnostics。
rustc 无机器建议的 trait import 可依据具体文件/行号删除；不机械删除整个 use 块，
不改函数体或旧后代。删后导致父 imports unused 时重复本步骤；最终零 warning 才进入回归。
如 cargo fix 修改范围外文件，停止检查并只还原本次工具引入的越界修改；不 reset 他人改动。
不运行 cargo fmt、workspace cargo fix 或新增 allow。AST 检查不能代替 Rust 编译。

- [ ] **Step 3: 审核 fixture 生命周期和受限可见性。**

```bash
git diff --check
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify
git diff --stat
```

逐项核对第 1.2 节，特别是 Buffer `.0`、Writer 关联类型、旧子树使用的 BlockingJudge
及 Channel 字段；保持唯一 OnceLock 和原 subscriber 初始化行为，不复制 recorder/Notify。
同一原函数体仅存在一次；不以降低/新增 sleep、调大 timeout 或断言来稳定搬运后的测试。

## Task 4: 发现、串并行、日志及重启完整回归

**Files:** 不新增 Rust 文件；证据放 WORKDIR。

- [ ] **Step 1: 比较全部 266 项真实发现名单。**

```bash
cargo test -p bcs-collaboration-runtime --test runtime_progression -- --list \
  > "$BCS_PLAN_WORKDIR/after-list.txt" 2> "$BCS_PLAN_WORKDIR/after-list.stderr"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" list
```

49 项仅增加约定场景前缀，217 项旧后代完整名称不变；不只比较总数。

- [ ] **Step 2: 精确运行宏/fixture 路径、日志和持久化失败入口。**

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
from pathlib import Path
import subprocess, sys
work = Path(sys.argv[1])
names = [
    "definition_binding::fixed_loop_preview_honors_server_limits_and_cannot_start_or_configure_v2",
    "completion::state_machine_runtime_logs_run_node_and_terminal_lifecycle",
    "human_input_response::human_input_is_persisted_and_no_longer_pending_while_judging",
    "opening::opening_message_persistence_failure_fails_run_before_node_dispatch",
    "fixed_loop_tests::observability_tests::loop_observations_log_retry_and_stale_attempt_without_recounting",
]
for index, name in enumerate(names):
    result = subprocess.run(["cargo", "test", "-p", "bcs-collaboration-runtime", "--test",
        "runtime_progression", name, "--", "--exact", "--nocapture"], capture_output=True, text=True)
    text = result.stdout + result.stderr
    (work / f"exact-{index}.log").write_text(text)
    assert result.returncode == 0, (name, text)
    assert "test result: ok. 1 passed; 0 failed; 0 ignored;" in text, (name, text)
print("PASS: five exact compatibility/fixture checks executed")
PY
```

- [ ] **Step 3: 单线程和默认并发执行整个 target，并直接验证所有 child phases。**

```bash
cargo test -p bcs-collaboration-runtime --test runtime_progression -- --test-threads=1 \
  2>&1 | tee "$BCS_PLAN_WORKDIR/after-serial.log"
cargo test -p bcs-collaboration-runtime --test runtime_progression \
  2>&1 | tee "$BCS_PLAN_WORKDIR/after-parallel.log"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/restart_probe.py" \
  after "$BCS_PLAN_WORKDIR/after-compile.jsonl" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/restart-after.log"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json, pathlib, sys
work = pathlib.Path(sys.argv[1])
for name in ["after-serial.log", "after-parallel.log"]:
    assert "test result: ok. 266 passed; 0 failed; 0 ignored;" in (work / name).read_text(), name
before = json.loads((work / "restart-baseline/summary.json").read_text())
after = json.loads((work / "restart-after/summary.json").read_text())
assert len(before) == len(after) == 76
assert before == after, "Restart names/cases/phases/persisted Run counts changed"
print("PASS: 266 serial + parallel, 76 child-phase evidence matches baseline")
PY
```

完整 target 会跑原父进程编排；独立 probe 补足父测试只检查子进程退出码的盲区。
不新增永久业务测试，不把存在 SQLite 文件误当作 recover 业务断言已执行。

## Task 5: 完整提交、crate/workspace 与统一门禁

- [ ] **Step 1: 核对全部文件同时达标后创建一个完整代码提交。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify
git add -- crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs \
  crates/services/bcs-collaboration-runtime/tests/runtime_progression
python3 - <<'PY'
import pathlib, subprocess
staged = subprocess.check_output(["git", "diff", "--cached", "--name-only", "--relative"], text=True).splitlines()
prefix = "crates/services/bcs-collaboration-runtime/tests/runtime_progression"
assert len(staged) == 20 and all(p == prefix + ".rs" or p.startswith(prefix + "/") for p in staged), staged
assert all(len(pathlib.Path(p).read_bytes().splitlines()) <= 1000 for p in staged)
PY
git diff --cached --check
git commit -m "refactor(bcs): split runtime progression tests by responsibility"
```

不提交 WORKDIR、机器状态或日志，不创建原入口仍超限的中间提交。这里不是授权本轮生成计划时提交实现。

- [ ] **Step 2: 执行 crate 及 workspace 默认/all-feature 编译。**

```bash
cargo test -p bcs-collaboration-runtime 2>&1 | tee "$BCS_PLAN_WORKDIR/crate-tests.log"
cargo build 2>&1 | tee "$BCS_PLAN_WORKDIR/build.log"
cargo test --workspace --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/workspace-default.jsonl" 2> "$BCS_PLAN_WORKDIR/workspace-default.stderr"
cargo test --workspace --all-features --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/workspace-all-features.jsonl" 2> "$BCS_PLAN_WORKDIR/workspace-all-features.stderr"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json, pathlib, sys
for name in ["workspace-default.jsonl", "workspace-all-features.jsonl"]:
    rows = [json.loads(line) for line in (pathlib.Path(sys.argv[1]) / name).read_text().splitlines()]
    assert not [r for r in rows if r.get("reason") == "compiler-message" and r["message"]["level"] == "warning"], name
    assert any(r.get("reason") == "build-finished" and r["success"] for r in rows), name
print("PASS: both workspace compile gates, no compiler warnings")
PY
```

- [ ] **Step 3: 保留架构与全 workspace 实际结果，失败在同基线复现。**

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

此块刻意保留非零退出，不将其转换成通过。若失败，在相同工具链、feature/env 的干净 baseline
工作区复现后比较具体测试及断言；不沿用历史失败标签，不改无关实现、架构 baseline 或重试配置。
诊断需要修改本单元装配时，回到 Task 3，并重跑 Task 4 后提交修正；保留实际提交历史，不擅自 amend。

- [ ] **Step 4: 用真实 merge-base 跑统一 Singlebox gate 及工件校验。**

```bash
cd "$(git rev-parse --show-toplevel)"
git fetch origin dev
export SINGLEBOX_BASE_REF="$(git merge-base origin/dev HEAD)"
export BCS_E2E_CHANGED_BASE_REF="$SINGLEBOX_BASE_REF"
export BCS_E2E_CHANGED_LINE_MIN="$(python3 -c 'import os; print(max(60.0, float(os.environ.get("BCS_E2E_CHANGED_LINE_MIN", "60"))))')"
export BCS_E2E_CHANGED_LINE_MIN_LINES="$(python3 -c 'import os; print(min(20, int(os.environ.get("BCS_E2E_CHANGED_LINE_MIN_LINES", "20"))))')"
printf 'Singlebox merge-base: %s\n' "$SINGLEBOX_BASE_REF"
bash singlebox/ci/singlebox_coverage.sh 2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports \
  2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox-artifacts.log"
cd "$BCS_ROOT"
```

保持 line >=41%、method >=36%、HTTP endpoint/CLI leaf 100%；适用 changed-line 严格 >60%，
不覆盖更严门槛。正常本单元仅移动测试，生产 changed-line 分母应无新增；异常先排查 diff/工件，
不能增加 exclusion。不得杀其他 worktree 的服务；缺少工具/可用端口时明确未运行原因，
必需门禁要有可追踪远端结果后才能最终验收。不把 standalone BCS coverage 当作 unified gate。

## Task 6: 最终范围、不变性与交接

- [ ] **Step 1: 复核已提交范围、干净工作区和原后代哈希。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" verify
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/move_runtime.py" list
git diff "$BCS_PLAN_BASE" HEAD --check
python3 - <<'PY'
import os, pathlib, subprocess
files = subprocess.check_output(["git", "diff", "--name-only", "--relative", os.environ["BCS_PLAN_BASE"], "HEAD"], text=True).splitlines()
prefix = "crates/services/bcs-collaboration-runtime/tests/runtime_progression"
assert len(files) == 20 and all(p == prefix + ".rs" or p.startswith(prefix + "/") for p in files), files
assert all(len(pathlib.Path(p).read_bytes().splitlines()) <= 1000 for p in files)
assert not subprocess.check_output(["git", "status", "--porcelain"], text=True)
print("PASS: 20 scoped Rust files, all <=1000, no unrelated or uncommitted changes")
PY
```

- [ ] **Step 2: 按 `/tmp/CODE_REVIEW.md`（存在时）及 requesting-code-review 做最后审查。**

关注宏作用域、关联类型隐私、旧后代共享字段、唯一 subscriber、唯一恢复子树和精确 child 证据；
不要仅复述生成器结果或以相同数量代替映射。普通 revert 该单元即可回滚，无 schema/config 变更。

- [ ] **Step 3: 汇总实际 Validation，等待发布授权。**

采用仓库 `Problem / Solution / Validation / Compatibility and risk / Spec` 结构，报告实际 HEAD/base、
行数、266 项串并行、76 child phase、warning、门禁退出码及未运行项。贴文档链接并明确文档是否已合入。
不自动 push、建 PR 或宣称未完成门禁已通过。

## 附录 A：49 项顶层测试的唯一迁移清单

保存为 `$BCS_PLAN_WORKDIR/moves.json`；217 个既有后代不在迁移表中，不修改其名称。

```json
{
  "session_permission": [
    "current_session_permission_is_owned_by_chat_and_manager_group_driver",
    "legacy_chat_session_allows_one_shot_run_for_participant_human_view",
    "human_run_owner_can_cancel_through_human_access_api"
  ],
  "one_shot": [
    "one_shot_session_run_uses_transient_bindings_keeps_chat_open_and_publishes_as_initiator",
    "manager_worker_one_shot_human_input_consumes_the_assigned_human_chat_reply"
  ],
  "result_publication": [
    "one_shot_result_publication_failure_marks_run_failed_and_allows_rerun"
  ],
  "human_input_activation": [
    "human_input_without_authenticated_or_present_human_is_invalid_request",
    "human_input_can_start_without_authenticated_human_when_session_has_present_human",
    "authenticated_human_is_added_or_restored_before_human_input_starts",
    "frontend_human_input_skips_im_delivery_and_accepts_present_human",
    "im_human_input_rejects_a_different_present_human"
  ],
  "human_input_response": [
    "human_input_waits_without_bot_delivery_and_completes_from_natural_language",
    "state_machine_session_rejects_message_without_pending_human_input",
    "human_runtime_rejects_missing_context_and_invalid_response_targets",
    "human_input_uses_the_same_judge_contract_as_bot_output",
    "human_input_is_persisted_and_no_longer_pending_while_judging",
    "human_input_remains_persisted_when_judge_fails"
  ],
  "timeouts": [
    "human_input_timeout_ignores_global_retries_and_rejects_late_response",
    "timeout_scanner_aborts_run_when_group_is_missing",
    "timeout_scanner_aborts_run_when_session_is_missing",
    "timeout_scanner_skips_invalid_candidate_and_processes_later_run",
    "state_machine_explicit_node_timeout_overrides_provider_chat_default",
    "start_run_fails_and_marks_node_failed_when_delivery_returns_not_delivered"
  ],
  "completion": [
    "single_node_run_completes_session_with_bot_final_text",
    "state_machine_bot_delivery_registers_message_flow_run_context",
    "message_less_final_fails_attempt_and_schedules_retry",
    "state_machine_completion_finishes_service_without_callback",
    "state_machine_runtime_logs_run_node_and_terminal_lifecycle"
  ],
  "resource_cleanup": [
    "deleting_session_aborts_all_active_state_machine_runs",
    "deleting_group_aborts_active_state_machine_runs",
    "retrying_deleted_group_cleanup_aborts_orphaned_active_runs",
    "group_runtime_cleanup_aborts_runs_and_removes_sessions_and_binding"
  ],
  "definition_binding": [
    "fixed_loop_preview_honors_server_limits_and_cannot_start_or_configure_v2",
    "start_run_uses_group_default_definition_binding",
    "configure_im_definition_defers_channel_validation_until_run_start",
    "group_collaboration_definition_get_and_patch_preserve_source_yaml",
    "group_collaboration_definition_get_generates_legacy_authoring_yaml_without_identity",
    "start_run_from_group_binding_does_not_upsert_persisted_definition",
    "start_run_uses_group_participant_bindings_for_template_definition",
    "start_run_rejects_multi_bot_slot_with_current_single_assignee_runtime"
  ],
  "opening": [
    "custom_opening_message_is_rendered_once_and_reused_from_history",
    "opening_message_persistence_failure_fails_run_before_node_dispatch"
  ],
  "graph_and_judge": [
    "graph_view_returns_snapshot_edges_and_node_status",
    "complete_transitions_support_fan_out_and_implicit_all_join",
    "judged_node_routes_selected_outcome_and_skips_unselected_branch",
    "judged_node_publishes_bot_output_but_not_judge_message_to_workbench",
    "judged_node_failure_records_runtime_event_and_fails_run",
    "judged_node_timeout_records_runtime_event_and_fails_run",
    "judged_node_keeps_shared_merge_reachable_from_selected_branch"
  ]
}
```

## 附录 B：完整临时搬运与保持性工具

保存为 `$BCS_PLAN_WORKDIR/move_runtime.py`。`preview` 不写源码；`apply` 仅写固定的
20 个路径并拒绝覆盖新目标；`verify` 比较精确可见性/路径白名单后的完整声明；`list`
对比真实 Cargo 发现。旧目录已经存在，不能套用第 1 单元“目录必须不存在”的前提。

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
TARGET = Path("crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs")
MODULE = "runtime_progression"
EXPECTED_HASH = "5b4ac6c6f39dce6aa8520b67901c59c08795cb044148c0675ced3073547556a9"
LANGUAGE = Language(tree_sitter_rust.language())
PARSER = Parser(LANGUAGE)
TYPES = {
    "FailingAppendMessageRepo": "history",
    "SharedLogBuffer": "logging", "SharedLogWriter": "logging",
    "RecordingSessionChannelOutbound": "channel", "RecordingEventFactory": "channel",
    "CountingDefinitionRepo": "definitions",
    "RecordingDelivery": "delivery", "RecordingBotRunContext": "delivery",
    "RejectingDelivery": "delivery", "RecordingFrontendDelivery": "delivery",
    "RecordingResultPublisher": "delivery", "FailingResultPublisher": "delivery",
    "RecordingJudge": "judge", "BlockingJudge": "judge", "SequencedJudge": "judge",
}
HELPERS = {
    "capture_tracing_logs": "logging", "test_sessions": "groups",
    "assert_inferred_default_requires": "definitions", "noop_judge": "judge",
    "chat_send_params": "delivery", "complete_with_text": "delivery",
    "test_group": "groups", "session_collaboration_group": "groups",
    "state_machine_test_group": "groups",
    **{name: "definitions" for name in [
        "one_shot_authoring_yaml", "one_shot_human_input_authoring_yaml",
        "single_node_yaml", "human_input_yaml", "frontend_human_input_yaml",
        "judged_human_input_yaml", "single_node_authoring_yaml",
        "single_node_template_yaml", "join_yaml", "judge_branch_yaml",
        "judge_timeout_yaml", "judge_shared_merge_yaml",
    ]},
}
FIELDS = {
    "RecordingSessionChannelOutbound": {
        "fail_human_publish", "human_publish_calls", "events", "terminal_events",
        "validation_calls", "validation_error",
    },
    "RecordingEventFactory": {"events"},
    "RecordingDelivery": {"commands"}, "RejectingDelivery": {"commands"},
    "RecordingFrontendDelivery": {"commands"}, "RecordingResultPublisher": {"commands"},
    "RecordingJudge": {"requests"}, "BlockingJudge": {"requests", "started", "release"},
    "SequencedJudge": {"requests"},
}
PREFIX = b"pub(in super::super) "
KEEP = {"use_declaration", "mod_item", "macro_definition"}


def units(source):
    tree = PARSER.parse(source)
    assert not tree.root_node.has_error, "Rust AST parse error"
    result, prefix = [], []
    for node in tree.root_node.named_children:
        if node.type in {"attribute_item", "line_comment", "block_comment"}:
            prefix.append(node)
            continue
        start = prefix[0].start_byte if prefix else node.start_byte
        name = node.child_by_field_name("name")
        name = name.text.decode() if name else ""
        key = f"{node.type}:{name}"
        if node.type == "impl_item":
            name = node.child_by_field_name("type").text.decode()
            trait = node.child_by_field_name("trait")
            key = f"impl:{name}:{trait.text.decode() if trait else 'inherent'}"
        result.append(dict(node=node, name=name, key=key, start=start,
                           raw=source[start:node.end_byte], test=(
                               node.type == "function_item" and any(
                                   re.search(rb"#\[(?:tokio::)?test(?:\s*[\](])", item.text)
                                   for item in prefix if item.type == "attribute_item"
                               ))))
        prefix = []
    assert not prefix, "Unattached comment: review ownership"
    return result


def original():
    source = (WORK / "original.rs").read_bytes()
    assert hashlib.sha256(source).hexdigest() == EXPECTED_HASH, "Source drift: re-plan"
    return source


def moves():
    groups = json.loads((WORK / "moves.json").read_text())
    pairs = [(name, group) for group, names in groups.items() for name in names]
    assert len(pairs) == len(dict(pairs)) == 49
    return groups, dict(pairs)


def destination(item, mapping):
    if item["test"]:
        return f"{MODULE}/{mapping[item['name']]}.rs"
    table = TYPES if item["node"].type in {"struct_item", "impl_item"} else HELPERS
    assert item["name"] in table, ("Unassigned item", item["key"])
    return f"{MODULE}/support/{table[item['name']]}.rs"


def expose(item):
    node, raw = item["node"], item["raw"]
    positions = []
    if node.type in {"struct_item", "function_item"}:
        positions.append(node.start_byte)
    if node.type == "struct_item":
        body = node.child_by_field_name("body")
        if item["name"] == "SharedLogBuffer":
            # Its .0 is read by existing loop_observability descendants.
            positions.append(body.named_children[0].start_byte)
        elif body:
            for field in body.named_children:
                if field.type == "field_declaration" and field.child_by_field_name("name").text.decode() in FIELDS.get(item["name"], set()):
                    positions.append(field.start_byte)
    if node.type == "impl_item" and node.child_by_field_name("trait") is None:
        positions.extend(child.start_byte for child in node.child_by_field_name("body").named_children
                         if child.type == "function_item")
    for absolute in sorted(positions, reverse=True):
        offset = absolute - item["start"]
        raw = raw[:offset] + PREFIX + raw[offset:]
    return raw


def bindings(node):
    if node.type in {"identifier", "type_identifier", "self"}:
        return [node.text.decode()]
    if node.type == "scoped_identifier":
        return [node.child_by_field_name("name").text.decode()]
    if node.type == "use_as_clause":
        return [node.child_by_field_name("alias").text.decode()]
    if node.type == "scoped_use_list":
        names = bindings(node.child_by_field_name("list"))
        if "self" in names:
            path = node.child_by_field_name("path").text.decode().split("::")[-1]
            names = [path if name == "self" else name for name in names]
        return names
    if node.type == "use_list":
        return [name for child in node.named_children for name in bindings(child)]
    raise AssertionError(("Unsupported import", node.type))


def expected_item(item):
    raw = item["raw"] if item["test"] else expose(item)
    if item["name"] == "fixed_loop_preview_honors_server_limits_and_cannot_start_or_configure_v2":
        old = b'include_str!("fixtures/fixed_loop.yaml")'
        assert raw.count(old) == 1
        raw = raw.replace(old, b'include_str!("../fixtures/fixed_loop.yaml")')
    return raw


def build():
    groups, mapping = moves()
    items = units(original())
    assert {i["name"] for i in items if i["test"]} == set(mapping)
    # Writer must be visible for MakeWriter::Writer, but nobody imports it at root.
    exported = (set(TYPES) | set(HELPERS)) - {"SharedLogWriter"}
    root_names = set(exported)
    root, bodies, names = [], defaultdict(list), defaultdict(set)
    for item in items:
        kind = item["node"].type
        if kind in KEEP:
            root.append(item["raw"])
            if kind == "use_declaration":
                root_names.update(bindings(item["node"].child_by_field_name("argument")))
            continue
        path = destination(item, mapping)
        bodies[path].append(expected_item(item))
        if kind != "impl_item":
            names[path].add(item["name"])
    files = {}
    for path, parts in bodies.items():
        parent = "super::super" if "/support/" in path else "super"
        imports = sorted(root_names - names[path])
        header = "use " + parent + "::{\n    " + ",\n    ".join(imports) + "\n};\n\n"
        files[path] = header.encode() + b"\n\n".join(parts) + b"\n"
    root_text = b"\n\n".join(root) + b'\n\n#[path = "runtime_progression/support.rs"]\nmod fixtures;\n'
    root_text += ("use fixtures::{" + ", ".join(sorted(exported)) + "};\n").encode()
    for group in groups:
        root_text += f'\n#[path = "{MODULE}/{group}.rs"]\nmod {group};\n'.encode()
    files[TARGET.name] = root_text
    fixture_root = []
    for group in sorted(set(TYPES.values()) | set(HELPERS.values())):
        exports = sorted(name for name in exported if TYPES.get(name, HELPERS.get(name)) == group)
        fixture_root.append(f'#[path = "support/{group}.rs"]\nmod {group};\n'
                            + "pub(super) use " + group + "::{" + ", ".join(exports) + "};\n")
    files[f"{MODULE}/support.rs"] = "\n".join(fixture_root).encode()
    assert len(files) == 20
    for path, data in files.items():
        assert len(data.splitlines()) <= 1000, (path, len(data.splitlines()))
        units(data)
    return files


def unchanged_tree():
    saved = json.loads((WORK / "protected.json").read_text())
    for path, digest in saved.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path


def verify():
    assert len(TARGET.read_bytes().splitlines()) <= 1000, "Root exceeds 1000 physical lines"
    files = build()
    _, mapping = moves()
    expected = {i["key"]: expected_item(i) for i in units(original()) if i["node"].type not in KEEP}
    actual = {}
    for relative in files:
        path = TARGET.parent / relative
        data = path.read_bytes()
        assert len(data.splitlines()) <= 1000, path
        for item in units(data):
            if item["node"].type in KEEP:
                continue
            assert item["key"] not in actual, ("Duplicate", item["key"])
            assert path == TARGET.parent / destination(item, mapping), path
            actual[item["key"]] = item["raw"]
    assert actual == expected, [key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key)]
    old_fixed = {i["key"]: i["raw"] for i in units(original()) if i["node"].type in {"mod_item", "macro_definition"}}
    new_fixed = {i["key"]: i["raw"] for i in units(TARGET.read_bytes()) if i["key"] in old_fixed}
    assert old_fixed == new_fixed
    assert TARGET.read_bytes().index(b"macro_rules! test_runtime") < TARGET.read_bytes().index(b"mod fixed_loop_tests")
    old_yaml = TARGET.parent / "fixtures/fixed_loop.yaml"
    new_yaml = TARGET.with_suffix("") / "../fixtures/fixed_loop.yaml"
    assert old_yaml.resolve() == new_yaml.resolve()
    unchanged_tree()
    print("PASS: all original items/attributes preserved with exact visibility/path allowlist; 20 files <=1000; old tree unchanged")


def check_list():
    _, mapping = moves()
    def names(file):
        return re.findall(r"^(.+): test$", (WORK / file).read_text(), re.M)
    before, after = names("baseline-list.txt"), names("after-list.txt")
    assert len(before) == len(set(before)) == 266
    expected = {f"{mapping[name]}::{name}" if name in mapping else name for name in before}
    assert set(mapping) <= set(before)
    assert len(after) == len(set(after)) == 266 and set(after) == expected
    assert len([name for name in before if name not in mapping]) == 217
    print("PASS: 49 moved + 217 unchanged tests, actual discovery is 266-to-266")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preview", "apply", "verify", "list"])
    mode = parser.parse_args().mode
    if mode == "verify":
        verify()
    elif mode == "list":
        check_list()
    else:
        unchanged_tree()
        files = build()
        for name, data in sorted(files.items()):
            print(len(data.splitlines()), name)
        if mode == "apply":
            assert TARGET.read_bytes() == original(), "Working source drift"
            for name in files:
                assert name == TARGET.name or not (TARGET.parent / name).exists(), ("Refuse overwrite", name)
            for name, data in files.items():
                path = TARGET.parent / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)


if __name__ == "__main__":
    main()
```

## 附录 C：子进程精确命中与 SQLite phase 证据工具

保存为 `$BCS_PLAN_WORKDIR/restart_probe.py`。运行参数为唯一 label 和本次 Cargo JSON
编译产物文件；不猜 target/debug/deps 的哈希 binary 名，不读取旧 coverage 工件。

```python
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

work = Path(os.environ["BCS_PLAN_WORKDIR"])
label, artifacts = sys.argv[1:]
rows = [json.loads(line) for line in Path(artifacts).read_text().splitlines()]
binaries = {row["executable"] for row in rows
            if row.get("reason") == "compiler-artifact" and row.get("executable")
            and row["target"]["name"] == "runtime_progression" and row["profile"]["test"]}
assert len(binaries) == 1, binaries
binary = next(iter(binaries))
prefix = "fixed_loop_tests::recovery_tests::sqlite_restart_tests::"
# suffix, DB variable, phase variable, case/status variable, case values
scenarios = [
    ("sqlite_process_restart_recovers_all_committed_progression_windows",
     "BCS_LOOP_RECOVERY_TEST_DB", "BCS_LOOP_RECOVERY_TEST_PHASE", "BCS_LOOP_RECOVERY_TEST_CASE",
     ["continue", "break", "exhausted", "finalize", "retry", "failed", "judge",
      "opening_pending", "opening_history", "dispatch_pending", "dispatch_unknown"]),
    ("session_recovery_tests::terminal_session_recovery_survives_process_restart",
     "BCS_LOOP_SESSION_RECOVERY_DB", "BCS_LOOP_SESSION_RECOVERY_PHASE", "BCS_LOOP_SESSION_RECOVERY_STATUS",
     ["completed", "failed", "aborted"]),
    ("publication_tests::sqlite_process_restart_recovers_chat_publication_windows",
     "BCS_CHAT_RESULT_TEST_DB", "BCS_CHAT_RESULT_TEST_PHASE", "BCS_CHAT_RESULT_TEST_CASE",
     ["pending", "delivered", "unknown", "failed"]),
    ("terminal_im_tests::sqlite_process_restart_recovers_terminal_im_windows",
     "BCS_TERMINAL_IM_TEST_DB", "BCS_TERMINAL_IM_TEST_PHASE", "BCS_TERMINAL_IM_TEST_CASE",
     ["before_session", "pending", "unknown", "delivered", "failed", "expired"]),
    ("recovery_gap_tests::sqlite_process_restart_converges_missing_original_facts",
     "BCS_GAP_TEST_DB", "BCS_GAP_TEST_PHASE", "BCS_GAP_TEST_CASE",
     ["snapshot", "opening", "dispatch", "retry", "session", "v2", "accepted"]),
    ("terminal_cleanup_tests::terminal_cleanup_recovers_after_process_restart_without_loading_snapshot",
     "BCS_TERMINAL_CLEANUP_TEST_DB", "BCS_TERMINAL_CLEANUP_TEST_PHASE", None, ["cleanup"]),
    ("human_notification_tests::human_notification_scanner_recovers_across_process_restart",
     "BCS_HUMAN_NOTIFICATION_TEST_DB", "BCS_HUMAN_NOTIFICATION_TEST_PHASE", "BCS_HUMAN_NOTIFICATION_TEST_CASE",
     ["missing", "pending", "unknown", "ack", "active", "expired"]),
]
listed = subprocess.run([binary, "--list"], check=True, capture_output=True, text=True).stdout
names = set(re.findall(r"^(.+): test$", listed, re.M))
assert all(prefix + row[0] in names for row in scenarios)
# Each probe process must enter precisely one child branch, not inherit another one.
controlled = {key for row in scenarios for key in row[1:4] if key}
env_base = {key: value for key, value in os.environ.items() if key not in controlled}
out = work / f"restart-{label}"
out.mkdir(exist_ok=False)
summary = []
for index, (suffix, db_var, phase_var, case_var, cases) in enumerate(scenarios):
    for case in cases:
        database = out / f"{index}-{case}.sqlite"
        for phase in ["prepare", "recover"]:
            env = env_base | {db_var: str(database), phase_var: phase}
            if case_var:
                env[case_var] = json.dumps(case) if case_var.endswith("_STATUS") else case
            result = subprocess.run([binary, "--exact", prefix + suffix, "--nocapture"],
                                    env=env, capture_output=True, text=True, timeout=180)
            log = result.stdout + result.stderr
            (out / f"{index}-{case}-{phase}.log").write_text(log)
            assert result.returncode == 0, (suffix, case, phase, log)
            assert "test result: ok. 1 passed; 0 failed; 0 ignored;" in log, log
            assert database.is_file(), (case, phase, "no persisted SQLite artifact")
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                count = db.execute("SELECT COUNT(*) FROM bcs_state_machine_runs").fetchone()[0]
                assert count > 0, (case, phase, "no persisted Run")
            summary.append(dict(test=prefix + suffix, case=case, phase=phase, runs=count))
assert len(summary) == 76
(out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print("PASS: 7 exact names, 38 SQLite cases, 76 child phases each executed one test")
```

## 计划自检与当前验证边界

| 设计要求 | 执行落点 |
| --- | --- |
| 每个修改/新增源文件 <=1,000 行 | Task 2 preview、Task 3 verify、Task 5/6 全范围检查 |
| 按职责拆分且无生产修改 | 第 1 节固定 20 个文件，附录 A/B 完整归属 |
| 宏、include、旧树身份不变 | 宏及旧 mod 字节核对、唯一 include 修正、protected.json |
| 测试一一对应且不变弱 | 49 项精确声明核对、266 项实际发现、串并行完整回归 |
| 真实 child phase，不以退出 0 代替执行 | 附录 C：7 名称、38 case、76 个精确命中和 SQLite Run 证据 |
| 单份 tracing/共享 fixture | 第 1.2 节可见性白名单，旧 observability 精确测试 |
| 零 warning、门禁及失败披露 | Task 3 diagnostics、Task 5 crate/workspace/Singlebox |
| 独立交付/回滚 | 单元完整提交，普通 revert，不依赖后续生产拆分 |

本轮仅生成计划并做临时验证：原 target 编译/实际发现 266 项；基线子进程 probe
76 phases 通过；临时副本的 AST 保持性、Rust 类型检查及链接通过，实际发现与基线
为 266-to-266。临时副本同样完成了 76 次 child phase，名称/case/phase/持久化 Run
数量记录与基线完全一致。文档内 20 个 fenced code block 已做 Bash/Python/JSON 语法检查。
初始具名 imports 有 41 个 unused-import diagnostics，实施时仍需按
Task 3 清理并完成零 warning 门禁。本轮未在工作区搬运 Rust 文件，也未运行完整
workspace、架构或 Singlebox，不宣称第 2 单元实施完成。

**执行选择：** 收到后续实施指令后，再选择 Inline Execution 或获用户明确授权的
Subagent-Driven；本计划不授权自动并发修改共享入口或启动其他单元。
