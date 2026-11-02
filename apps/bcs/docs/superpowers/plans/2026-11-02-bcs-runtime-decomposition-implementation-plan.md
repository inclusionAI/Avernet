# BCS Collaboration Runtime Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成大 Rust 文件拆分的第 5 单元：将 `bcs-collaboration-runtime/src/runtime.rs` 按职责拆分，保持公开入口、运行状态、权限、持久化/恢复顺序、错误语义和运行成本不变，使本单元全部新增、修改 Rust 文件不超过 1,000 物理行。

**Architecture:** `runtime.rs` 保留原类型/字段、20 个构造与 builder、5 个常量、caller resolver 和既有检查点模块装配。新增 19 个直接私有子模块；唯一 `CollaborationRuntimeService` impl 保留全部 43 个方法，24 个长方法转为同签名直接委托，19 个短方法保持原文；48 个 inherent 方法与 69 个 free helper 按能力搬移，不建立新组件、trait、锁、缓存或调度层。

**Tech Stack:** Rust 2024、async-trait、Tokio、tracing、既有 Service API/repo/port 与 Memory/SQLite 测试；临时 Python 3.12 + tree-sitter 0.25.2/rust grammar 0.24.2 用于搬移及独立审计，不加入产品依赖或构建链。

---

## 0. 授权、基线、范围和验收

- 设计：[大 Rust 文件拆分](../specs/2026-10-28-bcs-large-rust-files-decomposition-design.md) §6、§9–§11。本轮用户只授权**生成第五单元计划**；临时副本验证不是实施，不自动 commit、push 或发布 PR。
- 规划日期 **2026-11-02**。文档分支 `docs/bcs-large-rust-files-design` 已 rebase；规划 HEAD **`a02859d3a5`**，代码 merge-base **`2adc74324c37dc13bbb0aa8e4f622365a14b0648`**。执行时重新 fetch，禁止把这些历史值当作实时远端状态。
- 原文件 `apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs`：**7,182 行**，SHA-256 **`6299bec2173f92d8a96ca0db62e5322fcd5d10856072ff932b6488031c67ddf8`**。
- 当前 Service impl **2,531 行**（不含 `#[async_trait]`）；最长 `start_state_machine_run` **552 行**。此轮只搬移完整函数，**不再拆它内部的 caller 注册/relay 分支**。
- 精确源码范围 **20 文件：修改 1、新建 19**。原 crate 其余 **76 个已跟踪文件**全部只读，含 10 个既有 `runtime_*.rs`、其他源码、全部 integration tests/fixture 与 Cargo.toml。API、Store、插件、composition root、迁移、coverage gate/config 均不修改。
- 实际发现 **370 项**：lib 7；`definition_validation` 34；`dispatch_credential_isolation` 1；`edge_display_names` 4；`fixed_loop_compiler` 33；`fixed_loop_context` 4；`fixed_loop_snapshot` 6；`runtime_progression` **281**。没有 ignored；doc-tests 0。不能沿用第二单元的 266 或此前的 353 总数。
- 对源码的保持性审计覆盖 **190 个原函数身份**、**12 个类型/常量声明**（含测试 UnusedJudge）、**4 个 trait impl**（Service、两个 caller resolver、测试 Judge）、**24 个精确委托**和 **35 个隐式日志 target 的显式保留**。

交付要求：20 文件 <=1,000 行；370 项测试完整名称/target/属性保持；Memory 与 SQLite 原断言不弱化；7 个 restart 入口、38 个数据库 case、76 个 child phase 精确执行；受影响 crate 无新增 warning；unit changed-line **>=80%** 和统一 Singlebox E2E changed-line **>60%** 分别实测通过。不能用“纯搬运”或后一个门禁替代前一个门禁。

## 1. 固定文件结构

路径相对 `apps/bcs/crates/services/bcs-collaboration-runtime/src/`。行数来自本计划附录 A 的临时候选，不是未来变更的豁免；当前最大文件 631 行。

| 操作 | 文件 | 职责 | 候选行数 |
| --- | --- | --- | ---: |
| Modify | `runtime.rs` | 原状态/记录类型、20 构造/builder、5 常量、两个 caller resolver 的类型及实现、全部模块装配 | 516 |
| Create | `runtime/service.rs` | 唯一完整 Service trait 实现，24 委托 +19 原短方法 | 383 |
| Create | `runtime/definition.rs` | compile/resolve/load、Group definition 投影/patch/upgrade/configure、候选版本兼容校验 | 478 |
| Create | `runtime/authoring.rs` | YAML parse、identity 拒绝、规范化和默认字段清理 | 258 |
| Create | `runtime/bindings.rs` | slot/role/bot 映射、binding 校验和 permission 投影 | 223 |
| Create | `runtime/access.rs` | Human/Run access、Session permission、pending Human 查询 | 222 |
| Create | `runtime/start.rs` | start 主体及 build_node_runs；包括原 caller association/relay 分支 | 631 |
| Create | `runtime/rerun.rs` | Session one-shot 提交、rerun、物化 rerun 恢复和 rerun Node 构造 | 261 |
| Create | `runtime/human_input.rs` | activate/resume、自然语言输入/respond、通知、Human assignee 与 Channel 校验 | 596 |
| Create | `runtime/node_execution.rs` | dispatch/failure、prompt/upstream、Judge outcome、GroupContext 输入 | 458 |
| Create | `runtime/progression.rs` | ready/branch skip/join/retry、completed progression | 331 |
| Create | `runtime/completion.rs` | complete/fail/cancel、Service Session 收尾、Session/Group 清理、final output | 470 |
| Create | `runtime/terminal_event.rs` | bot terminal 主体、stale-event 判断、terminal payload/text 提取 | 478 |
| Create | `runtime/recovery.rs` | 原单 Run 恢复、timeout candidate 和各扫描/cleanup 编排 | 364 |
| Create | `runtime/views.rs` | Run/Node/graph/history/panel 投影与 message window | 567 |
| Create | `runtime/events.rs` | public event、panel/bot 发布、panel 持久化及 payload/metadata/时间转换 | 396 |
| Create | `runtime/opening.rs` | opening 取回、默认 panel、render 和 Run override | 142 |
| Create | `runtime/logging.rs` | 原 dispatch/delivery/terminal/timeout 结构化日志和 elapsed helper | 292 |
| Create | `runtime/caller.rs` | 原 release_instance_association / rearm_instance_association 两方法 | 97 |
| Create | `runtime/tests.rs` | 原 7 个内联测试、UnusedJudge 与其实现，完整身份不变 | 285 |

完整方法及 helper 名单在附录 A 的 `METHODS`、`DELEGATES`、`FUNCTIONS` 中，是闭合清单，不用历史行号裁剪。测试只移模块内部原文，不重组场景。

### 1.1 只读父级与公开兼容

