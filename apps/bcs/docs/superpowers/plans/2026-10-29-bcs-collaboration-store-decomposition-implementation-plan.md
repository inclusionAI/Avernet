# BCS Collaboration Store Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成大 Rust 文件拆分的第 4 单元：将 Collaboration Store 的 5,017 行 `src/lib.rs` 按 Memory/SQL 与持久化能力拆分，保持公开入口、共享状态、事务/CAS/错误语义及数据库访问成本不变，使本单元所有新增、修改 Rust 文件不超过 1,000 物理行。

**Architecture:** 三个公开 Store 类型、私有记录、构造器、常量和既有子模块留在 crate 根；Memory trait 实现整体搬移。SQL Run 保留一个完整 trait impl，把 28 个长方法原文搬入 `sql_*` inherent helper，原方法仅直接 `.await` 委托；其余已有检查点转发不变，不增加子仓储、锁、缓存或新 trait。

**Tech Stack:** Rust 2024、async-trait、Tokio、现有 DbPlugin/MemoryEventStore、SQLite 与 MySQL；规划/核对工具为临时 Python 3.12、tree-sitter 0.25.2、tree-sitter-rust 0.24.2，不加入产品依赖。

---

## 0. 授权、基线与完成定义

- 设计依据：[大文件拆分设计](../specs/2026-10-28-bcs-large-rust-files-decomposition-design.md) §8–§11。本单元与 CLI、Runtime 生产实现独立，不要求它们先落地，不修改第 1/2/3 单元的实现。
- 规划日期：**2026-10-29**；当前分支 `docs/bcs-large-rust-files-design`，实测 HEAD **`5f2492498700d36aaa3552a4238ad4b81469c114`**，与本地 `origin/dev` 的 merge-base 为 **`86252c1e98d812f18166f930d9f7c2ceafd06f46`**。这不是远端最新状态声明；执行时重新 fetch/确定基线，不沿用早期计划的 `b57b8e2d7`。
- 源文件：`apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs`，**5,017 物理行**，SHA-256 **`c0a1c31d9046cec72440d456e772b8cd9e97382619b33cc16f9cee3a9ae6edea`**。
- AST 基线：**235 个函数身份**（含 trait/inherent 方法和测试辅助）、**10 个根类型/常量声明**；SQL Run trait 1,497 行、Memory Run trait 696 行（均不含前置属性）。SQL Run 仅 28 个方法改变装配方式。
- 实际测试发现：lib **7**、`memory_store` **11**、`mysql_store` **115**，总计 **133**，其中 1 个真实 MySQL 测试默认 ignored。包括 `mysql_store` 通过原路径挂入的 bootstrap migration tests；不能把这部分漏出发现清单。
- **本轮仅生成计划和规划验证**，不在当前工作区修改 Rust，不自动 commit/push/建 PR。实施需单独授权，并在实现工作区执行。

完成定义：18 个受控 Rust 文件（1 修改、17 新建）均 <=1,000 行；235 个原函数的签名/函数体/属性按白名单保持；新增 28 个直接委托；133 项测试按 target 和完整名称一一不变；默认/all-features 零新增 warning；Memory、SQLite、真实 MySQL 及交叉回归、适用架构和统一 Singlebox 门禁有本次证据。不能用默认的 132 passed + 1 ignored 代替真实 MySQL 验证。

## 1. 固定文件结构与接口

下表路径相对 `apps/bcs/crates/services/bcs-collaboration-store/`。行数是附录 A 在规划指纹下生成的临时副本实测，不是给未来变更的豁免。

| 操作 | 文件 | 职责 | 规划行数 |
| --- | --- | --- | ---: |
| Modify | `src/lib.rs` | 三个公开 Store、StoreInner/DefinitionSourceRecord、构造器、5 个原常量、原/新子模块装配、私有具名 imports | 197 |
| Create | `src/memory_definition.rs` | 完整 Memory definition/source/snapshot trait | 160 |
| Create | `src/memory_binding.rs` | 完整 Memory Group binding trait | 71 |
| Create | `src/memory_runs.rs` | 完整唯一 Memory Run trait，含原 eventful transition 锁内流程 | 698 |
| Create | `src/memory_events.rs` | Memory event trait、两种实现共用的 state_machine_transition_events | 74 |
| Create | `src/memory_transition.rs` | clone-candidate 应用、node_mut、retry/terminal/failure 状态辅助 | 196 |
| Create | `src/sql_definition.rs` | SQL definition trait、4 个原 definition inherent helper | 409 |
| Create | `src/sql_binding.rs` | SQL binding trait、default/current-version 条件写 | 272 |
| Create | `src/sql_runs.rs` | 唯一 SQL Run trait，原检查点转发 + 28 个直接委托 | 284 |
| Create | `src/sql_run_lifecycle.rs` | create/idle/rerun、Run 点查/分页/状态，共 10 方法 | 426 |
| Create | `src/sql_node_state.rs` | Node 查询/activation/完成/响应/失败/retry/skip/过期，共 14 方法 | 562 |
| Create | `src/sql_transition.rs` | eventful transition 完整事务，1 方法 | 292 |
| Create | `src/sql_correlation.rs` | delivery/alias 持久化与查询，3 方法 | 161 |
| Create | `src/sql_events.rs` | 完整 SQL event trait | 94 |
| Create | `src/sql_mapping.rs` | 原 row/param/status/DB error helper（含 current_millis） | 323 |
| Create | `src/snapshot_validation.rs` | envelope、JSON/hash、definition conflict helper | 52 |
| Create | `src/template.rs` | 完整 template trait、tag/language 批量查询和专属 helper | 212 |
| Create | `src/tests.rs` | 原 tests 模块内部内容原文，7 测试和局部 fixtures | 808 |

### 1.1 只读边界

