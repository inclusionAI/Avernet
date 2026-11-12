# BCS internal 五个超 1000 行 Rust 大文件按职责拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `apps/bcs-internal` 内 5 个达到/超过 1000 行的 Rust 源文件按职责拆到每个文件 ≤1000 行（目标 ≤800），行为零变化。

**Architecture:** 纯移动式重构（move-only）：F1 `outbound.rs`(4096) 走"文件模块→目录模块零路径变化"拆成 9 个生产子模块 + `tests/` 主题测试目录；F2 `lib.rs`(1625)、F3(1329)、F5(1000) 生产区本已 ≤1000，按既有 `#[cfg(test)] #[path = "…"]` 惯例抽取内联测试；F4 `zcache/client.rs`(1140) 抽观测簇 + 抽测试。不新建 crate，不改 `Cargo.toml`/`Cargo.lock`，公共 API 与 `inventory::submit!` 注册零变化。

**Tech Stack:** Rust 1.99（edition 2024 workspace）、cargo / cargo-nextest（如可得）、`apps/bcs-internal/scripts/check-{architecture,p0,p4,p5}.sh`。

**Spec:** `docs/superpowers/specs/2026-11-12-bcs-internal-large-file-split-design.md`（含 builder-check 裁决记录 §9，与本计划配套阅读）。

**交付形态（spec §5）：** 一个 PR、按文件分阶段共 7 个提交：Task 1(F5)、Task 2(F3)、Task 3(F4)、Task 4(F2) 各 1 个；F1 = Task 5(`git mv` 零编辑) + Task 6(生产逐簇抽出) + Task 7(测试拆分)。

## Global Constraints

- 行数口径：全量 `wc -l`（含测试）≤1000，目标 ≤800；每个新文件行数写入 PR 描述（spec §1.3/§6）。
- 纯移动：方法体逐字节保留；只允许最低限度可见性放宽（`pub(super)`/`pub(crate)`）与 `use` 路径调整；禁全局格式化（`cargo fmt` 等）禁顺手重构/重命名；`git diff --check` 干净。
- 不改任何 `Cargo.toml`/`Cargo.lock`/workspace members/bootstrap `main.rs`/`check-p4.sh`/`check-p5.sh`；不动 `submodules/avernet`（含指针）；不出现 `crates/plugin-api`。
- 测试模块名保持 `tests`（`crate::tests::*` 被同 crate 其他测试文件消费，见 Review Focus 1）；`#[ignore]`、`#[tokio::test]`、全局 `OnceLock/AtomicBool` 与全局 tracing subscriber 的序语义原样保留。
- 磁盘纪律：构建产物走 `/tmp/cargo/Avernet-target`（Task 0 配 `.cargo/config.toml` 并登记 gitdir `info/exclude`，不进版本库）；构建前 `df -h /tmp`，<8G 只清 `debug/incremental`，<3G 停手；禁止 `cargo clean` / 整删共享缓存；`check-p5.sh` 硬编码 `apps/bcs-internal/target/debug/bcs-internal`，跑它时用默认 target 目录或临时符号链接。
- 工具链现实：`git submodule update --init submodules/avernet` 是一切前置；本机无 `protoc`/`cargo-nextest`（F4/收口按 Task 0/8 的获取与降级条款处理）。

## Review Focus

1. **`crate::tests::` 跨文件消费断链**：`identity_tests.rs`/`observations_test.rs` 消费 `crate::tests::{empty_acm_user, test_acm_params, MockAgentAcmClient}`；`lib.rs` 的 `crate::tests::caller_tests`——模块名与可见性一旦变化即编译断。→ Task 1/4 的 `cargo test -p` 步骤钉死（编译即验证）。
2. **F4 的 `pool.rs` `use super::{…}` 与 `zcache_observation.rs` 的 `use super::*` 依赖宿主私有项**：观测簇搬家后宿主模块从 `client` 变 `client_observation`。→ Task 3 步骤 3/4 逐项钉死。
3. **F1 测试直改私有字段（`delivery.ack_reactions` 等）**：拆到兄弟文件后必须仍是 `outbound` 后代模块。→ Task 7 的目录结构 + `cargo test -p bcs-channel-dingtalk` 钉死。
4. **公共路径与注册漂移**：crate 根 re-export、`bcs-internal` 二进制 `use … as _;` 链接清单、`inventory::submit!` 三处一旦漂移，插件静默失踪。→ Task 8 的 `check-p5.sh`（二进制 `--help` 标记）与 dingtalk 单 crate 全量测试钉死。
5. **移动中混入逻辑编辑**（重排语句、"顺手"改名）：review 无法逐行核对 9000 行移动。→ 每个 Task 的"移动核对"步骤：用 `git show --stat` + `git diff -M --color-moved` 自证纯移动。