- `src/lib.rs` 的 `pub mod runtime` 与 `pub use runtime::CollaborationRuntime` 原文不动。
- 根保留 `CollaborationRuntime`、`ResolvedDefinition`、`ResolvedDefinitionSource`、`JudgeEvaluationResult`，不开放字段，不改变 Arc/Option/defaults。`with_caller_relay_redeemer` 的装配先后条件、20 个 builder/constructor 的签名和正文原样保持。
- `ControlPlaneOrchestrationResolver` / `RefusingOrchestrationResolver` 的原 `pub(super)` 类型、`pub plane` 字段、原 trait 实现均**留在 runtime 根**，不因搬移改成 pub(crate) 或重新导出。
- 既有 `#[path = "runtime_*.rs"]` 挂载完全保留：`judge_recovery`、`runtime_recovery_gap`、`terminal_im`、`publication`、`opening_recovery`、`dispatch_recovery`、`metadata_projection`、`loop_observability`、`message_history`、`history_query`。不重命名、不挪物理路径、不改逻辑父级。
- 新模块 `mod definition` 是 `runtime::definition`，不得替代 `crate::definition`；`runtime::progression` 同样不替代原 `crate::progression`。所有旧 crate-qualified 调用保持原文。
- 新模块仍属于同一 Runtime 内部实现；不新增 Service/Plugin API、外部组件、环境分支或 Backend context YAML。当前 Runtime 无 crate-local CONTEXT.md；读 Service API/Domain 的相关 context 和现有契约即可，不把新增 CONTEXT 当作交付前提。

### 1.2 可见性与委托

新模块为 `runtime` 的直接私有子模块，保持已有子模块的 `use super::*` 风格。跨职责 inherent 方法仅 `pub(super)`；free helper 仅在被其他模块引用时开放 `pub(super)` 并在根私有具名导入，同一模块专属 helper 保持私有。不使用 `pub use`、`macro_export`、`include!`、blanket allow 或运行时文件生成。

两个 helper 仅通过根提供给原内联 tests，采用精确测试导入，避免生产 unused_imports：

```rust
#[cfg(test)]
use opening::default_state_machine_opening_message;
#[cfg(test)]
use start::build_node_runs;
```

仅 24 个长 Service 方法提取，全部使用 `<原方法名>_impl`，原参数/返回值/泛型/属性不变。保持 19 个已有短投影/转发（包括同步 `caller_orchestration_wired()`），不制造额外 wrapper 提高 method coverage。

```rust
// runtime/service.rs，位于原 #[async_trait] impl 内
async fn start_state_machine_run(
    &self,
    cmd: StartStateMachineRunCommand,
) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
    self.start_state_machine_run_impl(cmd).await
}
```

`runtime/start.rs` 的 inherent 方法仅改名为 `pub(super) async fn start_state_machine_run_impl`，正文由附录 A 精确复制。旧正文中通过 `self.start_state_machine_run` 等调用 Service 的位置不改成直接 helper 调用；不引入同名 inherent shadow、自递归、Box::pin、spawn、额外 clone 或错误转换。`#[async_trait]` 的 Send/对象安全和借用关系必须实际通过编译验证。

### 1.3 日志、测试身份和源码位置

根新增一个常量：

```rust
const RUNTIME_LOG_TARGET: &str = module_path!();
```

35 处原来使用隐式模块 target 的 `info!`/`warn!`/`tracing::{info,warn,debug}!` 搬移后只插入 `target: RUNTIME_LOG_TARGET,`，level、message、fields 不变。原显式 `MSG_LOG_TARGET` 和 `"state_machine_timeout_scanner"` 不改；旧 `runtime_observability.rs` 继续拥有 Loop instrumentation，不挪进去“统一日志”。文件/行号元数据会随源码位置变化，此结构变化不是日志字段契约变更。

根改为 `#[cfg(test)] mod tests;`，`runtime/tests.rs` 不再包一层 tests。7 项全名仍为 `runtime::tests::<name>`；integration targets、fixture、现有 restart 子进程字符串全部不动。重启 child 的退出 0 不足以证明运行过测试，须执行附录 C。

## 2. 新基线必须守住的语义

最初 spec 没有充分展开后续合入的 caller wiring/relay。先读 `apps/bcs/specs/2026-10-30-caller-relay-orchestration-nodes/design.md`，但注意该文档标为 proposed，**运行接口以当前 Service API 和已提交实现/测试为准**；不得按旧草稿字段名重写现有代码。

| 边界 | 必须保持 |
| --- | --- |
| Caller 装配 | `with_caller_orchestration` 在 control plane 存在但缺 protected pipeline/registrar 时选择 Refusing resolver；读取失败/缺配置返回 UnknownClosed，不退化为普通 wire |
| Instance 生命周期 | start 的 scope/clock stamping、24h budget、register 在 dispatch 前；terminal complete/fail/abort 的 release；HumanInput rearm 不翻转原 subject |
| Relay | 原 Execution assignee 收集/去重位置、redeem_submission、allowlist/无 capture 拒绝、补偿与终态收敛完全保留，不重新排序 create/register/redeem，不把 failed admission 隐藏为成功 |
| 错误政策 | repo/数据库持久化失败原样传播；原 best-effort registrar release/rearm 的日志与既有拒绝行为也原样保持，不能混同两类失败并统一成 Ok/Err |
| 凭据隔离 | persisted dispatch request 不新增 token/identity；既有 credential-isolation canary 是形状测试，不应被描述成端到端凭据系统验收 |
| Start/rerun | one-shot transient bindings 不写成 Group 默认绑定；Session idle/activation、唯一后继、immutable snapshot、opening override、开始发送 barrier 原顺序 |
| HumanInput | participant/access/currentness、64KiB 限制、持久化响应先于 Judge；晚到/重复响应、拒绝通知和 pending 查询语义保持 |
| Node/terminal | canonical correlation、attempt、Bot/Session 匹配，stale event 不推进；accepted/unknown/rejected 不统一；Judge/dispatch lease 与失败重试次数保持 |
| Recovery | 从持久化 snapshot/checkpoint 恢复，不重新编译当前 YAML、不猜 request、不重发 unknown；Run/Session activation fencing、partial-progress/全失败返回、cursor 保持 |
| Completion | Event 与业务变更顺序、history/panel/publication、Service Session 收尾、Chat session 保持、delete cleanup 不改 |

### 2.1 数据库与扫描成本（预期变化为零，不声称原流程是常数成本）

已追踪 Runtime → `StateMachineRunRepoPort` → Store `sql_runs.rs` → `sql_run_lifecycle.rs` / `branch_skip.rs` / checkpoint modules；Memory 后端不产生 SQL。一次 repo 调用不是一次网络往返，`transaction` 仍有各 statement 及 BEGIN/COMMIT 成本；不把这些误算成一次 DB 查询。