- 同 crate 原 `src/{draft,judge,history,opening,dispatch,publication,terminal_im,recovery_gap,terminal_cleanup,branch_skip}.rs` **10 文件**全部原文保留，逻辑父级仍是 crate 根。
- 所有原 `tests/**`、bootstrap migrations 源码、SQL migration/fixture、Service/Plugin API、配置、Cargo.toml/Cargo.lock、coverage 脚本和门槛只读。`tests/mysql_store.rs` 本来超限也不在此轮修改，不能为加断言把它变成新的超限修改文件。
- `pub mod draft` 公开路径保持，三个公开类型不搬入 private module 后再 `pub use`；根私有字段不改为 public/pub(crate)。既有检查点模块继续访问原 `inner/db/env/flavor`。
- 不拆 `StoreInner`，不更换 Arc/RwLock，不让不同 capability 创建各自 store；不更改业务返回类型、迁移校验和、依赖图、composition root。
- 永久 nameless-result fixture 保持原样。不得借此执行 Runtime、CLI、Draft 或其他超长文件清理。

### 1.2 可见性、方法解析和测试装配

- 新模块均是 `lib.rs` 的直接私有子模块；采用与现有检查点相同的 `use super::*;`，根仅具名私有导入实际跨模块 helper，**不使用 `pub use` 或 blanket allow**。
- 需要跨模块的 free helper 只开放 `pub(super)`；仅在自身文件使用的 mapping/template helper 保持私有，完整清单由附录 A 固定。
- `db_i32` 还被原 lib tests 使用，但生产侧不需要从根导入。保持它的 `pub(super)`，根使用 `#[cfg(test)] use sql_mapping::db_i32;`，不引入生产 unused_imports。
- 原 SQL definition helper 中只有 `definition_metadata` 被 `sql_binding` 调用，开放 `pub(super)`；另外三个仍私有。跨文件 inherent 方法不是自动可见的，不能只凭同类型 impl 推断访问有效。
- 28 个新 inherent helper 统一为 `pub(super) async fn sql_<原方法名>`；参数、返回类型、原函数体完全保留。不得用与 trait 同名的 inherent 方法，以免 shadow 或自递归；原内部 `self.get_run` 等调用仍走原 trait 入口，不顺带改成 helper 直调。
- `#[async_trait]` 仍只修饰完整 trait impl。helper 不另加 async_trait，不 Box、不 spawn、不增加 clone/重试/外部 I/O。
- 根为 `#[cfg(test)] mod tests;`；`src/tests.rs` 直接承载旧模块内部内容，**不能再包一层 `mod tests`**。所以 7 个 `tests::<name>` 保持原全名。
- 当前待移源码没有 tracing/module_path!/include_str! 路径敏感调用；执行时若指纹变化引入这些项，先重新评估，不能照搬此前 CLI 的日志改写。

### 1.3 SQL 委托示例（其余由附录 A 的完整 28 项清单生成）

```rust
// src/sql_runs.rs，仍在 #[async_trait] impl StateMachineRunRepoPort 内
async fn create_run(
    &self,
    run: StateMachineRun,
    nodes: Vec<StateMachineNodeRun>,
) -> ServiceResult<()> {
    self.sql_create_run(run, nodes).await
}
```

`src/sql_run_lifecycle.rs` 的 `sql_create_run` 使用原 `create_run` 的完整正文，唯一签名变化是增加 `pub(super)` 和 `sql_` 前缀。附录 A 按 AST 复制真实正文，非用省略号补写；附录 B 独立比对签名、属性、正文以及 wrapper 参数顺序。

## 2. 必须保持的存储语义与成本

| 风险 | 必须核对的事实 | 现有回归入口 |
| --- | --- | --- |
| eventful transition 半提交 | Memory 原锁内 clone-candidate → 校验 → shared event store 提交 → 替换状态；SQL 业务 mutation 和 EventAppendTransactionPlan 位于同一 transaction，step offset 不变 | memory_store 的事件成功/失败；mysql_store 的 sqlite event batch |
| Session rerun 竞争 | 先查直接后继，原 Session 行锁、Run 条件写、activation、snapshot/opening override 继承在同一事务；失败后只按原流程重查 | lib rerun 测试、fixed-loop/nested-loop、batched_nodes |
| CAS/租约绕过 | env、active、attempt、status、owner/token/deadline、affected_rows 和条件释放保持 | mysql CAS、failure/judge/dispatch/history/publication/terminal 子套件 |
| 错误被吞掉 | 保留所有 `?`、map_err 分类、0 行语义、alias 第二次 query 失败；不能把损坏 JSON/缺失 metadata 改为 default | mysql_runtime_propagates_database_failures_across_repository_operations 等 |
| 数据源/类型不兼容 | source YAML/hash/metadata、v1/v2 snapshot envelope/compiler/hash、不完整持久化 plan 错误保持 | lib source、mysql snapshot、真实 MySQL Text/Prepared |
| 无意扩大 DB/锁成本 | 同一实参进入原正文，无新连接/scan/缓存/锁，保留现有批次和事务生命周期 | 下方成本表 + recorded statements + SQLite + MySQL |

下表区分 **DbPlugin API 次数、事务内 statement 数、网络往返**；一次 `transaction` 不是一次 SQL 往返。已追踪 `bcs-db-mysql/src/lib.rs::transaction_with_options` 的逐 step 执行和 `manager.rs::with_transaction` 的取连接/BEGIN/COMMIT/ROLLBACK；SQLite 则在原同一连接/事务内逐步执行。预期变化均为 **零**，不在本 PR 优化既存成本。