---

### Task 0: 环境准备与基线快照

**Files:**
- Create: `.cargo/config.toml`（worktree 根，登记进 gitdir `info/exclude`，不提交）

**Interfaces:** 后续所有 Task 依赖本 Task 的编译环境与基线数据。

- [ ] **Step 1: 配 target-dir 与磁盘检查**

```bash
df -h /tmp   # 余量 ≥8G 才继续；<8G 先清 /tmp/cargo/Avernet-target/debug/incremental
mkdir -p .cargo && printf '[build]\ntarget-dir = "/tmp/cargo/Avernet-target"\n' > .cargo/config.toml
grep -qxF '.cargo/config.toml' "$(git rev-parse --git-dir)/info/exclude" || echo '.cargo/config.toml' >> "$(git rev-parse --git-dir)/info/exclude"
```

- [ ] **Step 2: 初始化子模块（门禁与编译前置）**

```bash
git submodule update --init submodules/avernet
git submodule status submodules/avernet   # 期望:无 '-' 前缀,停在 daf07b789a
```

- [ ] **Step 3: 基线测试快照（改动前必须全绿；失败则在开工评论留证后继续）**

```bash
M=apps/bcs-internal/Cargo.toml
for p in bcs-auth-agentpass bcs-buservice-user-directory bcs-channel-dingtalk; do
  cargo test --manifest-path $M -p $p --lib 2>&1 | tail -3
done
```

- [ ] **Step 4: protoc 获取（F4/收口才用得到，此时备妥）**

以内网制品源或组织缓存下载预编译 protoc 到 `/tmp/protoc/bin/`，`export PATH=/tmp/protoc/bin:$PATH`（可写入本任务 shell 环境）；验证 `protoc --version`。拿不到则在任务日志记"protoc 不可得"，Task 3 的 check/test 与 Task 8 的 ci_test 走降级条款。同步尝试 `cargo install cargo-nextest --locked`（不可得不阻塞）。
Expected: protoc 可用或已记录降级原因；不得因工具未备而开工改动。

### Task 1: F5 · `bcs-auth-agentpass/src/lib.rs` 抽测试

**Files:**
- Create: `apps/bcs-internal/crates/plugins/bcs-auth-agentpass/src/plugin_tests.rs`
- Modify: `apps/bcs-internal/crates/plugins/bcs-auth-agentpass/src/lib.rs:495-506, 721-993`

**Interfaces:**
- Produces: `crate::tests` 模块经 `#[path]` 继续提供 `empty_acm_user`、`test_acm_params`、`MockAgentAcmClient`（`identity_tests.rs`、`observations_test.rs` 消费，路径与可见性不变）。

- [ ] **Step 1: 抽取 `mod tests`** — `lib.rs:721-993` 整体移入 `src/plugin_tests.rs`；`lib.rs` 原处替换为：

```rust
#[cfg(test)]
#[path = "plugin_tests.rs"]
mod tests;
```

- [ ] **Step 2: 移动 test-only 助手** — `authenticate_agent_code_with_client`（495–506）移入 `plugin_tests.rs` 内 `mod tests` 之外的文件级（或 tests 模块内，保持调用点 `super::` 可达的一处置）；消费点同步改到新路径。

- [ ] **Step 3: 验证**

```bash
cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-auth-agentpass
wc -l apps/bcs-internal/crates/plugins/bcs-auth-agentpass/src/lib.rs   # 期望 ≤800（约 716）
```

Expected: 38 例全绿（与 Task 0 基线一致）;`identity_tests`/`observations_test` 编译过。

- [ ] **Step 4: 移动核对** — `git diff --stat && git diff --color-moved=dimmed-zebra | grep -c '^[-+][^-+]'` 人工确认非移动行仅为声明/helper 移位。

- [ ] **Step 5: Commit 1/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): extract agentpass lib tests into plugin_tests.rs (CI 1000-line)"
```

### Task 2: F3 · `bcs-buservice-user-directory/src/lib.rs` 抽测试

**Files:**
- Create: `apps/bcs-internal/crates/plugins/bcs-buservice-user-directory/src/plugin_tests.rs`
- Modify: `apps/bcs-internal/crates/plugins/bcs-buservice-user-directory/src/lib.rs:670-1325`

**Interfaces:** 无对外新接口；`observations_test` 声明原样保留在 `lib.rs` 尾部。

- [ ] **Step 1: `mod tests`（36 例，655 行）整体移入 `src/plugin_tests.rs`**，`lib.rs` 原处替换为 `#[cfg(test)] #[path = "plugin_tests.rs"] mod tests;`（ houses idiom，同文件 1327 行既有先例）。