| 路径 | 当前成本/界限和不可改变项 |
| --- | --- |
| progression page | limit=0 返回空；`tokio::try_join!` 并发查 running/pending 两页，SQL 各 1 query、各 <=limit 行；merge/sort/dedup 后 truncate 到 limit，逐 Run 恢复，不增加并发 task |
| Run recovery | snapshot/Group/Session 查询，Node 全量读取会在 startup/Judge/retry/skip 后按原路径重复；各 Node 分支也有查询，保留原调用次数和序列，不能概括成“一 Run 一 query” |
| branch skip | 原最多 3 次竞争尝试（`0..3`），status 查询与 skip 以 `STATE_MACHINE_NODE_BATCH_LIMIT=200` 分块；SQL status 是 scoped SELECT，skip 是 ExecuteChecked 原子事务；不改变 DFS 顺序、CAS 冲突回退与 fencing |
| join readiness | `all_upstreams_completed` 当前逐上游 `get_node_run` 并可早返回；属于现有成本，不借拆分引入 batching、缓存或一致性改变 |
| terminal cleanup | 候选 limit=min(requested,32)，每 Run checkpoint cleanup limit=32；history repairs 仅在原开关下查询/写入；不扩大批次/更改 cursor |
| timeout | 保留传入 batch limit、node/group/session 校验、失效候选计数；整批处理失败必须返回错误用于调度 backoff；不能让失败变 idle |
| start/rerun SQL | 原整事务建立 Run/Node/Session snapshot，guarded Node INSERT 48 条一块；不改变上限或把数据写分成多次独立提交 |
| caller policy/relay | port 背后的 control plane/cache/lineage 成本随部署实现变化；原 resolve 次数、重复 assignee 处理、单次 redeem/registration/release 保持；不臆测每次 resolve 都是 DB query |

不增加锁、不延长资源生命周期、不跨外部投递引入新数据库事务；同一未来链的机械 `.await` 委托不增加调度或重试。现有 2048/近容量 skip、并发 cancel/recovery、持久化失败与 restart 回归是成本和顺序证据，不是生产压测结论。

## Task 1: 准备实现工作区并保存可恢复基线

**Files:** 只读 root/local instructions、架构文档、原 Runtime/API/Store 和本 spec。工具、日志、原文放临时 WORKDIR。

- [ ] **Step 1: 按 using-git-worktrees 选择实现工作区，不在 docs 分支执行生产搬移。** 复用已有合适 worktree，确认无用户未提交变更；不要并发修改共享 `runtime.rs`。当前基线不存在 `scripts/install_git_hooks.sh`，不能照抄旧计划的失效入口；确认已提交 `.githooks/pre-push` 与实际 hooksPath，有安装脚本时才调用。

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
if test -f scripts/install_git_hooks.sh; then
  bash scripts/install_git_hooks.sh
else
  test -x .githooks/pre-push
  test "$(git config --get core.hooksPath)" = .githooks
fi
git fetch origin dev
export ROOT="$(git rev-parse --show-toplevel)"
export BCS_ROOT="$ROOT/apps/bcs"
export BASE="$(git rev-parse HEAD)"
export TARGET_BASE="$(git merge-base origin/dev HEAD)"
export RUNTIME="$BCS_ROOT/crates/services/bcs-collaboration-runtime"
export WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/bcs-runtime-unit5.XXXXXX")"
{
  printf 'set -euo pipefail\n'
  printf 'export ROOT=%q BCS_ROOT=%q BASE=%q TARGET_BASE=%q RUNTIME=%q WORKDIR=%q\n' \
    "$ROOT" "$BCS_ROOT" "$BASE" "$TARGET_BASE" "$RUNTIME" "$WORKDIR"
  printf 'cd "$BCS_ROOT"\n'
} > "$WORKDIR/env.sh"
cp "$RUNTIME/src/runtime.rs" "$WORKDIR/original.rs"
printf '%s\n' "$BASE" "$TARGET_BASE" > "$WORKDIR/revisions.txt"
(rustc -Vv; cargo -V) > "$WORKDIR/toolchain.txt"
shasum -a 256 "$WORKDIR/original.rs"
```

SHA 不匹配时停止，重新 inventory、函数归属、测试清单与保持性审计；禁止只修改 EXPECTED hash。每次恢复 Bash 先 source `env.sh`，同时恢复 cwd、变量与 `set -euo pipefail`，保证 `tee` 不吞失败。

- [ ] **Step 2: 保存附录 A/B/C 为 WORKDIR 的三个独立工具，安装临时解析环境。**

```bash
uv venv --python 3.12 "$WORKDIR/venv"
uv pip install --python "$WORKDIR/venv/bin/python" --index-url https://pypi.org/simple \
  tree-sitter==0.25.2 tree-sitter-rust==0.24.2
```

工具不是 Cargo build script，不提交 generated source/临时依赖。输出是正常可审阅 Rust 文件；不在构建期生成代码。

- [ ] **Step 3: 保存完整发现、实际测试与编译 diagnostics。**

```bash
cd "$BCS_ROOT"
cargo test -p bcs-collaboration-runtime -- --list \
  > "$WORKDIR/before-list.txt" 2> "$WORKDIR/before-list.stderr"
cargo test -p bcs-collaboration-runtime 2>&1 | tee "$WORKDIR/before-tests.log"
cargo test -p bcs-collaboration-runtime --all-features 2>&1 | tee "$WORKDIR/before-all-features.log"
cargo test -p bcs-collaboration-runtime --no-run --message-format=json \
  > "$WORKDIR/before-artifacts.jsonl" 2> "$WORKDIR/before-artifacts.stderr"
python3 "$WORKDIR/restart_probe.py" before "$WORKDIR/before-artifacts.jsonl"
```

期望 370 passed/0 failed/0 ignored、7 名称/38 case/76 child phases。当前 fresh 基线包含 **bcs-bot-store 的 1 条 unused_imports diagnostic**；Runtime 自身无 warning。记录归属与集合，不把此 warning 修复混入本单元，也不宣称全依赖零 warning。

- [ ] **Step 4: 改代码前跑 unit coverage 与统一 Singlebox 基线，保存逐文件报告。**

```bash
cd "$BCS_ROOT"
bash scripts/ci_test.sh --coverage --reuse-build 2>&1 | tee "$WORKDIR/before-unit-coverage.log"
python3 scripts/cov_gate.py --base-ref "$TARGET_BASE" --bcs-dir "$BCS_ROOT" \
  --pass-rate-min 100 --overall-line-min 84 --changed-line-min 80 \
  2>&1 | tee "$WORKDIR/before-unit-gate.log"
cp -R testresult "$WORKDIR/before-unit-reports"
cd "$ROOT"
bash singlebox/ci/singlebox_coverage.sh 2>&1 | tee "$WORKDIR/before-singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
cp -R singlebox/.dependencies/coverage/singlebox/reports "$WORKDIR/before-e2e-reports"
BCS_BASE_REF="$TARGET_BASE" bash apps/bcs/scripts/ci/arch-check.sh \
  2>&1 | tee "$WORKDIR/before-arch.log"