| 操作/路径 | 基线调用/规模与保持要求 |
| --- | --- |
| create_run | 1 transaction，1 Run INSERT + 有 Node 时 1 多行 INSERT；原非 guarded 路径没有在此 helper 强加 48 行分块，不能声称所有 create 都有相同上界 |
| create_run_if_session_idle | 1 transaction，2 个前置 step + ceil(N/48) 个 guarded Node INSERT；每块 <=48 行、<=818 bind 参数；成功最多写 1 Run + N Nodes，冲突不创建 orphan Nodes |
| create_rerun_if_session_idle | 先 1 次直接后继 query；新建分支 1 transaction，4 个前置 step + ceil(N/48) 插入；无写入或事务错误时保留原一次后继重查。Session/Run/snapshot 与 Node 批次不能分事务 |
| N=2048 的既有容量用例 | 43 个 Node batch，idle-create/rerun 分别 45/47 step；保持重复 Node 导致整事务回滚，不以仅完成前 48 条视为成功 |
| Run progression/expired candidates | 原单 query，保留 cursor、ORDER BY、LIMIT、env 和 active 过滤；上界为传入 limit；保留 SQLite index/无 TEMP B-TREE 断言 |
| correlation lookup/alias | request 命中 1 query，否则最多 2 query；alias 注册仍 1 transaction/2 UPDATE，第一结果 0 行按原错误返回 |
| definition upsert | 原 metadata lookup，按冲突/相同版本/YAML backfill/新建分支执行；新建为 blob+definition 两步 transaction 后再读 hash。不把多次访问“简化”为盲 upsert |
| template list_entries | 空列表 1 query；非空 3 query（主列表、批量 tags、批量 languages），不变为每 template 各查。原列表/IN 长度无新增分页上限，本轮不宣称修复这一既有规模风险 |
| Memory transition | 同一 StoreInner 写锁和 candidate clone，锁内 await event commit 的范围原样保留；不得为降低锁时间把持久化前移/后移 |

并发/持续失败路径：不增 task、重试循环或额外连接；保持现有 Session 锁串行化、单后继约束和失败前缀回滚。大批次仍在一个事务占用连接，不能把 statement 有界等同于事务时长/全系统负载有界。当前 conformance 是回归证据，不是生产压测结论。

## Task 1: 建立实现工作区、冻结原文与门禁基线

**Files:** 仅读取 root/local AGENTS、CLAUDE、CONTEXT-MAP、架构规则和本设计；Store 无独立 CONTEXT.md。辅助工具/日志写入临时 WORKDIR。

- [ ] **Step 1: 按 using-git-worktrees 复用/建立合适实现工作区。** 不在 docs 分支执行搬运，不并发让两个 agent 改根装配；安装该 worktree hooks。以下命令在已选择的实现工作区根运行（分支创建遵从用户工作流）。

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
bash scripts/install_git_hooks.sh
git fetch origin dev
export ROOT="$(git rev-parse --show-toplevel)"
export BCS_ROOT="$ROOT/apps/bcs"
export BASE="$(git rev-parse HEAD)"
export TARGET_BASE="$(git merge-base origin/dev HEAD)"
export STORE="$BCS_ROOT/crates/services/bcs-collaboration-store"
export WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/bcs-store-split.XXXXXX")"
{
  printf 'set -euo pipefail\n'
  printf 'export ROOT=%q BCS_ROOT=%q BASE=%q TARGET_BASE=%q STORE=%q WORKDIR=%q\n' \
    "$ROOT" "$BCS_ROOT" "$BASE" "$TARGET_BASE" "$STORE" "$WORKDIR"
  printf 'cd "$BCS_ROOT"\n'
} > "$WORKDIR/env.sh"
cp "$STORE/src/lib.rs" "$WORKDIR/original.rs"
printf '%s\n' "$BASE" "$TARGET_BASE" > "$WORKDIR/revisions.txt"
(rustc -Vv; cargo -V) > "$WORKDIR/toolchain.txt"
shasum -a 256 "$WORKDIR/original.rs"
```

核对 hash 与 §0 一致；若不同，停下重新生成 inventory/归属/保持性基准，**不只是改 EXPECTED 常量绕过检查**。新的业务方法必须全部纳入清单。后续 Bash 调用先 source 保存的 `env.sh`（同时恢复 cwd、变量、`set -euo pipefail`）。

- [ ] **Step 2: 把附录 A/B 原样保存为 `$WORKDIR/split_store.py` / `$WORKDIR/audit_store.py`，准备只用于核对的解析环境。**

```bash
uv venv --python 3.12 "$WORKDIR/venv"
uv pip install --python "$WORKDIR/venv/bin/python" --index-url https://pypi.org/simple \
  tree-sitter==0.25.2 tree-sitter-rust==0.24.2
```

不使用曾导致 inventory 崩溃的 tree-sitter 0.26.0，不改项目 pyproject/Cargo 依赖。

- [ ] **Step 3: 保存 target-aware 测试发现和实际默认/all-feature 基线。**

```bash
cd "$BCS_ROOT"
for target in lib memory_store mysql_store; do
  if [ "$target" = lib ]; then args=(--lib); else args=(--test "$target"); fi
  cargo test -p bcs-collaboration-store "${args[@]}" -- --list \
    > "$WORKDIR/before-$target.list" 2> "$WORKDIR/before-$target.stderr"
done
cargo test -p bcs-collaboration-store 2>&1 | tee "$WORKDIR/before-tests.log"
cargo test -p bcs-collaboration-store --all-features 2>&1 | tee "$WORKDIR/before-all-features.log"
cargo test -p bcs-message-flow -p bcs-collaboration-runtime -p bcs-collaboration-store \
  2>&1 | tee "$WORKDIR/before-cross.log"
```

期望 Store 7 + 11 + 114 passed /1 ignored，无 warning。交叉套件以执行时的发现为准，不引用旧 PR 的数字。失败保留日志并在同一 BASE 重现；不得用历史 flaky 清单直接豁免新失败。

- [ ] **Step 4: 移动生产代码前运行架构及统一覆盖率基线。**

```bash
cd "$ROOT"
BCS_BASE_REF="$TARGET_BASE" bash apps/bcs/scripts/ci/arch-check.sh \
  2>&1 | tee "$WORKDIR/before-arch.log"