- [ ] **Step 2: 验证**

```bash
cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-buservice-user-directory
wc -l apps/bcs-internal/crates/plugins/bcs-buservice-user-directory/src/lib.rs   # 期望 ≤800（约 674）
```

Expected: 37 例全绿（含 `observations_test`）。

- [ ] **Step 3: 移动核对**（同 Task 1 Step 4）。

- [ ] **Step 4: Commit 2/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): extract buservice-user-directory lib tests into plugin_tests.rs (CI 1000-line)"
```

### Task 3: F4 · `bcs-layotto/src/zcache/client.rs` 抽观测簇 + 抽测试

**Files:**
- Create: `apps/bcs-internal/crates/external-clients/bcs-layotto/src/zcache/client_observation.rs`、`apps/bcs-internal/crates/external-clients/bcs-layotto/src/zcache/client_tests.rs`
- Modify: `apps/bcs-internal/crates/external-clients/bcs-layotto/src/zcache/client.rs:17-184, 953-1140`

**Interfaces:**
- Consumes: `zcache/pool.rs` 的 `use super::{logged_connection_request, redis_auth_command, ClientObservation, ZCacheConfig}`（保持不变，靠 Step 2 的 re-export 维持）。
- Produces: `bcs_layotto::AsyncZCacheClient`/`ZCacheClient` 与全部 `zone_*`/`ldc_*` 方法签名不变（`bcs-leader-election-zcache`、`bcs-zcache` 消费）。

- [ ] **Step 1: 建 `client_observation.rs`** — 移入 `ClientObservation`(+`Drop`)、`RequestObservation`(+方法/`Drop`)、`logged_connection_request`、test-only `logged_request`、`NEXT_CLIENT_ID`；移入的 `#[cfg(test)] #[path = "../../tests/support/zcache_observation.rs"] mod observation_tests;` 声明一并放进该文件尾部（深度与 `client.rs` 同目录，相对路径不变）。可见性：供 `client.rs`/`pool.rs` 使用的项标 `pub(crate)`，其余保持私有。

- [ ] **Step 2: `client.rs` 接线** — 头部加：

```rust
#[path = "client_observation.rs"]
mod client_observation;
pub(crate) use client_observation::{ClientObservation, logged_connection_request};
```

（`RequestObservation` 仅 client 自用，`pub(crate)` 或 `use client_observation::…` 就近引入；`redis_auth_command` 留在 `client.rs` 不动。）`pool.rs` 一行不动。

- [ ] **Step 3: 抽内联测试** — `client.rs:953-1136` 的 `mod tests`（1 纯例 + 5 个 `#[ignore = "Requires Redis server"]`）整体移入 `src/zcache/client_tests.rs`，`client.rs` 尾部替换为 `#[cfg(test)] #[path = "client_tests.rs"] mod tests;`。`#[ignore]` 属性逐字节保留。

- [ ] **Step 4: 验证（需 Task 0 的 protoc）**

```bash
export PATH=/tmp/protoc/bin:$PATH
cargo test  --manifest-path apps/bcs-internal/Cargo.toml -p bcs-layotto
cargo check --manifest-path apps/bcs-internal/Cargo.toml -p bcs-leader-election-zcache -p bcs-zcache --all-targets
wc -l apps/bcs-internal/crates/external-clients/bcs-layotto/src/zcache/client.rs   # 期望 ≤800（约 784）
```

Expected: 34 lib + 44 集成测试全绿（含 fake Redis loopback 套件）；protoc 不可得时按 spec §6 降级并留证。

- [ ] **Step 5: 移动核对**（同 Task 1 Step 4）。