```

这些是实施要求，规划期未跑。不以旧 PR 的 green 或归档覆盖率代替本次 gate。非零结果保存退出码并同 BASE 复现；恢复 env 后分别运行其他独立基线，不加 `|| true` 显示成功。

原 `runtime.rs` 的低覆盖分支搬迁后会进入 changed-line 分母。若基线显示需要新增测试，先依据实际缺口确定**具体新 test 文件/公共行为/断言**并更新授权范围和计划，再做 RED/GREEN；当前纯搬移的 20 文件白名单不隐含授权改整个测试树。不得改阈值、skip regex、coverage exclusion/min-lines，或添加按路径/日期/整个目录生效的 waiver。新增测试必须执行真实 Runtime 对外入口并验证 observable state/port 调用，不能用空 wrapper 或测试专属生产调用提高数字。

## Task 2: 结构 RED 与完整候选生成

**Files:** 临时 `$WORKDIR/{original.rs,split_runtime.py,audit_runtime.py,candidate-src/}`，真实源码保持原样。

- [ ] **Step 1: 对未拆分源码确认结构检查 RED，而不是人为破坏行为。**

```bash
if "$WORKDIR/venv/bin/python" "$WORKDIR/audit_runtime.py" "$WORKDIR/original.rs" "$RUNTIME/src" \
  > "$WORKDIR/structure-red.log" 2>&1; then
  echo 'ERROR: unsplit Runtime passed the decomposition check' >&2
  exit 1
fi
grep -F 'AssertionError: 0' "$WORKDIR/structure-red.log"
```

预期缺少 24 个委托，`AssertionError: 0`；ImportError/不存在路径不是有效 RED。

- [ ] **Step 2: 根据附录 A 的完整归属生成候选，运行独立审计 GREEN。**

```bash
"$WORKDIR/venv/bin/python" "$WORKDIR/split_runtime.py" \
  "$WORKDIR/original.rs" "$RUNTIME/src" "$WORKDIR/candidate-src" \
  | tee "$WORKDIR/candidate-lines.txt"
"$WORKDIR/venv/bin/python" "$WORKDIR/audit_runtime.py" \
  "$WORKDIR/original.rs" "$WORKDIR/candidate-src"
```

期望 190 原函数、12 声明、4 trait impl、24 精确委托、35 target 保留、20 文件 <=1,000。生成器拒绝覆盖已有输出目录，防止多个基线混用。

- [ ] **Step 3: 人工核对装配、caller 和持久化边界。**

```bash
cat "$WORKDIR/candidate-src/runtime.rs"
cat "$WORKDIR/candidate-src/runtime/service.rs"
diff -u "$RUNTIME/src/runtime_dispatch.rs" "$WORKDIR/candidate-src/runtime_dispatch.rs"
diff -u "$RUNTIME/src/runtime_message_history.rs" "$WORKDIR/candidate-src/runtime_message_history.rs"
```

对照 §1/§2 和附录名单：原 builder/state/resolver 根不动；显式 target 不改；新增 helper 只改签名和必要可见性；startup 与 relay 正文不抽成第二层算法。附录 B 是函数保持性，不替代真实 Rust 名称解析/Send/借用验证。

## Task 3: 原子安装、编译和行为回归

**Files:** §1 的全部 20 文件。不要提交原入口仍超限或只安装了一半模块的中间状态。

- [ ] **Step 1: 原子切换源码装配，只复制本单元文件。**

```bash
test ! -e "$RUNTIME/src/runtime"
cp "$WORKDIR/candidate-src/runtime.rs" "$RUNTIME/src/runtime.rs"
cp -R "$WORKDIR/candidate-src/runtime" "$RUNTIME/src/runtime"
"$WORKDIR/venv/bin/python" "$WORKDIR/audit_runtime.py" "$WORKDIR/original.rs" "$RUNTIME/src"
cd "$BCS_ROOT"
cargo test -p bcs-collaboration-runtime 2>&1 | tee "$WORKDIR/after-tests.log"
cargo test -p bcs-collaboration-runtime --all-features 2>&1 | tee "$WORKDIR/after-all-features.log"
```

期望各 370 passed、Runtime 零新增 warning。必要编译修正只能是本范围 imports/路径/可见性；不得修改只读子模块、allow/fmt、配置/port 或删测试来通过。

- [ ] **Step 2: 比较完整测试清单，保留每个 target 的数量和顺序分组；串行重跑 progression。**

```bash
cargo test -p bcs-collaboration-runtime -- --list \
  > "$WORKDIR/after-list.txt" 2> "$WORKDIR/after-list.stderr"
diff -u "$WORKDIR/before-list.txt" "$WORKDIR/after-list.txt"
cargo test -p bcs-collaboration-runtime --test runtime_progression -- --test-threads=1 \
  2>&1 | tee "$WORKDIR/after-serial.log"
```

发现 diff 应为空，串行 **281 passed**；默认并发已由全 crate 覆盖。原 observability 并发测试的历史 flaky 不是默认豁免：若出现，必须当前 BASE 重现，不能改变日志断言或串行化属性隐藏问题。

- [ ] **Step 3: 精确运行 caller/fencing/持久化高风险入口。**

```bash
for name in \
  caller_orchestration::a_bot_submission_without_a_live_capture_refuses_the_whole_run \
  caller_orchestration::a_bot_submitted_caller_run_relays_the_session_capture_into_the_instance_association \
  caller_orchestration::human_input_response_refused_association_refresh_still_consumes_the_reply \
  caller_orchestration::run_terminal_cancels_the_caller_instance_association_release \
  fixed_loop_tests::recovery_tests::caller_node_dispatch_tests::a_protected_assignee_on_a_non_provider_target_refuses_closed \
  fixed_loop_tests::recovery_tests::dispatch_recovery_tests::accepted_dispatch_with_lost_checkpoint_write_is_not_resent \
  fixed_loop_tests::recovery_tests::concurrent_recovery_and_cancel_before_target_cas_do_not_double_dispatch; do
  log="$WORKDIR/exact-${name//::/_}.log"
  cargo test -p bcs-collaboration-runtime --test runtime_progression "$name" -- --exact --nocapture \
    2>&1 | tee "$log"
  grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$log"
done
cargo test -p bcs-collaboration-runtime --test dispatch_credential_isolation \
  2>&1 | tee "$WORKDIR/credential-isolation.log"
```

每个 exact 必须运行 1 项，不接受 filter 后 0 tests。其他权限/HumanInput/timeout/terminal/history/loop/容量场景仍由完整 281 项覆盖，不以这些点查代替全套。

- [ ] **Step 4: 重启 child phase 验证与基线持久化摘要对比。**

```bash
cargo test -p bcs-collaboration-runtime --no-run --message-format=json \
  > "$WORKDIR/after-artifacts.jsonl" 2> "$WORKDIR/after-artifacts.stderr"