bash singlebox/ci/singlebox_coverage.sh 2>&1 | tee "$WORKDIR/before-singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
cp -R singlebox/.dependencies/coverage/singlebox/reports "$WORKDIR/before-reports"
```

分别运行并记录真实退出码。某门禁失败导致 shell 停止时，先定位/记录基线，恢复 env 后再运行其他独立基线，不能用 `|| true` 把失败显示为通过。架构工具缺失按 `.github/workflows/architecture-checks.yml` 准备；PR-gate 要求的描述文件按其当前格式填写，不伪造通过。

本轮搬迁的生产行进入 E2E changed-line 分母。先检查每个目标能力的真实用户故事可达性；已有 coverage 不足时停止并报告。增加/修改公共入口 E2E 场景须先明确新增文件范围和授权，不扩充本文的 18 文件白名单、不改排除目录或分母。

## Task 2: 验证结构检查能失败，再生成完整候选

**Files:** 临时 `$WORKDIR/{original.rs,split_store.py,audit_store.py,candidate/}`，暂不改真实源码。

- [ ] **Step 1: 对未拆分源码执行结构检查，确认 RED。** 纯搬移不需要人为制造业务失败；红灯针对尚未满足的唯一委托/文件大小结构。

```bash
if "$WORKDIR/venv/bin/python" "$WORKDIR/audit_store.py" "$WORKDIR/original.rs" "$STORE/src" \
  > "$WORKDIR/structure-red.log" 2>&1; then
  echo 'ERROR: structural checker accepted the unsplit source' >&2
  exit 1
fi
grep -F 'AssertionError: 0' "$WORKDIR/structure-red.log"
```

这里应因 28 个委托尚不存在而报 `AssertionError: 0`；若是 ImportError/语法错/文件不存在，不算 RED。

- [ ] **Step 2: 按完整 AST 清单生成候选并独立核对。**

```bash
"$WORKDIR/venv/bin/python" "$WORKDIR/split_store.py" \
  "$WORKDIR/original.rs" "$WORKDIR/candidate" | tee "$WORKDIR/candidate-lines.txt"
"$WORKDIR/venv/bin/python" "$WORKDIR/audit_store.py" \
  "$WORKDIR/original.rs" "$WORKDIR/candidate"
```

期望 `235 function identities, 10 declarations, 28 exact delegates; 18 files <=1000`。候选目录已存在时生成器拒绝覆盖；不要向已有目录重复混合不同基线。

- [ ] **Step 3: 人工核对根装配和三类移动。**

```bash
cat "$WORKDIR/candidate/lib.rs"
diff -u "$WORKDIR/original.rs" "$WORKDIR/candidate/lib.rs" > "$WORKDIR/root-move.diff" || test "$?" -eq 1
sed -n '1,110p' "$WORKDIR/candidate/sql_runs.rs"
```

逐项对照 §1/§2：Memory trait 完整；SQL wrapper 参数不 clone 不换序；SQL string/transaction steps 原文；tests 只去掉旧外壳。生成器保留旧注释/属性，不用行号切割、不全局格式化。若需要修改候选，必须记录与生成结果的精确差异，并更新本计划白名单和检查；不靠压缩一行来降行数。

## Task 3: 原子切换装配并检查所有原行为

**Files:** §1 中全部 18 文件。这些共享根状态，**一次切换**；不能在根仍 >1,000 行或模块只装了一半时提交所谓阶段性完成。

- [ ] **Step 1: 将已核对的 18 文件复制到实现工作区。**

```bash
cp "$WORKDIR/candidate/"*.rs "$STORE/src/"
"$WORKDIR/venv/bin/python" "$WORKDIR/audit_store.py" "$WORKDIR/original.rs" "$STORE/src"
cd "$BCS_ROOT"
cargo test -p bcs-collaboration-store 2>&1 | tee "$WORKDIR/after-tests.log"
cargo test -p bcs-collaboration-store --all-features 2>&1 | tee "$WORKDIR/after-all-features.log"
```

期望默认/all-features 各 132 passed、1 ignored。必须检查所有 compiler diagnostics 为零新增 warning，不运行 cargo fix/fmt，不添加 allow。Rust 编译是隐私/方法解析验证，不能用 Python PASS 替代。

- [ ] **Step 2: 比较每个 target 的完整测试名，并串行再跑一次。**

```bash
for target in lib memory_store mysql_store; do
  if [ "$target" = lib ]; then args=(--lib); else args=(--test "$target"); fi
  cargo test -p bcs-collaboration-store "${args[@]}" -- --list \
    > "$WORKDIR/after-$target.list" 2> "$WORKDIR/after-$target.stderr"
  diff -u "$WORKDIR/before-$target.list" "$WORKDIR/after-$target.list"
done
cargo test -p bcs-collaboration-store -- --test-threads=1 \
  2>&1 | tee "$WORKDIR/after-serial.log"
```

三份 diff 都应为空，包含原测试属性/ignored、bootstrap migration 后代。没有移动 integration tests，不需要修改它们的相对 include/path。

- [ ] **Step 3: 精确执行高风险现有回归，防止用过滤后 0 tests 冒充成功。**

```bash
for name in \
  mysql_runtime_cas_failures_return_false \
  mysql_runtime_propagates_database_failures_across_repository_operations \
  mysql_alias_lookup_propagates_second_query_failure \
  mysql_progression_query_is_bounded_and_propagates_database_failure \
  batched_nodes::mysql_large_one_shot_and_rerun_use_bounded_statement_counts \
  batched_nodes::sqlite_batched_nodes_preserve_guard_and_atomicity \
  sqlite_run_start_and_public_event_batch_commit_in_one_transaction; do
  log="$WORKDIR/exact-${name//::/_}.log"
  cargo test -p bcs-collaboration-store --test mysql_store "$name" -- --exact --nocapture \
    2>&1 | tee "$log"
  grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$log"