- [ ] **Step 6: Commit 3/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): split zcache client observation cluster and tests by responsibility (CI 1000-line)"
```

### Task 4: F2 · `bcs-channel-dingtalk/src/lib.rs` 抽测试目录

**Files:**
- Create: `apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/lib_tests/{mod.rs,support.rs,provider.rs,callback_messages.rs,callback_ack.rs,factory.rs}`
- Move: `apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/tests/caller_tests.rs` → `src/lib_tests/caller_tests.rs`（内容零改动）
- Modify: `apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/lib.rs:141-146(可见性), 517-1625`

**Interfaces:**
- Produces: `crate::tests::caller_tests` 路径不变；`lib.rs` 生产区（517 行以内）与全部 `pub use` re-export 不变。

- [ ] **Step 1: 落地测试目录骨架** — `lib_tests/mod.rs` 仅含 `mod support; mod provider; mod callback_messages; mod callback_ack; mod factory; mod caller_tests;`；`support.rs` 收全部 doubles 与 helper（`TestDownloadUrlResolver`、`TestBindingRepo`、`TestSink/BlockingSink/FailingSink/RecordingFailingSink`、`RecordingFailureDelivery`、`SharedLogBuffer/Writer`、`capture_tracing_logs`、`valid_config/provider/attachment_service/test_provider_config/signed_headers`），`pub(super)`/`pub(crate)` 从支持者搬运。

- [ ] **Step 2: 按主题搬测试** — `provider.rs`（4 例，现 636–649/849–889）、`callback_messages.rs`（11 例，890–1293）、`callback_ack.rs`（5 例，1294–1563）、`factory.rs`（3 例，1564–1625）；`caller_tests.rs` 物理移动并 `mod.rs` 声明收编。`lib.rs:517` 起替换为 `#[cfg(test)] #[path = "lib_tests/mod.rs"] mod tests;`；`lib.rs:141-146` 的 `DingTalkHttpIngress` 字段提为 `pub(crate)`（测试构造字面量需要）；`TEST_TRACING_CAPTURE_LOCK` 保留在 `lib.rs`。

- [ ] **Step 3: 验证**

```bash
cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk
wc -l apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/lib.rs apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/lib_tests/*.rs   # 全部 ≤800
```

Expected: 与 Task 0 基线同数全绿（23 + caller_tests 2 + 其余文件内测试）。

- [ ] **Step 4: 移动核对**（同 Task 1 Step 4）。

- [ ] **Step 5: Commit 4/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): extract dingtalk lib.rs tests into lib_tests/ themes (CI 1000-line)"
```

### Task 5: F1-a · `outbound.rs` → `outbound/mod.rs`（零编辑）

**Files:**
- Move: `apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/outbound.rs` → `src/outbound/mod.rs`（内容零改动）

**Interfaces:** 模块路径 `crate::outbound::*` 全不变（`lib.rs:26,40`、`http_client.rs:13`、`failure_notifier.rs:16`、`attachment.rs:15` 零改动）。

- [ ] **Step 1: `git mv`** — `cd apps/bcs-internal/crates/plugins/bcs-channel-dingtalk && mkdir src/outbound && git mv src/outbound.rs src/outbound/mod.rs`。

- [ ] **Step 2: 验证** — `cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk` 全绿（同基线）。

- [ ] **Step 3: Commit 5/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): convert dingtalk outbound.rs to directory module (no content change)"
```

### Task 6: F1-b · `outbound/` 生产簇逐簇抽出

**Files:**
- Create: `src/outbound/{tokens.rs,webhook.rs,file_download.rs,failure_notice.rs,interactive.rs,normal_delivery.rs,streaming_card.rs,ack_reaction.rs}`
- Modify: `src/outbound/mod.rs`（结构体/常量/编排保留；逐簇删除 + `mod` 声明 + `pub(super)` 可见性）

**Interfaces:**
- Consumes: `DingTalkDelivery` 9 字段与约 30 个跨簇私有方法（按簇提升 `pub(super)`）。
- Produces: `crate::outbound::{DingTalkDelivery, DingTalkApiResponse}` 路径不变；`mod.rs` 为唯一对外面孔（spec §4.1 生产模块图）。

- [ ] **Step 1-8: 按依赖叶子→核心顺序抽 8 簇**（每簇同一循环：建文件移动方法体（逐字节）→ `mod.rs` 加 `mod xxx;` 与必要 `pub(super)` → 循环验证）。顺序与内容（spec §4.1）：①`tokens.rs`（31–35, 690–734）②`webhook.rs`（190–252）③`file_download.rs`（736–784）④`failure_notice.rs`（867–945）⑤`interactive.rs`（1242–1410）⑥`normal_delivery.rs`（786–865, 1082–1104, 1412–1498）⑦`streaming_card.rs`（43–50, 947–1027, 1106–1205, 1500–1738）⑧`ack_reaction.rs`（52–101, 268–688）。`mod.rs` 保留：结构体+字段（`pub(super)`）、构造、常量表、`active_config`、`send_dingtalk_json`、render/log 共享小助手、`DingTalkApiResponse`、`ChannelDeliveryPort` 编排 impl。子模块 import 用 `crate::config::…` 直连（不经根 re-export）。

```bash
# 每簇固定循环
cargo check --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk --all-targets
cargo test  --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk --lib
```