python3 "$WORKDIR/restart_probe.py" after "$WORKDIR/after-artifacts.jsonl"
cmp "$WORKDIR/restart-before/summary.json" "$WORKDIR/restart-after/summary.json"
```

7 exact names/38 新 SQLite DB/76 prepare+recover phases；每 phase 日志确认 1 test passed，且实际数据库存在非空 Run 表。此检查不靠复用旧 SQLite 文件或只看父进程退出 0。

## Task 4: 范围审计与消费者验证

**Files:** 无额外源码；此前 76 个 crate 文件和 crate 外文件保持只读。获批准新增行为回归时必须先更新白名单，不改本检查为宽松目录匹配。

- [ ] **Step 1: 精确核对写入集合与物理行数。**

```bash
cd "$ROOT"
python3 - <<'PY'
import os, subprocess
from pathlib import Path
root = Path(os.environ['ROOT'])
prefix = 'apps/bcs/crates/services/bcs-collaboration-runtime/src/'
names = 'access authoring bindings caller completion definition events human_input logging node_execution opening progression recovery rerun service start terminal_event tests views'.split()
allowed = {prefix + 'runtime.rs', *(prefix + 'runtime/' + n + '.rs' for n in names)}
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).splitlines()
changed = set(git('diff', '--name-only', os.environ['BASE']))
changed.update(git('ls-files', '--others', '--exclude-standard'))
assert changed == allowed, (sorted(changed - allowed), sorted(allowed - changed))
for name in sorted(changed):
    lines = len((root / name).read_bytes().splitlines())
    assert lines <= 1000, (name, lines)
    print(lines, name)
print('PASS: exact 20-file scope; every other tracked path unchanged')
PY
git diff --check "$BASE"
```

该计数不替代 canonical CI size gate。当前 `.githooks`/workflow 入口按实际源码运行，不加 allowlist。BASE 是实现开始 commit；PR 覆盖率和模块检测使用 fetched target 的 merge-base，不能直接拿最新 dev tree diff 混入 target-only 文件。

- [ ] **Step 2: 运行 Store/MessageFlow 交叉回归和全 workspace 编译。**

```bash
cd "$BCS_ROOT"
cargo test -p bcs-message-flow -p bcs-collaboration-runtime -p bcs-collaboration-store \
  2>&1 | tee "$WORKDIR/after-cross.log"
cargo build 2>&1 | tee "$WORKDIR/build.log"
cargo build --workspace --all-features 2>&1 | tee "$WORKDIR/build-all-features.log"
cargo test --workspace --no-run 2>&1 | tee "$WORKDIR/compile-workspace.log"
cargo test --workspace --all-features --no-run 2>&1 | tee "$WORKDIR/compile-all-features.log"
cargo test --workspace --no-fail-fast 2>&1 | tee "$WORKDIR/workspace-tests.log"
BCS_BASE_REF="$TARGET_BASE" bash scripts/ci/arch-check.sh 2>&1 | tee "$WORKDIR/after-arch.log"
```

当前 scope 不改 SQL dialect/migration，Memory/SQLite 行为由 Store/Runtime 套件覆盖。完整 CI 中已有真实 MySQL conformance 不能被跳过；若需本地诊断可运行设计 §9.3 的 Store exact ignored 用例，但必须使用拥有明确所有权的空可丢弃 schema，不擅自初始化/清理保留数据库。记录未运行项，mock SQL 不是 MySQL 验证。

- [ ] **Step 3: 局部测试和范围核对通过后提交一个完整的代码拆分 commit。**

```bash
cd "$ROOT"
git add apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs \
  apps/bcs/crates/services/bcs-collaboration-runtime/src/runtime/
git diff --cached --check
git commit -m "refactor(bcs): split collaboration runtime by execution responsibility"
```

这一步仅在后续用户已授权的实施流程中执行。若需补测试，先独立提交经批准的基线回归，再整体移动；不要为“频繁提交”提交无法编译或原入口仍超限的半成品。本 commit 不代表已可 merge。

## Task 5: 不豁免的最终覆盖率、文档和交付

**Files:** 无额外源码；日志、数据库、coverage 工件不提交。

- [ ] **Step 1: 重新运行 unit coverage 的 canonical 入口。**

```bash
cd "$BCS_ROOT"
bash scripts/ci_test.sh --coverage --reuse-build 2>&1 | tee "$WORKDIR/after-unit-coverage.log"
python3 scripts/cov_gate.py --base-ref "$TARGET_BASE" --bcs-dir "$BCS_ROOT" \
  --pass-rate-min 100 --overall-line-min 84 --changed-line-min 80 \
  2>&1 | tee "$WORKDIR/after-unit-gate.log"
cp -R testresult "$WORKDIR/after-unit-reports"
```

要求 pass-rate=100%、overall **>84%**、changed-line **>=80%**。`ci_test.sh` 非 fast-fail 模式可能先产报告再返回 0，必须读 JUnit 和执行 cov_gate，不能只检查 shell exit。还要验证新的 runtime/*.rs 确实进入 Cobertura/变化分母；不能把空映射的 `gate not applicable` 当作成功。

- [ ] **Step 2: 运行统一 Singlebox，并验证本次 artifacts。**

```bash
cd "$ROOT"
bash singlebox/ci/singlebox_coverage.sh 2>&1 | tee "$WORKDIR/after-singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
cp -R singlebox/.dependencies/coverage/singlebox/reports "$WORKDIR/after-e2e-reports"
```

要求真实故事全过、runtime line>=41%、method>=36%、HTTP endpoint/CLI leaf=100%；>=20 可测变化行时 E2E changed-line **严格 >60%**，若未来更严则遵从更严者。本基线 workflow/runner 默认已是 60，不再引用旧的 50。生产搬移行必须计入分母；24 个薄委托不作为替代真实业务覆盖的证据。

端口冲突使用 runner 支持的隔离配置，不停止其他 worktree 服务。缺失覆盖不能通过新增日期/路径 waiver、排除生产文件、改 denominator/min-lines 或忽略测试解决；在既定范围不能合规通过时报告阻塞，先确认补测试的具体范围再继续。

- [ ] **Step 3: 完成 spec checklist 和 PR 验证记录，不自动发布。**

建议标题 `refactor(bcs): split collaboration runtime by execution responsibility`。
PR 按 Problem/Solution/Validation/Compatibility and risk/Spec 填写：20 文件、24 个委托、35 target、190 身份/370 测试、caller/relay 不变、数据库成本零变化依据、76 restart phases、warning 差异、两套 coverage 数字及同一 HEAD artifacts。明确哪些是独立执行、哪些仅 CI 证据、哪些未执行。

只在 required checks 和约定验收都满足后建议 merge；revert 本单元完整 commit 即可回滚，无 schema/config migration、数据修复、双写/兼容层。不要单文件 restore 留下残缺模块装配。

## 3. 规划验证边界与自检

规划时已验证（全部在工作区原版或仓库外临时完整 Cargo 副本）：

- 原版与候选默认、all-features 各 **370 passed/0 failed/0 ignored**；完整发现清单逐行一致，候选 progression 串行 **281 passed**。
- 原版和候选各 **7 exact restart names、38 SQLite cases、76 child phases**；持久化摘要一致。
- 附录 B 的 **190 函数/12 声明/4 trait impl/24 委托/35 target**检查通过；20 文件最大 631 行，root 516 行。7 内联测试仍保留原名。
- 初版临时生成出现两个 test-only root import warning，已收窄为 cfg(test) 导入；候选 Runtime 无新增 warning。原版/候选都有 bcs-bot-store 既有 unused_imports warning，未修改它。
- **未运行**本单元完整 workspace、cross suites、真实 MySQL、架构、unit coverage 或统一 Singlebox；这些是执行阶段门禁，不把临时编译等同于实现/验收完成。
- 真实工作区没有 Rust 修改；临时工具完整附录化，不依赖某台机器已有 `/tmp` 文件。

自检映射：spec G1→Task4 行数/白名单；G2→§1 归属；G3→§1–2 +附录B；G4→370发现/Task3 restart；G5→两套覆盖率/diagnostics/架构；G6→独立 crate 范围、完整 commit 与 revert。新增 caller baseline 由 §2 和精确测试覆盖，不扩大成身份体系重构。

## 附录 A：完整 AST 搬移工具

保存为 `$WORKDIR/split_runtime.py`。固定基线输入、旧 src 目录和不存在的输出目录；复制旧源码供候选编译，但仅改变 runtime.rs 并新建 runtime/。输出中的旧文件不能作为真实工作区覆盖来源。

```python
"""Usage: split_runtime.py ORIGINAL_RUNTIME_RS ORIGINAL_SRC_DIR OUTPUT_SRC_DIR.
Output directory must not exist; copies old src read-only, changes runtime only.
"""
import hashlib
import re
import shutil
import sys
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_rust