done
cargo test -p bcs-collaboration-store --test memory_store \
  event_append_failure_rolls_back_run_start_and_prior_event -- --exact --nocapture \
  2>&1 | tee "$WORKDIR/exact-memory-rollback.log"
grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$WORKDIR/exact-memory-rollback.log"
```

这些测试连同全套原测试验证成功、冲突、写失败和 2048 Node 批次成本；不重写已有 RecordingDb、AlwaysFailDb 或断言。若发现真正行为 bug，单独报告，不把修复混在纯拆分里。

- [ ] **Step 4: 检查只读边界、public API 与差异卫生，形成首个代码提交。**

附录 B 验证根类型/常量原文；原 integration tests 已从外部 crate 使用 public Store，`draft` 挂载保持。再运行 Task 5 的范围检查命令，确保 10 个老模块、API 和 migration 没变。全部局部验证通过后可在已授权的实施流程中提交：

```bash
cd "$ROOT"
git diff --check
git add apps/bcs/crates/services/bcs-collaboration-store/src/lib.rs \
  apps/bcs/crates/services/bcs-collaboration-store/src/memory_{definition,binding,runs,events,transition}.rs \
  apps/bcs/crates/services/bcs-collaboration-store/src/sql_{definition,binding,runs,run_lifecycle,node_state,transition,correlation,events,mapping}.rs \
  apps/bcs/crates/services/bcs-collaboration-store/src/{snapshot_validation,template,tests}.rs
git commit -m "refactor(bcs): split collaboration store by persistence responsibility"
```

不要单独提交仅 tests/support 搬移但根仍超限的中间状态。该提交不表示集成验收或允许 merge。

## Task 4: 真实 MySQL 与消费者交叉验证

**Files:** 无新增/修改；使用现有 `tests/mysql_store.rs`、`tests/support/real_mysql_fixed_loop.rs` 和相应消费者套件。

- [ ] **Step 1: 在 BASE 与实现 HEAD 分别运行真实 MySQL ignored 测试。**

使用 `.github/workflows/unit-tests.yml` 对应的 MySQL 8.4/公开依赖环境；`BCS_TEST_MYSQL_URL` 由测试环境注入，必须指向明确属于本次测试的**空、可丢弃 schema**。不打印 URL/密码、不使用保留开发数据。基线在干净 BASE 工作区运行同一命令，HEAD 在实现工作区运行，分别保存日志。

```bash
cd "$BCS_ROOT"
: "${BCS_TEST_MYSQL_URL:?provide an owned disposable empty test schema}"
cargo test -p bcs-collaboration-store --test mysql_store \
  real_mysql_fixed_loop::real_mysql_fixed_loop_snapshot_and_rerun_contract \
  -- --ignored --exact --nocapture 2>&1 | tee "$WORKDIR/real-mysql.log"
grep -F 'test result: ok. 1 passed; 0 failed; 0 ignored;' "$WORKDIR/real-mysql.log"
test "$(grep -c 'real MySQL snapshot contract:' "$WORKDIR/real-mysql.log")" -eq 4
```

必须实际覆盖 Text/Prepared × fixed-loop/nested-loop 4 组合，既有测试还覆盖历史/dispatch/judge/publication/terminal、scope、批次、snapshot 和 rerun rollback。保留 10 张目标表的所有权检查和测试自行清理，不提前创建这些表让测试失去安全检查。

这个用例使用历史 pre-loop fixture + 原 028 migration，不等于完整 migration chain；后者继续按现有 `bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored --nocapture` 的 CI 入口在**另一专用测试 schema**验证。不得在此测试的空 schema 上先跑全量初始化，也不得改旧 migration 适配搬移。

缺少真实 MySQL 时，记录未执行和需要的远端 job/commit/run 证据，状态为待验收；SQLite 或 RecordingDb 不能替代 dialect 验证。

- [ ] **Step 2: 运行交叉消费者回归。**

```bash
cd "$BCS_ROOT"
cargo test -p bcs-message-flow -p bcs-collaboration-runtime -p bcs-collaboration-store \
  2>&1 | tee "$WORKDIR/after-cross.log"
```

Runtime 既有恢复/重启测试仍走原身份和持久化 Store；Store 不改 runtime trait/contracts。并发失败必须与 Task 1 的 fresh BASE 比对，不能引用 #3209/#3240 的历史失败充当解释。

## Task 5: 最终门禁、范围审计和交付

**Files:** 无额外源码。日志/coverage 工件保存在 WORKDIR/runner artifact 目录，不提交运行状态。

- [ ] **Step 1: 检查精确文件集合、每个改动源码的物理行数与只读文件。**

```bash
cd "$ROOT"
python3 - <<'PY'
import os, subprocess
from pathlib import Path
root = Path(os.environ['ROOT'])
prefix = 'apps/bcs/crates/services/bcs-collaboration-store/src/'
names = 'lib memory_definition memory_binding memory_runs memory_events memory_transition sql_definition sql_binding sql_runs sql_run_lifecycle sql_node_state sql_transition sql_correlation sql_events sql_mapping snapshot_validation template tests'.split()
allowed = {prefix + n + '.rs' for n in names}
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().splitlines()
changed = set(git('diff', '--name-only', os.environ['BASE']))
changed.update(git('ls-files', '--others', '--exclude-standard'))
assert changed == allowed, (sorted(changed - allowed), sorted(allowed - changed))
for name in sorted(changed):
    lines = len((root / name).read_bytes().splitlines())
    assert lines <= 1000, (name, lines)
    print(lines, name)