- [ ] **Step 9: 全量验证 + 行数**

```bash
cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk
wc -l apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/outbound/*.rs   # 生产文件全部 ≤800（最大约 500+残余测试）
```

- [ ] **Step 10: Commit 6/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): split dingtalk outbound production clusters by responsibility (CI 1000-line)"
```

### Task 7: F1-c · `outbound/tests/` 主题测试拆分

**Files:**
- Create: `src/outbound/tests/{mod.rs,support.rs,webhook_and_binding.rs,ack_reaction.rs,command_and_normal.rs,failure_notice_and_logging.rs,final_display.rs,streaming_card.rs,human_input.rs}`
- Modify: `src/outbound/mod.rs`（`#[cfg(test)] mod tests;` 声明收编；内联 `mod tests {…}` 删除）

**Interfaces:** 测试保持 `outbound` 后代模块（Review Focus 3）；`tests/mod.rs` 以 `pub(super) use super::*; pub(super) use support::*;` 收口，主题文件沿用 `use super::*;`。

- [ ] **Step 1: `support.rs` 先行** — 共享设施全部先入（SharedLogBuffer/capture、TestBindingRepo、`active_binding`、`delivery_with_*` 构造器、`event/reply_context/webhook_reply` builders），`pub(super)` 放行。

- [ ] **Step 2: 按主题搬 8 个测试文件**（主题与现行段对应见 spec §4.1 测试表；ranges 是指引，依赖就近微调；human_input.rs 兼收 rendering 2 例）。每个文件落位后 `use super::*; use super::support::*;` 起步，缺什么再显式补。

- [ ] **Step 3: 验证（每轮主题搬完跑一次）**

```bash
cargo test --manifest-path apps/bcs-internal/Cargo.toml -p bcs-channel-dingtalk
wc -l apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/outbound/*.rs apps/bcs-internal/crates/plugins/bcs-channel-dingtalk/src/outbound/tests/*.rs   # 全部 ≤800
```

Expected: 138 例 + conformance 全绿（同基线）；最大文件 ack_reaction 相关 ≤800。

- [ ] **Step 4: 移动核对**（同 Task 1 Step 4）。

- [ ] **Step 5: Commit 7/7**

```bash
git add -A && git commit -m "refactor(bcs-internal): split dingtalk outbound tests into themed modules (CI 1000-line)"
```

### Task 8: 收口验证与留证

**Files:** 无代码变更；产出 PR 描述素材。

- [ ] **Step 1: 全仓行数终检** — `find apps/bcs-internal -name '*.rs' -exec wc -l {} + | awk '$1 > 1000'` 必须为空；5 个原文件与全部新文件行数表格化（before/after）。

- [ ] **Step 2: 快门禁** — `./apps/bcs-internal/scripts/check-architecture.sh && ./apps/bcs-internal/scripts/check-p0.sh`。

- [ ] **Step 3: 慢门禁（按 spec §6 分级，留证降级）** — `check-p4.sh`（11 包；注意磁盘水位）、`check-p5.sh`（默认 target 目录跑一次或临时符号链接 `apps/bcs-internal/target` → 重定向目录，跑完恢复）、能装 `cargo-nextest` 且 protoc 可用则 `bash apps/bcs-internal/scripts/ci_test.sh --fast-fail`；每一项记录"本机实跑 / 降级原因"。

- [ ] **Step 4: 组装 PR 描述** — 含：5 文件 before/after 行数表、7 提交清单、"纯移动无行为变化"声明、本机/降级门禁证据清单、`git diff --check` 干净证明；按实现工单指令 `git push --no-verify -u origin <分支>`。

---

## Self-Review 结论（2026-11-12，写手自检）

1. **Spec coverage**：spec §4.1–4.5 五个文件 ↔ Task 1–7 一一对应；§5 交付/编辑/磁盘纪律 ↔ Global Constraints + Task 0/8；§6 分级门禁 ↔ Task 8；§7 风险 ↔ Review Focus 1–5。无缺口。
2. **Step scan**：每步一个动作带可检查结果；方法体写入由执行者按"逐字节移动"完成（搬运内容 spec/plan 均依赖现状行段定位，不转录代码）。
3. **Type consistency**：命名贯穿一致（`plugin_tests.rs`、`client_observation.rs`、`client_tests.rs`、`lib_tests/`、`outbound/{…, tests/…}`），spec §4 与 Task Files 段一致。
4. **Review Focus**：5 项均有对应 Task 的编译/测试步骤钉死；无空转。
5. **Proportion**：spec 225 行 / plan ≈200 行，无代码转录段。