EXPECTED = '6299bec2173f92d8a96ca0db62e5322fcd5d10856072ff932b6488031c67ddf8'
P = Parser(Language(tree_sitter_rust.language()))
METHODS = {
 'caller': 'release_instance_association rearm_instance_association',
 'definition': 'compile_for_execution resolve_definition group_collaboration_definition_view load_run_compiled load_run_definition_for_view',
 'views': 'run_view judge_outputs judge_outputs_for_node pending_human_node_view run_plan_for_view state_machine_panel_message state_machine_messages_from_snapshot',
 'access': 'authorize_human_for_run authorize_run_access',
 'human_input': 'activate_human_input resume_human_ready validate_human_input_channel_for_group',
 'node_execution': 'dispatch_node fail_dispatched_node loop_context_for_node build_node_prompt evaluate_node_outcome judge_failure_details judge_upstream_outputs',
 'events': 'prepare_public_event publish_state_machine_panel_event persist_state_machine_panel_message publish_state_machine_bot_event',
 'opening': 'persisted_state_machine_opening_message opening_message_for_existing_run',
 'rerun': 'resume_materialized_rerun',
 'progression': 'dispatch_ready_targets skip_unselected_targets skip_branch_targets all_upstreams_completed fail_retryable_node_attempt resume_failed_node apply_completed_node_progression progression_run_is_active',
 'completion': 'complete_run_if_done is_chat_session fail_run complete_service_session_for_run recover_terminal_run_session',
 'recovery': 'recover_run_progression process_expired_node_timeout_candidate',
}
# Keep existing short forwarding/projection methods in the unique Service impl.
DELEGATES = {
 'access': 'get_session_state_machine_permission list_pending_human_nodes',
 'start': 'start_state_machine_run',
 'rerun': 'start_session_state_machine_run rerun_state_machine_run',
 'human_input': 'handle_session_human_input respond_human_node',
 'views': 'get_state_machine_node_run get_state_machine_session_history get_state_machine_session_history_for_view',
 'completion': 'cancel_state_machine_run_for_human cancel_state_machine_run_with_access cancel_state_machine_run cancel_session_runs cancel_group_runs delete_group_runtime_state',
 'terminal_event': 'handle_bot_terminal_event',
 'recovery': 'process_expired_node_timeouts recover_state_machine_progression cleanup_state_machine_terminal_work recover_state_machine_sessions',
 'definition': 'patch_group_collaboration_definition upgrade_group_collaboration_definition configure_group_runtime',
}
FUNCTIONS = {
 'events': 'event_timestamp content_value normalize_state_machine_event_payload workbench_event_name chat_event_state_slug bot_display_name state_machine_message_metadata state_machine_human_input_prompt_metadata state_machine_panel_metadata',
 'node_execution': 'predecessor_node_ids group_context_input participant_role_slug judge_service_error_message',
 'bindings': 'resolve_participant_bindings validate_runtime_participant_bindings referenced_participant_slots runtime_binding_bot_ids inferred_participant_role session_state_machine_permission_view group_strategy_slug invalid_participant_binding',
 'authoring': 'definition_yaml_for_record generated_authoring_yaml cleanup_metadata_authoring_yaml cleanup_participants_authoring_yaml cleanup_state_machine_authoring_yaml remove_default_projection remove_default_state_machine_defaults cleanup_state_machine_nodes_authoring_yaml cleanup_node_transitions_authoring_yaml cleanup_judge_authoring_yaml remove_empty_mapping remove_empty_sequence remove_bool_value remove_string_value remove_i64_value parse_authoring_definition_yaml validate_v2_definition_input reject_authoring_yaml_identity normalized_definition_json',
 'definition': 'ensure_candidate_definition_slot_is_compatible',
 'terminal_event': 'stale_event_reason extract_text compact_bot_terminal_event_payload',
 'human_input': 'compiled_has_human_input human_input_assignees human_input_prompt_actor_ids',
 'start': 'build_node_runs',
 'rerun': 'build_rerun_node_runs',
 'views': 'run_graph_view node_uses_judge node_sub_status apply_message_window',
 'logging': 'log_state_machine_node_dispatch log_state_machine_delivery_result log_state_machine_bot_event log_state_machine_node_result log_state_machine_transition log_state_machine_run_complete log_state_machine_run_failed log_state_machine_timeout elapsed_ms',
 'progression': 'collect_reachable_targets',
 'completion': 'final_output_text',
 'opening': 'default_state_machine_opening_message render_state_machine_opening_message render_run_opening_message json_string single_quoted_json_attr',
}

def parse(data):
 tree = P.parse(data)
 assert not tree.root_node.has_error
 return tree