print('PASS: exact 18-file scope; every other tracked file unchanged')
PY
"$WORKDIR/venv/bin/python" "$WORKDIR/audit_store.py" "$WORKDIR/original.rs" "$STORE/src"
git diff --check "$BASE"
```

该物理计数是额外核对，不替代仓库正式门禁；若实施时 CI 有新的 canonical size 入口，还须运行它，不添加 allowlist/豁免。上面的 BASE 是实现开始的 commit，不是 docs 规划 HEAD；PR diff 另使用 fetched target 的 merge-base，不能直接 diff 最新 dev tree。

- [ ] **Step 2: 运行统一编译、全 workspace 测试和架构检查。**

```bash
cd "$BCS_ROOT"
cargo build 2>&1 | tee "$WORKDIR/build.log"
cargo build --workspace --all-features 2>&1 | tee "$WORKDIR/build-all-features.log"
cargo test --workspace --no-run 2>&1 | tee "$WORKDIR/compile-tests.log"
cargo test --workspace --all-features --no-run 2>&1 | tee "$WORKDIR/compile-tests-all-features.log"
cargo test --workspace --no-fail-fast 2>&1 | tee "$WORKDIR/workspace-tests.log"
BCS_BASE_REF="$TARGET_BASE" bash scripts/ci/arch-check.sh 2>&1 | tee "$WORKDIR/after-arch.log"
```

无关基线失败也要列出实际结果和逐项差异，不称全 workspace 通过。`--no-run` 不是业务运行。不得修改检查工具/architecture baseline 来掩盖新失败。

- [ ] **Step 3: 提交后的统一 shared-stack coverage 与产物核验。**

```bash
cd "$ROOT"
bash singlebox/ci/singlebox_coverage.sh 2>&1 | tee "$WORKDIR/after-singlebox.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
cp -R singlebox/.dependencies/coverage/singlebox/reports "$WORKDIR/after-reports"
```

验收：BCS 故事全部通过、runtime line >=41%、method >=36%、HTTP endpoint/CLI leaf 均 100%；本单元 >=20 可测变化行时 E2E changed-line **严格 >60%**（若当前门槛更严遵从更严者）。查看 17 个新文件逐文件映射，生产代码不进入 tests 排除范围；只有 `src/tests.rs` 是原测试搬移。只跑 BCS 独立 runner 不等于统一 shared-stack gate 通过。

端口冲突用 runner 的隔离配置，不停止其他 worktree 服务；若覆盖率不足，不降低 threshold/min-lines、不改 denominator、不制造仅测 wrapper 的调用，报告阻塞并另行确定有效公共入口 E2E 工作范围。

- [ ] **Step 4: 交付审阅材料，不自动 push/发 PR。**

PR 标题建议：`refactor(bcs): split collaboration store by persistence responsibility`。
描述按 Problem/Solution/Validation/Compatibility and risk/Spec 顺序：明确 18 文件范围、公开 API/SQL/schema/config 未变、28 个委托、133 项发现身份保持、Memory/SQLite/MySQL 结果、DB 成本零变化依据、warning、架构/coverage 工件，以及未执行项。真实 MySQL 或 required gate 尚未通过时不推荐合并。

回滚为 revert 此单元 commit；不需要 schema 回滚、数据修复或重新运行迁移。不得用对原 lib.rs 的单文件 restore 留下另一半装配。

## 3. 规划期验证边界（不是实施完成记录）

本计划编写时已在真实基线和仓库外临时完整 Cargo 副本检查：

- 原版默认/all-features Store 测试各 **132 passed、0 failed、1 ignored**；实际发现 **7/11/115**。
- 附录 A 生成的临时拆分副本，默认/all-features Store 测试各 **132 passed、0 failed、1 ignored**；发现清单与原版逐行一致；最终两个构建/测试日志均无 compiler warning/error。
- 附录 B 对临时副本通过 **235 函数身份 + 10 根声明 + 28 精确委托**保持性检查；18 文件全部 <=1,000 行，最大 `tests.rs` **808** 行。
- 最初临时草稿出现根未使用 helper imports，已通过缩小私有导入和对 `db_i32` 增加 cfg(test) 导入解决；没有增加 allow，没有修改原 10 子模块。
- 真实工作区保持 Rust 源码原样；以上是计划工具/模块边界可执行性验证，不是第 4 单元已落地。未运行真实 MySQL、workspace/cross suites、architecture 或 Singlebox coverage；这些仍是实施阶段必需验收项。
- 所有临时工具完整收录附录，不依赖机器上已有的 `/tmp/bcs-unit4-plan` 内容。日期/指纹变化时按 Task 1 重新核对。

## 附录 A：完整 AST 搬移工具

保存为 `$WORKDIR/split_store.py`。只读固定基线，写入一个不存在的候选目录，不直接重写工作区；函数/方法清单是闭合的，未知 free function 或丢失清单项会失败。

```python
"""Usage: split_store.py ORIGINAL.rs OUTPUT_SRC; OUTPUT_SRC must not exist."""
import hashlib
import sys
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_rust

EXPECTED = 'c0a1c31d9046cec72440d456e772b8cd9e97382619b33cc16f9cee3a9ae6edea'
source = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(source).hexdigest() == EXPECTED, 'source drift: re-inventory first'
parser = Parser(Language(tree_sitter_rust.language()))
tree = parser.parse(source)
assert not tree.root_node.has_error
out = Path(sys.argv[2])
assert not out.exists(), 'refuse to overwrite output'
out.mkdir(parents=True)