def item_chunks(data, node):
 start = node.start_byte + (node.type == 'declaration_list')
 for n in node.named_children:
  if n.type in {'attribute_item', 'line_comment', 'block_comment'}: continue
  yield n, data[start:n.end_byte].decode()
  start = n.end_byte
 end = node.end_byte - (node.type == 'declaration_list')
 assert not data[start:end].strip(), 'unattached trailing comments'

def name(n): return n.child_by_field_name('name').text.decode()

def logs(raw):
 # Only tracing invocations without an explicit target change. Token-tree
 # offsets ensure no edits to messages, fields, comments or unrelated macros.
 data = raw.encode(); tree = parse(data); edits = []
 def walk(n):
  if n.type == 'macro_invocation':
   macro = n.child_by_field_name('macro').text.decode()
   if macro in {'info','warn','tracing::info','tracing::warn','tracing::debug'}:
    tok = next(c for c in n.named_children if c.type == 'token_tree')
    if not re.match(rb'\s*target\s*:', tok.text[1:-1]):
     edits.append(tok.start_byte + 1)
  for c in n.named_children: walk(c)
 walk(tree.root_node)
 for at in reversed(edits): data = data[:at] + b'target: RUNTIME_LOG_TARGET,' + data[at:]
 return data.decode()

def invert(groups):
 result = {n: module for module, names in groups.items() for n in names.split()}
 assert len(result) == sum(len(names.split()) for names in groups.values())
 return result

def main():
 source, old, out = map(Path, sys.argv[1:])
 data = source.read_bytes()
 assert hashlib.sha256(data).hexdigest() == EXPECTED, 'source drift: re-inventory, do not just replace hash'
 assert (old / 'runtime.rs').read_bytes() == data
 assert not out.exists(), 'refuse to overwrite output'
 tree = parse(data)
 methods, delegates, functions = map(invert, (METHODS, DELEGATES, FUNCTIONS))
 parts, inherent, root, exports = {}, {}, [], {}
 seen_m, seen_d, seen_f = set(), set(), set()
 def add(m, text): parts.setdefault(m, []).append(text)
 def inherent_impl(chunks): return 'impl CollaborationRuntime {' + ''.join(chunks) + '\n}\n'
 for n, chunk in item_chunks(data, tree.root_node):
  if n.type == 'function_item':
   key = name(n); module = functions[key]; seen_f.add(key)
   chunk = chunk.replace('async fn '+key, 'pub(super) async fn '+key, 1) if chunk.lstrip().startswith('async fn ') else chunk.replace('fn '+key, 'pub(super) fn '+key, 1)
   add(module, logs(chunk)); exports.setdefault(module, []).append(key)
  elif n.type == 'impl_item' and n.child_by_field_name('type').text == b'CollaborationRuntime':
   trait = n.child_by_field_name('trait')
   keep = []
   for method, raw in item_chunks(data, n.child_by_field_name('body')):
    assert method.type == 'function_item'
    key = name(method)
    if trait is None:
     if key not in methods:
      assert method.text.startswith(b'pub fn '), key
      keep.append(raw)
     else:
      seen_m.add(key)
      raw = raw.replace('async fn '+key, 'pub(super) async fn '+key, 1) if b'async fn' in method.text[:80] else raw.replace('fn '+key, 'pub(super) fn '+key, 1)
      inherent.setdefault(methods[key], []).append(logs(raw))
    elif key not in delegates: keep.append(raw)
    else:
     seen_d.add(key)
     body = method.child_by_field_name('body')
     params = method.child_by_field_name('parameters')
     args = []
     for param in params.named_children:
      if param.type == 'self_parameter': continue
      assert param.type == 'parameter'
      pattern = param.child_by_field_name('pattern'); assert pattern.type == 'identifier'
      args.append(pattern.text.decode())
     at = len(raw.encode()) - (method.end_byte - body.start_byte)
     wrapper = raw.encode()[:at].decode() + '{\n        self.' + key + '_impl(' + ', '.join(args) + ').await\n    }'
     keep.append(wrapper)
     moved = raw.replace('async fn '+key, 'pub(super) async fn '+key+'_impl', 1)
     inherent.setdefault(delegates[key], []).append(logs(moved))
   if trait is None: root.append(inherent_impl(keep))
   else:
    assert trait.text == b'CollaborationRuntimeService'
    add('service', '#[async_trait]\nimpl CollaborationRuntimeService for CollaborationRuntime {' + ''.join(keep) + '\n}\n')
  elif n.type == 'mod_item' and name(n) == 'tests':
   b = n.child_by_field_name('body')
   add('tests', data[b.start_byte+1:b.end_byte-1].decode())
  else: root.append(chunk)
 assert seen_m == methods.keys() and seen_d == delegates.keys() and seen_f == functions.keys()
 for module, rows in inherent.items(): add(module, inherent_impl(rows))
 # Decide which free helpers must remain available to their former siblings.
 # Exclude comments and strings when finding cross-module identifier references.
 def identifiers(text):
  tree = parse(text.encode()); result=set()
  def visit(n):
   if n.type in {'identifier','type_identifier'}: result.add(n.text.decode())
   for c in n.named_children: visit(c)
  visit(tree.root_node); return result
 external = set()
 for path in old.glob('runtime_*.rs'): external.update(identifiers(path.read_text()))
 references = {m: identifiers(''.join(chunks)) for m, chunks in parts.items()}
 for module, names in exports.items():
  needed = external.union(*(v for k,v in references.items() if k != module))
  keep = [n for n in names if n in needed]
  local = set(names) - set(keep)
  parts[module] = [re.sub(r'pub\(super\) ((?:async )?fn) ('+'|'.join(map(re.escape,sorted(local)))+r')\b', r'\1 \2', text) if local else text for text in parts[module]]
  exports[module] = keep
 shutil.copytree(old, out)
 directory = out / 'runtime'; assert not directory.exists(); directory.mkdir()
 root.append('\nconst RUNTIME_LOG_TARGET: &str = module_path!();\n')
 root.append('\n#[cfg(test)]\nuse opening::default_state_machine_opening_message;\n#[cfg(test)]\nuse start::build_node_runs;\n')
 exports['opening'].remove('default_state_machine_opening_message')
 exports['start'].remove('build_node_runs')
 for module in sorted(parts):
  root.append(('\n#[cfg(test)]' if module == 'tests' else '') + '\nmod ' + module + ';\n')
  if exports.get(module): root.append('use ' + module + '::{\n    ' + ', '.join(exports[module]) + ',\n};\n')
 for module, rows in parts.items():
  text = ('' if module == 'tests' else 'use super::*;\n') + ''.join(rows).strip('\n') + '\n'
  (directory / (module+'.rs')).write_text(text)
 (out / 'runtime.rs').write_text(''.join(root).strip('\n')+'\n')
 files = [out/'runtime.rs',*sorted(directory.glob('*.rs'))]
 for p in files:
  parse(p.read_bytes()); count = len(p.read_bytes().splitlines()); assert count<=1000,(p,count)
  print(p.relative_to(out),count)
 print(f'{len(files)} files; {len(methods)} moved inherent methods; {len(delegates)} delegates; {len(functions)} free helpers')