FUNCTIONS = {
 'snapshot_validation': 'validate_snapshot_envelope definition_json sha256_hex definition_conflict_error short_hash',
 'memory_events': 'state_machine_transition_events',
 'memory_transition': 'apply_memory_state_machine_transition node_mut retry_is_within_limit failure_action_name run_is_running is_terminal',
 'sql_mapping': 'snapshot_column current_millis optional_u64_value run_insert_params build_guarded_node_runs_inserts build_node_runs_insert node_insert_params row_to_state_machine_run row_to_state_machine_node_run row_to_delivery_correlation row_to_collaboration_event db_plugin_error db_string db_assignee_bot_id db_optional_string db_i32 db_optional_i32 db_u64 db_optional_u64 db_json db_opening_message_override run_status_to_str parse_run_status node_status_to_str parse_node_status',
 'template': 'collect_string_column template_priority_from_row placeholders env_and_ids_params',
}
METHODS = {
 'sql_transition': 'commit_eventful_transition',
 'sql_run_lifecycle': 'create_run create_run_if_session_idle create_rerun_if_session_idle get_direct_rerun get_run list_running_runs list_pending_runs get_run_by_session_id list_runs_by_session_id update_run_status',
 'sql_node_state': 'list_node_runs get_node_run mark_node_running mark_node_running_if_run_active complete_node_attempt mark_human_node_running_if_run_active record_node_artifact_if_running record_human_response_if_running fail_node_attempt fail_node_attempt_with_action get_node_attempt_failure schedule_node_retry skip_node list_expired_running_node_runs',
 'sql_correlation': 'upsert_delivery_correlation register_delivery_alias lookup_delivery_correlation',
}
function_dest = {n: m for m, names in FUNCTIONS.items() for n in names.split()}
method_dest = {n: m for m, names in METHODS.items() for n in names.split()}
TRAITS = {'StateMachineDefinitionRepoPort': 'definition', 'GroupRuntimeBindingRepoPort': 'binding', 'StateMachineRunRepoPort': 'runs', 'CollaborationEventRepoPort': 'events'}
parts = {}
exports = {}
root = []
sql_helpers = {}
seen_functions, seen_methods = set(), set()
def add(module, text):
 parts.setdefault(module, []).append(text)
def text(node):
 return source[node.start_byte:node.end_byte].decode()
def name(node):
 return text(node.child_by_field_name('name'))
def items(container):
 """Attach whitespace, comments and attributes to their following AST item."""
 start = container.start_byte + (1 if container.type == 'declaration_list' else 0)
 for n in container.named_children:
  if n.type in ('attribute_item', 'line_comment', 'block_comment'): continue
  yield n, source[start:n.end_byte].decode()
  start = n.end_byte
 # Only whitespace may remain; do not silently lose trailing comments.
 end = container.end_byte - (1 if container.type == 'declaration_list' else 0)
 assert not source[start:end].strip(), 'unattached trailing content'

def inherent(header, rows):
 return header + ' {' + ''.join(rows) + '\n}\n'

for node, chunk in items(tree.root_node):
 if node.type == 'function_item':
  n = name(node); module = function_dest[n]; seen_functions.add(n)
  # Minimal shared helper visibility; private helpers remain in their owner.
  local_only = {'short_hash', 'template_priority_from_row', 'env_and_ids_params',
                'collect_string_column', 'placeholders', 'db_assignee_bot_id',
                'db_json', 'db_opening_message_override', 'db_optional_i32',
                'db_optional_string', 'db_optional_u64', 'node_insert_params',
                'node_status_to_str', 'parse_run_status'}
  if n not in local_only:
   chunk = chunk.replace('fn ' + n, 'pub(super) fn ' + n, 1)
   if n != 'db_i32': exports.setdefault(module, []).append(n)
  add(module, chunk)
 elif node.type == 'impl_item':
  typ = text(node.child_by_field_name('type'))
  trait = node.child_by_field_name('trait')
  if trait is not None:
   tr = text(trait)
   module = 'template' if tr == 'CollaborationTemplateRepoPort' else ('memory_' if typ.startswith('Memory') else 'sql_') + TRAITS[tr]
   if module != 'sql_runs':
    add(module, chunk); continue
   wrappers = []
   for method, raw in items(node.child_by_field_name('body')):
    n = name(method)
    if n not in method_dest:
     wrappers.append(raw); continue
    seen_methods.add(n)
    params = method.child_by_field_name('parameters')
    args = []
    for param in params.named_children:
     if param.type == 'self_parameter': continue
     assert param.type == 'parameter'
     pat = param.child_by_field_name('pattern')
     assert pat.type == 'identifier', 'nontrivial parameter pattern'
     args.append(text(pat))
    body = method.child_by_field_name('body')
    wrappers.append(raw[:raw.index(text(body))] + '{\n        self.sql_' + n + '(' + ', '.join(args) + ').await\n    }')
    helper = raw.replace('async fn ' + n, 'pub(super) async fn sql_' + n, 1)
    sql_helpers.setdefault(method_dest[n], []).append(helper)
   add(module, '#[async_trait]\n' + inherent('impl StateMachineRunRepoPort for MySqlCollaborationStore', wrappers))
  elif typ == 'MySqlCollaborationStore':
   constructors, helpers = [], []
   for method, raw in items(node.child_by_field_name('body')):
    n = name(method)
    if n in {'new', 'sqlite'}: constructors.append(raw)
    else:
     # Binding uses definition_metadata; other definition helpers are local.
     if n == 'definition_metadata': raw = raw.replace('async fn ', 'pub(super) async fn ', 1)
     helpers.append(raw)
   root.append(inherent('impl MySqlCollaborationStore', constructors))
   add('sql_definition', inherent('impl MySqlCollaborationStore', helpers))
  elif typ == 'DbCollaborationTemplateRepo' and any(name(m) == 'tags_for_templates' for m, _ in items(node.child_by_field_name('body'))):
   add('template', chunk)
  else: root.append(chunk)
 elif node.type == 'mod_item' and name(node) == 'tests':
  b = node.child_by_field_name('body')
  add('tests', source[b.start_byte+1:b.end_byte-1].decode())
 else: root.append(chunk)