if __name__ == '__main__': main()
```

## 附录 B：独立函数/声明/委托/日志 target 审计

保存为 `$WORKDIR/audit_runtime.py`。不消除任意空白、字符串或注释；正文只允许精确日志 target 注入。
可见性、24 个方法改名和直接委托按原身份核对；还需 Task4 的改动范围检查和真实 Cargo 编译。

```python
"""Usage: audit_runtime.py ORIGINAL_RUNTIME_RS CANDIDATE_SRC_DIR.
Independent exact signature/body/delegate checks, not a compiler or test substitute.
"""
import re
import sys
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_rust

P = Parser(Language(tree_sitter_rust.language()))
OLD, SRC = Path(sys.argv[1]), Path(sys.argv[2])
MODULES = set('access authoring bindings caller completion definition events human_input logging node_execution opening progression recovery rerun service start terminal_event tests views'.split())

def parse(data):
 t = P.parse(data); assert not t.root_node.has_error
 return t

def normalize(data):
 # This exact injected clause is the only permitted body change. Preserve all
 # other bytes, including strings, SQL/JSON, comments, existing log targets.
 return data.replace(b'target: RUNTIME_LOG_TARGET,', b'')

def extract(data, scope='', follow=False):
 rows, declarations, mounts, impl_attrs, wrappers = {}, {}, {}, {}, {}
 def merge(dst, src):
  assert not dst.keys() & src.keys(), ('duplicate identities', dst.keys() & src.keys())
  dst.update(src)
 def visit(parent, scope):
  attrs=[]
  for n in parent.named_children:
   if n.type == 'attribute_item': attrs.append(n.text); continue
   if n.type in {'line_comment','block_comment'}: continue
   attr=b'\n'.join(attrs); attrs=[]
   name=n.child_by_field_name('name')
   name=name.text.decode() if name else ''
   if n.type == 'function_item':
    key=(scope,name); raw=n.text
    body=n.child_by_field_name('body')
    if scope == 'CollaborationRuntime' and name.endswith('_impl'):
     old_name=name[:-5]; key=('CollaborationRuntimeService for CollaborationRuntime',old_name)
     raw=raw.removeprefix(b'pub(super) ').replace(name.encode(),old_name.encode(),1)
    elif scope in ('', 'CollaborationRuntime'):
     raw=raw.removeprefix(b'pub(super) ')
    value=(attr,normalize(raw))
    if scope=='CollaborationRuntimeService for CollaborationRuntime' and re.fullmatch(rb'\{\s*self\.[a-z_]+_impl\([^{}]*\)\.await\s*\}',body.text):
     merge(wrappers,{key:value})
    else: merge(rows,{key:value})
   elif n.type == 'impl_item':
    typ=n.child_by_field_name('type').text.decode(); trait=n.child_by_field_name('trait')
    impl_scope=(trait.text.decode()+' for ' if trait else '')+typ
    if trait is not None: merge(impl_attrs,{impl_scope:attr})
    visit(n.child_by_field_name('body'),impl_scope if not scope else scope+'::'+impl_scope)
   elif n.type == 'mod_item':
    body=n.child_by_field_name('body')
    if body is not None: visit(body,name)
    elif follow and name in MODULES:
     child=extract((SRC/'runtime'/f'{name}.rs').read_bytes(), 'tests' if name=='tests' else '')
     for dst,src in zip((rows,declarations,mounts,impl_attrs,wrappers),child):merge(dst,src)
    else: merge(mounts,{name:(attr,n.text)})
   elif n.type in {'struct_item','enum_item','const_item','type_item'}:
    if name=='RUNTIME_LOG_TARGET':
     assert attr==b'' and n.text==b'const RUNTIME_LOG_TARGET: &str = module_path!();'
     continue
    merge(declarations,{(scope,n.type,name):(attr,n.text)})
 visit(parse(data).root_node,scope)
 return rows,declarations,mounts,impl_attrs,wrappers

before,old_decls,old_mounts,old_attrs,_=extract(OLD.read_bytes())
after,new_decls,new_mounts,new_attrs,wrappers=extract((SRC/'runtime.rs').read_bytes(),follow=True)
old_mounts.pop('tests',None);new_mounts.pop('tests',None)
assert old_mounts==new_mounts, 'old module mounts changed'
assert old_decls==new_decls, 'types/fields/constants changed'
assert old_attrs==new_attrs, 'trait implementation/attributes changed'
assert before.keys()==after.keys(),(before.keys()-after.keys(),after.keys()-before.keys())
for key,value in before.items():assert value==after[key],('function signature/body/attributes changed',key)
for key,(attrs,wrapper) in wrappers.items():
 attr,raw=before[key];node=parse(raw).root_node.named_children[0]
 body=node.child_by_field_name('body')
 args=[p.child_by_field_name('pattern').text.decode() for p in node.child_by_field_name('parameters').named_children if p.type=='parameter']
 expected=raw[:body.start_byte]+('{\n        self.'+key[1]+'_impl('+', '.join(args)+').await\n    }').encode()
 assert attrs==attr and wrapper==expected,('delegate changed',key)
assert len(wrappers)==24,len(wrappers)
files=[SRC/'runtime.rs',*(SRC/'runtime'/f'{m}.rs' for m in sorted(MODULES))]
assert len(files)==20
for p in files:assert len(p.read_bytes().splitlines())<=1000,p
# Implicit tracing targets must not acquire the new child module's path.
log_injections=0
for p in files:
 tree=parse(p.read_bytes())
 def visit(n):
  global log_injections
  if n.type=='macro_invocation':
   macro=n.child_by_field_name('macro').text.decode()
   if macro in {'info','warn','tracing::info','tracing::warn','tracing::debug'}:
    tok=next(c for c in n.named_children if c.type=='token_tree')
    if b'target: RUNTIME_LOG_TARGET,' in tok.text:log_injections+=1
    assert p.name=='runtime.rs' or re.match(rb'\s*target\s*:',tok.text[1:]),('implicit log target drift',p,n.start_point)
  for c in n.named_children:visit(c)
 visit(tree.root_node)
assert log_injections==35,log_injections
print(f'PASS: {len(before)} original functions, {len(old_decls)} declarations, {len(old_attrs)} trait impls, 24 exact delegates, 35 log targets; 20 files <=1000')
```

## 附录 C：restart child 的真实执行探针

保存为 `$WORKDIR/restart_probe.py`。使用本次 Cargo JSON 中唯一 runtime_progression 测试二进制；
每个 phase 新进程，清理继承的其他 phase 变量，检查真实测试计数与持久化 Run。
数据库只在临时 WORKDIR 创建，不读取或清理用户数据库。

```python
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

work = Path(os.environ["WORKDIR"])
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