assert seen_functions == function_dest.keys()
assert seen_methods == method_dest.keys()
for module, methods in sql_helpers.items():
 add(module, inherent('impl MySqlCollaborationStore', methods))
root.append('\n#[cfg(test)]\nuse sql_mapping::db_i32;\n')
for module in sorted(parts):
 root.append(('\n#[cfg(test)]' if module == 'tests' else '') + '\nmod ' + module + ';\n')
 if module in exports:
  root.append('use ' + module + '::{\n    ' + ', '.join(exports[module]) + ',\n};\n')
for module, chunks in parts.items():
 value = ('' if module == 'tests' else 'use super::*;\n') + ''.join(chunks).strip('\n') + '\n'
 (out / (module + '.rs')).write_text(value)
(out / 'lib.rs').write_text(''.join(root).strip('\n') + '\n')
for file in sorted(out.glob('*.rs')):
 data = file.read_bytes()
 assert not parser.parse(data).root_node.has_error, file
 count = len(data.splitlines())
 assert count <= 1000, (file, count)
 print(file.name, count)
```

## 附录 B：独立函数保持性与委托核对

保存为 `$WORKDIR/audit_store.py`。按 AST 逻辑身份比较原文，不删除任意空白或字符串；
只允许列明的可见性与 helper 命名变化，28 个 trait wrapper 必须精确直接委托。
此工具不证明数据库行为或跨文件隐私正确，必须结合 Cargo/数据库/只读边界检查。

```python
"""Usage: audit_store.py ORIGINAL.rs CANDIDATE_SRC"""
import sys
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_rust
P = Parser(Language(tree_sitter_rust.language()))
original, candidate = Path(sys.argv[1]), Path(sys.argv[2])
modules = set('memory_definition memory_binding memory_runs memory_events memory_transition sql_definition sql_binding sql_runs sql_run_lifecycle sql_node_state sql_transition sql_correlation sql_events sql_mapping snapshot_validation template tests'.split())
def extract(data, scope='', follow=False):
 tree = P.parse(data)
 assert not tree.root_node.has_error
 rows, declarations, wrappers = {}, {}, {}
 def visit(parent, scope):
  attrs = []
  for node in parent.named_children:
   if node.type == 'attribute_item': attrs.append(node.text); continue
   if node.type in {'line_comment', 'block_comment'}: continue
   attribute_bytes = b'\n'.join(attrs); attrs = []
   if node.type == 'function_item':
    n = node.child_by_field_name('name').text.decode()
    raw = node.text
    if scope == 'MySqlCollaborationStore' and n.startswith('sql_'):
     old_name = n.removeprefix('sql_')
     raw = raw.replace(b'pub(super) ', b'', 1).replace(n.encode(), old_name.encode(), 1)
     key = ('StateMachineRunRepoPort for MySqlCollaborationStore', old_name)
    else:
     key = (scope, n)
     if scope in {'', 'MySqlCollaborationStore'}:
      raw = raw.removeprefix(b'pub(super) ')
    value = (attribute_bytes, raw)
    if scope == 'StateMachineRunRepoPort for MySqlCollaborationStore' and b'self.sql_' in node.child_by_field_name('body').text:
     wrappers[key] = value
    else:
     assert key not in rows, key
     rows[key] = value
   elif node.type == 'impl_item':
    typ = node.child_by_field_name('type').text.decode()
    trait = node.child_by_field_name('trait')
    impl_scope = (trait.text.decode() + ' for ' if trait else '') + typ
    if trait: assert attribute_bytes == b'#[async_trait]', impl_scope
    visit(node.child_by_field_name('body'), impl_scope)
   elif node.type == 'mod_item':
    n = node.child_by_field_name('name').text.decode()
    body = node.child_by_field_name('body')
    if body is not None: visit(body, n)
    elif follow and n in modules:
     path = candidate / (n + '.rs')
     child_rows, child_declarations, child_wrappers = extract(path.read_bytes(), 'tests' if n == 'tests' else '')
     assert not rows.keys() & child_rows.keys()
     rows.update(child_rows); declarations.update(child_declarations); wrappers.update(child_wrappers)
   elif node.type in {'const_item', 'struct_item', 'enum_item', 'type_item'}:
    key = (scope, node.type, node.child_by_field_name('name').text.decode())
    assert key not in declarations
    declarations[key] = (attribute_bytes, node.text)
 visit(tree.root_node, scope)
 return rows, declarations, wrappers
before, before_decls, _ = extract(original.read_bytes())
after, after_decls, wrappers = extract((candidate / 'lib.rs').read_bytes(), follow=True)
assert before_decls == after_decls, 'root type/constant drift'
assert before.keys() == after.keys(), (before.keys() - after.keys(), after.keys() - before.keys())
for key in before:
 assert before[key] == after[key], ('function signature/body/attribute drift', key)
for key, (attrs, wrapper) in wrappers.items():
 raw = before[key][1]
 node = P.parse(raw).root_node.named_children[0]
 body = node.child_by_field_name('body')
 args = [p.child_by_field_name('pattern').text.decode() for p in node.child_by_field_name('parameters').named_children if p.type == 'parameter']
 expected = raw[:body.start_byte] + ('{\n        self.sql_' + key[1] + '(' + ', '.join(args) + ').await\n    }').encode()
 assert wrapper == expected and attrs == before[key][0], key
assert len(wrappers) == 28, len(wrappers)
for file in [candidate / 'lib.rs', *(candidate / (m + '.rs') for m in sorted(modules))]:
 count = len(file.read_bytes().splitlines())
 assert count <= 1000, (file, count)
print(f'PASS: {len(before)} function identities, {len(before_decls)} declarations, {len(wrappers)} exact delegates; 18 files <=1000')
```
