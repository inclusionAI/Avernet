# BCS Code and Test Build Footprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留功能、唯一测试场景和覆盖率要求，消除无效依赖、迁移测试重复注册及多余测试构建入口。

**Architecture:** 删除无消费者的 callback 空壳；把 SQLite schema 执行器抽为只依赖 DbPlugin 契约的独立库。保留 bootstrap 公开导入路径，在同一个 crate 内按业务域合并测试入口，并保留需要全局状态隔离的独立入口。

**Tech Stack:** Rust/Cargo、Tokio、cargo-nextest、cargo-llvm-cov、Python 3.11+、Bash、SQLite、MySQL 及现有 Singlebox gate。

---

## 范围、约束和执行顺序

依据 [spec.md](spec.md)。本次仅交付计划，所有未勾选步骤都尚未执行。
当前代码基线为 `origin/dev@f8adbab061`，完整 SHA：
`f8adbab061e63fe72a8042b0a2a9bf1a7d7c525c`。原 spec/plan 提交分别为
`b45ba367a0` / `f6409854d9`，本次修订见 [origin-dev-review.md](origin-dev-review.md)。
实施应从已 fetch 的 origin/dev 建立分支，再带入本 spec/plan；不得把仍停留在
旧代码的文档分支 HEAD 当成实现基线。

执行顺序：任务 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8。
迁移库化先完成，再合并测试入口，避免一次改变源码所有权、测试身份和编译拓扑。
生产 Noop/test-support 解耦、依赖升级、功能裁剪和 debug profile 调整不在本期。

本轮沿用上游已完成的三项拆分：message-flow `contract_message_flow.rs` 已为
74 行入口，CLI `client.rs` 为 484 行，Group `management.rs` 为 85 行。
只处理本计划的入口合并和依赖，不重做这三项拆分；相关旧文档即使尚未更新
“未实施”状态，也以固定远端 SHA 的实际源码为准。

默认将 message-flow 从 60 个入口调整为 6 个 suite 加 3 个独立入口，将 HTTP
从 53 个调整为 8 个 suite 加 2 个独立入口；另移除空 helper target，总数预计
601 → 506。附录 A/B 列出全部 113 个旧入口的唯一去向。隔离或性能实测不通过
时保留更多独立入口，记录偏差，不减少用例来满足数量目标。

所有新增/修改源文件必须 ≤1,000 行。附录 E 的 12 个超长入口在移动、改 import
前按职责拆分；原测试函数、输入、断言和 helper 行为原样搬迁。禁止全局 cargo fmt。
历史 migrations（含 Rust 字符串中的 DDL）冻结；未经验证不删除依赖。

每批验证通过后做一个独立提交，使用 `<type>(bcs): <outcome>`；只暂存该批文件。
不自动 push、创建 PR、部署或改动现有工作区缓存。正常失败不得用重试隐藏。

## 文件职责图

所有相对路径默认以 `apps/bcs/` 为根；标注“仓库根”的路径例外。

| 文件/目录 | 操作 | 职责 |
| --- | --- | --- |
| `Cargo.toml`、`Cargo.lock`、`README.md` | 修改 | 记录真实 workspace 成员和依赖 |
| `crates/services/bcs-callback/` | 删除候选 | 退役空壳，不涉及 WS TaskCallbackHook |
| `crates/bootstrap/bcs/tests/support/e2e_helpers.rs` | 移入 | 保留 E2E 辅助函数，消除独立空 target |
| 附录 C 的 21 个 `Cargo.toml` | 修改 | 逐项清理 34 条候选声明 |
| `crates/services/bcs-db-migrations/` | 新增 | schema 执行器、检查/报告、迁移单测和 CONTEXT |
| `crates/bootstrap/bcs/src/migrations.rs` | 改为 re-export | 保持 `bcs::migrations` 导入兼容 |
| 附录 D 的 29 个源文件及所属 manifest | 修改 | 用库依赖替换源码包含 |
| 两个试点的 `tests/suite_*.rs` | 新增 | suite 入口及 suite 内共享 fixture 声明 |
| 两个试点的 `tests/cases/` | 移入/拆分 | 保留原用例模块，不再形成独立 binary |
| `scripts/ci/check-deps.sh` | 修改 | 约束迁移库普通依赖 |
| `scripts/ci/check-r25-conformance.sh` | 修改 | 发现迁入子目录的 conformance 入口 |
| `scripts/ci/tests/conformance_layout_regression.sh` | 新增 | 验证嵌套发现及缺失 harness 时拒绝通过 |
| `.config/nextest.toml`、CI 定向选择器 | 按引用证据修改 | 保留串行/资源约束与精确用例选择 |
| `scripts/e2e_coverage.sh`、仓库根 `singlebox/ci/` | 仅必要时修改映射 | 移动后不丢失生产覆盖分母 |
| 本目录 `validation.md` | 实施时新增 | 实际命令、结果、用例差异和性能数据 |

## 任务 0：隔离执行、建立可复现基线

**文件：** 本目录 `validation.md`；临时证据目录；不得提交原始日志或产物。

- [ ] 实施开始时 `git fetch origin dev`，固定 `git rev-parse origin/dev` 的完整 SHA。
  使用 `using-git-worktrees` 技能从该 SHA 建立隔离分支，带入已审阅文档并记录
  源码基线与文档提交。在新 worktree 运行仓库根 `scripts/install_git_hooks.sh`。
  若远端相对本计划再次前进，先重算附录映射和统计，不照抄 601/113/34。
- [ ] 阅读仓库/BCS 指导及 spec 的权威文档；确认现有用户改动不在工作树内。
- [ ] 在实施 worktree 的仓库根执行以下初始化，后续代码块默认 cwd 为 BCS：

```bash
export BCS_REPO_ROOT="$(git rev-parse --show-toplevel)"
export BCS_ROOT="$BCS_REPO_ROOT/apps/bcs"
export BCS_SPEC_DIR="$BCS_ROOT/specs/2026-11-05-code-build-footprint"
export BCS_CLEANUP_BASE="$(git rev-parse HEAD)"
export BCS_EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/bcs-footprint.XXXXXX")"
export SERVER_ENV=dev
cd "$BCS_ROOT"
git rev-parse HEAD > "$BCS_EVIDENCE/base.sha"
rustc -Vv > "$BCS_EVIDENCE/rustc.txt"
cargo -V > "$BCS_EVIDENCE/cargo.txt"
cargo nextest --version > "$BCS_EVIDENCE/nextest.txt"
cargo llvm-cov --version > "$BCS_EVIDENCE/llvm-cov.txt"
cargo metadata --locked --no-deps --format-version 1 > "$BCS_EVIDENCE/before.metadata.json"
cargo fetch --locked
```

- [ ] 导出真实用例清单并运行原 workspace。保留正常 skipped/ignored 清单，
  不把依赖下载计入编译时间；不得添加私有 registry 作为公开构建前提。

```bash
CARGO_TARGET_DIR="$BCS_EVIDENCE/before-dev" cargo nextest list \
  --workspace --locked --message-format json > "$BCS_EVIDENCE/before.inventory.json"
CARGO_TARGET_DIR="$BCS_EVIDENCE/before-dev" cargo nextest run \
  --workspace --locked --retries 0
```

- [ ] 用任务 8 的同一测量协议完成“before”实验。记录迁移 SQL 文件的路径和
  SHA-256，以及执行器原始源文件；核对实际 targets/49 个迁移测试/34 条候选。
- [ ] 基线失败时记录具体用例与输出，在未改基线复现；不降低门槛或将失败
  测试排除。无法取得有效基线时可以继续静态清理，但不得验收性能与测试等价。

**预期：** 形成可复用的原始 inventory、schema/源码基线和测量记录；不是只记录
“测试总数”。后续每次结构变更都与这个基线建立身份映射。

## 任务 1：定义测试身份账本和验收方式

**文件：** 临时目录 `test-identity-map.json`；本目录 `validation.md` 记录摘要。

- [ ] 将 nextest 的 `(rust-suites 键, 完整 testcase 名)` 作为测试身份，保留
  suite 原始 metadata 以追溯 package/binary/source；忽略标记从 testcase.ignored 读取。
- [ ] 建立显式变更账本，JSON 是行对象数组，每行有 `old`（身份数组）、`new`
  （单个身份或 null）、`reason`。未改名用例不写账本；移动项须逐一明确名称。
- [ ] `reason` 仅允许 `rename`、`migration-dedup`、`callback-stub`、
  `new-assertion`。只有 migration-dedup 允许多个旧身份指向一个新身份；只有
  callback-stub 允许无新身份，且只列原空桩的两个测试。
- [ ] 用下面的一次性 Python 校验器比较 before/after；把代码保存到临时证据
  目录的 `compare_inventory.py`，无需为了本次迁移新增产品工具。

```python
import json
import sys

def inventory(path):
    data = json.load(open(path))
    return {
        (suite_id, name): bool(case["ignored"])
        for suite_id, suite in data["rust-suites"].items()
        for name, case in suite.get("testcases", {}).items()
    }

before, after = inventory(sys.argv[1]), inventory(sys.argv[2])
rows = json.load(open(sys.argv[3]))
old_seen, new_seen = set(), set()
for row in rows:
    old = [tuple(key) for key in row["old"]]
    new = tuple(row["new"]) if row["new"] is not None else None
    reason = row["reason"]
    assert len(old) == len(set(old)) and not (set(old) & old_seen), row
    assert all(key in before for key in old), row
    if reason == "callback-stub":
        assert len(old) == 1 and new is None, row
    elif reason == "new-assertion":
        assert not old and new in after and not after[new], row
    else:
        assert reason in {"rename", "migration-dedup"}, row
        assert old and new in after, row
        assert reason == "migration-dedup" or len(old) == 1, row
        assert all(before[key] == after[new] for key in old), row
    old_seen.update(old)
    if new is not None:
        assert new not in new_seen, row
        new_seen.add(new)
remaining_before = {key: value for key, value in before.items() if key not in old_seen}
remaining_after = {key: value for key, value in after.items() if key not in new_seen}
assert remaining_before == remaining_after, "unmapped identity or ignored-state change"
print("PASS: every unchanged or explicitly mapped testcase is accounted for")
```

- [ ] 每批在同一个 feature/profile 范围下采集 after 并运行校验器：

```bash
cargo nextest list --workspace --locked --message-format json > "$BCS_EVIDENCE/after.inventory.json"
python3 "$BCS_EVIDENCE/compare_inventory.py" "$BCS_EVIDENCE/before.inventory.json" \
  "$BCS_EVIDENCE/after.inventory.json" "$BCS_EVIDENCE/test-identity-map.json"
```

- [ ] 人工审查 migration-dedup 行：仅同一份迁移测试源码的重复注册可合并，
  不得合并不同实现的 contract 用例。校验器负责清单完整性，源码等价由 diff
  与行为测试验证；不能靠合法填写 reason 来证明语义等价。

## 任务 2：删除空壳、移走 helper、清理依赖

**文件：** 文件职责图中前三类及附录 C；六个 helper 调用者为
`e2e_onboarding.rs`、`integration_http_api.rs`、`e2e_bot_authority.rs`、
`e2e_workbench.rs`、`e2e_chat.rs`、`integration_cli.rs`，均在 bootstrap 的 tests 下。

- [ ] 用 `rg -n 'bcs[-_]callback|TaskCallbackHook|task_callback'` 重新检查整个
  仓库的 Cargo、Rust、发布配置和脚本；确认独立库消费者未出现后删除 callback
  目录、两个 workspace 声明和当前 README 条目。保留 WS 接口和历史设计记录。
- [ ] 移动 helper，六个调用者将 `mod e2e_helpers;` 改为下面两行；辅助函数行为不改：

```bash
git mv crates/bootstrap/bcs/tests/e2e_helpers.rs \
  crates/bootstrap/bcs/tests/support/e2e_helpers.rs
```

```rust
#[path = "support/e2e_helpers.rs"]
mod e2e_helpers;
```

- [ ] 因 `e2e_workbench.rs` 已有 1,106 行，按附录 E 把其测试函数完整搬入子模块，
  共享连接/setup 留在父模块；保留原独立 test target 和所有 ignored 属性。
- [ ] 按附录 C 逐包检查宏、注册、build script 和 feature 后删除确认无用的
  `[dependencies]` 条目。保留条目记录理由；不同时删除同名 dev-dependency。
- [ ] 新增候选 `bcs-a2a-http` 的 `a2a-client` 虽已无 `a2a_client` 源码引用，
  仍为 reqwest 0.13 隐式启用了 JSON、stream、query、charset、HTTP/2 和系统
  proxy。先将该包现有 reqwest 声明改成下式保留当前能力，再删除 SDK client
  声明；`a2a` 模型依赖和 protobuf/gRPC 依赖原样保留。这是依赖归属修复，
  不是统一 reqwest 版本或裁剪 TLS/proxy 功能。

```toml
reqwest = { version = "0.13", default-features = false, features = ["rustls", "json", "stream", "charset", "http2", "system-proxy", "query"] }
```

- [ ] 编辑成员后运行 `cargo metadata --offline --format-version 1` 更新锁文件，
  审查 lock diff，只接受删除空壳/失去所有引用的包及 workspace 关系变化。
  不执行 cargo update；之后恢复所有命令的 `--locked`。
- [ ] 用 `cargo tree --locked -p bcs-a2a-http -e features` 对照修改前归档的 feature
  来源，运行 `cargo check --locked -p bcs-a2a-http --lib`、
  `cargo check --locked -p bcs-a2a-http --all-targets` 和
  `cargo test --locked -p bcs-a2a-http`。单独验证生产 library，避免仅用含 dev
  依赖的构建掩盖缺失 feature；记录传输 feature 是否守恒。
- [ ] 对每个改动包执行下面的命令模式，`BCS_PACKAGE` 从附录 C 取值；对于实际
  支持的无默认/全部 feature 组合补运行 `cargo check`，记录不适用原因。

```bash
export BCS_PACKAGE=bcs-cli
cargo check --locked -p "$BCS_PACKAGE" --all-targets
cargo test --locked -p "$BCS_PACKAGE"
cargo check --locked -p "$BCS_PACKAGE" --all-targets --no-default-features
cargo check --locked -p "$BCS_PACKAGE" --all-targets --all-features
cargo check --locked --workspace --all-targets
```

- [ ] `cargo test --locked -p bcs --tests --no-run` 验证六个 helper 调用者可构建，
  运行原本可运行的近邻测试；外部引擎 E2E 的 ignored 状态不改。metadata 应为
  111 members、600 integration targets（无基线上游漂移时）。更新身份账本。
- [ ] 检查差异与源文件行数，提交 `refactor(bcs): remove unused callback and build dependencies`。

## 任务 3：抽取迁移库并保留调用兼容

**文件：** 新迁移库、bootstrap migrations 模块、附录 D 源码与所属 manifest、
`Cargo.toml`、`Cargo.lock`、bootstrap CONTEXT.md、`scripts/ci/check-deps.sh`。

- [ ] 先运行原 `cargo test --locked -p bcs --lib migrations::`，保留 49 个测试
  的名称与结果。归档其全部子模块；不得在抽取时“修正”历史 SQL。
- [ ] 新建库并移动现有执行器及其子目录；从 bootstrap 到 services 的目录层级
  相同，原执行器内指向 BCS migrations 资源的相对深度可保留，但仍逐项解析验证。

```bash
mkdir -p crates/services/bcs-db-migrations/src
git mv crates/bootstrap/bcs/src/migrations.rs crates/services/bcs-db-migrations/src/lib.rs
git mv crates/bootstrap/bcs/src/migrations crates/services/bcs-db-migrations/src/migrations
```

新 `crates/services/bcs-db-migrations/Cargo.toml`：

```toml
[package]
name = "bcs-db-migrations"
description = "BCS SQLite schema initialization and versioned migrations"
version.workspace = true
edition.workspace = true
license.workspace = true
repository.workspace = true
rust-version.workspace = true

[lints]
workspace = true

[dependencies]
bcs-db-api = { workspace = true }
sha2 = { workspace = true }
hex = { workspace = true }

[dev-dependencies]
bcs-db-local = { workspace = true }
tokio = { workspace = true }
```

- [ ] 在 workspace members 加入新目录，在 workspace.dependencies 加入
  `bcs-db-migrations = { path = "crates/services/bcs-db-migrations" }`；bootstrap
  普通依赖加入 `bcs-db-migrations = { workspace = true }`。
- [ ] 重建 bootstrap `src/migrations.rs` 为以下薄转发，保持全部既有公开符号：

```rust
//! Compatibility exports for the BCS SQLite migration runner.
pub use bcs_db_migrations::{
    SqliteMigrationPlan, SqliteMigrationReport, check_sqlite_migrations,
    run_sqlite_bootstrap_indexes, run_sqlite_bootstrap_tables,
    run_sqlite_migrations, run_sqlite_migrations_with_report,
    run_sqlite_versioned_migrations, sqlite_migration_count, sqlite_target_version,
};
```

- [ ] 对附录 D 的每个源码包含，删除对应 `#[path]` 属性，保留原别名替换为
  `use bcs_db_migrations as migrations;` 或
  `use bcs_db_migrations as bootstrap_migrations;`。删除只用于原 mod 的属性，
  不把 `#[path]` 悬挂到 use 上。所属包加入开发依赖（bootstrap 是普通依赖）。
- [ ] 按附录 E 拆分本批触及的 `bcs-group-store/tests/conformance_group_repo.rs`
  和 `bcs-collaboration-store/tests/mysql_store.rs`，保持原 binary 名及唯一用例。
  首个文件保留顶层调用中央 harness 的 conformance 用例，避免既有 R25 检查失效。
- [ ] 检查 `crates/tools/bcs-admin/src/migrate.rs` 中的源码路径断言/引用，更新
  到新权威位置；它不是第 30 个 `#[path]` 消费者。更新 CONTEXT.md，声明普通
 依赖只允许 bcs-db-api，dev-dependency 允许真实 SQLite 实现。
- [ ] 在 `check-deps.sh` 加入新库存在性和路径依赖检查；普通和 build 路径依赖
  仅允许 bcs-db-api，不允许借用别名绕过。可复用已有 metadata，检查逻辑如下：

```bash
count=$(echo "$metadata" | jq '[.packages[] | select(.name == "bcs-db-migrations")] | length')
if [[ "$count" != 1 ]]; then
  log FAIL migration "bcs-db-migrations must exist exactly once"
  fail=1
fi
while IFS= read -r dep; do
  if [[ "$dep" != bcs-db-api ]]; then
    log FAIL migration "bcs-db-migrations has forbidden runtime/build path dependency: $dep"
    fail=1
  fi
done < <(echo "$metadata" | jq -r '.packages[] | select(.name == "bcs-db-migrations") |
  .dependencies[] | select(.kind != "dev" and .path != null) | .name')
```

- [ ] 更新锁文件但不升级依赖，运行新库 49 个测试、所有附录 D 所属包的测试及
  `cargo check --locked --workspace --all-targets`。确认 callback 替换为迁移库后
  members 恢复 112，没有 bootstrap/plugin/app 反向依赖。
- [ ] 校验 `bcs::migrations` 仍能构建；用 SQL 资源 hash、完整旧测试和迁移报告
  对照证明迁移结果/历史/checksum/错误传播一致。SQLite 空库、保留旧记录的升级、
  重复启动均要覆盖；保留失败返回错误的测试，缺项才补有意义的行为断言。
  运行 `git diff --exit-code "$BCS_CLEANUP_BASE" -- migrations`，预期无差异；
  对移动的 Rust 执行器执行 `git diff --find-renames` 人工核对，除资源解析路径、
  模块声明和导入外，DDL/执行分支/错误处理不变。
- [ ] 执行 `cargo test --locked -p bcs-db-migrations` 和
  `cargo test --locked -p bcs --lib`；按附录 D 所属包逐个执行 `cargo test --locked -p`
  后接实际包名。依赖包的 MySQL ignored 契约由任务 7 显式执行，不以默认 skipped
  代表已覆盖真实 MySQL。
- [ ] 导出 after inventory，按任务 1 校验重复迁移用例归并；搜索结果不得再有
  引入 bootstrap migrations 的 `#[path]`。提交
  `refactor(bcs): share SQLite migrations across runtime and tests`。

## 任务 4：让 conformance 门禁支持嵌套测试文件

**文件：** `scripts/ci/check-r25-conformance.sh`、
`scripts/ci/tests/conformance_layout_regression.sh`。

- [ ] 先新增下列脚本，用最小目录模拟嵌套 conformance、被删除的 harness 调用、
  无 conformance 三种状态。使用 fake cargo 仅隔离该结构检查的测试，不改真实门禁。

```bash
#!/usr/bin/env bash
set -euo pipefail
checker="$(cd "$(dirname "$0")/.." && pwd)/check-r25-conformance.sh"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/crates/test-support/bcs-test-support/src/contract"
mkdir -p "$fixture/crates/service-api" "$fixture/crates/plugin-api"
mkdir -p "$fixture/crates/services/example/tests/cases" "$fixture/bin"
printf '#!/bin/sh\nprintf "conformance_fixture: test\\n"\n' > "$fixture/bin/cargo"
chmod +x "$fixture/bin/cargo"
export PATH="$fixture/bin:$PATH"
casefile="$fixture/crates/services/example/tests/cases/conformance_fixture.rs"
printf 'bcs_test_support::contract::fixture_contract_tests();\n' > "$casefile"
(cd "$fixture" && bash "$checker")
printf 'fn conformance_fixture() {}\n' > "$casefile"
if (cd "$fixture" && bash "$checker"); then
  echo 'FAIL: missing centralized harness accepted'; exit 1
fi
rm "$casefile"
if (cd "$fixture" && bash "$checker"); then
  echo 'FAIL: empty conformance inventory accepted'; exit 1
fi
```

- [ ] 运行 `bash scripts/ci/tests/conformance_layout_regression.sh`，原 checker
  应在第一个有效嵌套场景失败；记录失败原因确实是扫描范围，而非缺少 rg。
- [ ] 将 checker 两处 `find crates -path '*/tests/conformance_*.rs' -type f | sort`
  精确替换为以下命令，仍限定 tests 子树并保留逐实现 harness 检查：

```bash
find crates -type f -path '*/tests/*' -name 'conformance_*.rs' | sort
```

- [ ] 重跑 regression 脚本应通过，再跑真实 `bash scripts/ci/check-r25-conformance.sh`。
  原门禁基线问题单独记录，不借本任务扩大 waiver 或吞掉错误。
- [ ] 提交 `ci(bcs): preserve conformance checks for nested test suites`。

## 任务 5：message-flow 试点，逐 suite 合并

**文件：** `crates/services/bcs-message-flow/tests/`，精确入口见附录 A；
附录 E 的两个超长入口及拆分子模块；已拆分的 contract_message_flow 按下述
既有子树迁移规则处理。

- [ ] 先按附录 E 拆分超长文件：共享 imports/helper 留在父模块；完整测试函数
  搬入命名子模块，子模块开头使用 `use super::*;`，父模块显式 `mod`。每个新增
  子模块 ≤1,000 行；不能截断函数/impl，也不能按固定行数切割 Rust 字符串。
- [ ] 每次只处理附录 A 的一个 suite，把成员文件移动到 `tests/cases/`。
  保留原 stem 作为模块名；为附录 E 的子模块保留同一父子结构。
- [ ] `contract_message_flow.rs` 已在上游拆为 12 个业务模块和 6 个 support
  文件；保留这些模块名和函数身份，将其现有子树与父入口一同移动：

```bash
git mv crates/services/bcs-message-flow/tests/contract_message_flow.rs \
  crates/services/bcs-message-flow/tests/cases/contract_message_flow.rs
git mv crates/services/bcs-message-flow/tests/contract_message_flow \
  crates/services/bcs-message-flow/tests/cases/contract_message_flow
```

  在移动前创建 `tests/cases/`。父入口原有 `contract_message_flow/...` 路径在
  整棵子树移动后仍有效；仅把其共享 flow/session support 接到 suite 根。
  不重新搬回单文件，也不按旧附录重新命名现有 `session_history`、
  `web_send_*`、`bot_event_callback`、`attachments` 等子模块。
- [ ] 新入口例如 `tests/suite_run_state.rs` 的完整模块清单如下；其他入口按
  附录 A 同样逐项声明，每行只引用一份源文件：

```rust
#[path = "cases/conformance_run_budget.rs"]
mod conformance_run_budget;
#[path = "cases/conformance_run_deadline.rs"]
mod conformance_run_deadline;
#[path = "cases/run_context_redis.rs"]
mod run_context_redis;
#[path = "cases/run_store_engine.rs"]
mod run_store_engine;
```

- [ ] suite 内重复的 message-flow support 和 session support 改为 suite 根各
  声明一次；引用的 case 模块改用原别名，完整形式如下：

```rust
// At tests/suite_delivery.rs (only declare what this suite uses):
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

// In a moved case formerly declaring these two modules:
use crate::flow_support as support;
use crate::session_support;
```

- [ ] 逐个更新相对资源路径：case 文件比原 tests 根深一级，原 include_str 和
  非共享 `#[path]` 必须保持解析到同一个物理文件；对 support 中依赖 `super::*`
  的场景保持父模块，不把这些 helper 移成 suite 全局。同理核对 `mod foo;`
  默认解析、内层 `#![...]` 属性和 `crate::` 引用，不能全局替换路径字符串。
- [ ] 特别保留上游新增的 `caller_owner_relay_contract` →
  `support/native_owner_task.rs` → `support/protected_dispatch_io.rs` 挂载链。
  父 case 移入 cases 后其第一段 path 改为 `../support/native_owner_task.rs`，
  support 文件本身留在原目录，内部的 `protected_dispatch_io.rs` 路径不变。
  Caller/Owner、身份继承、TTL 和失败关闭等新增断言必须出现在身份账本中。
- [ ] 保留 `caller_p2c_e2e_contract`、`relay_capture_diagnostics_contract`、
  `delivery_policy_audit` 三个独立 target：它们使用全局 tracing subscriber。
- [ ] 每个 suite 完成后运行精确入口；全部完成后运行整个包并核对身份账本：

```bash
cargo test --locked -p bcs-message-flow --test suite_run_state
cargo nextest run --locked -p bcs-message-flow --test suite_run_state --retries 0
cargo test --locked -p bcs-message-flow
cargo nextest run --locked -p bcs-message-flow --retries 0
```

- [ ] 比较第 3 批结束状态与本批状态的测试 binary 总字节和耗时；使用任务 8
  测量协议，确认唯一用例不丢失、byte 下降且无确认的耗时退步后，才进入 HTTP。
  提交 `refactor(bcs): consolidate message-flow test binaries`。

## 任务 6：HTTP 测试入口收敛

**文件：** `crates/adapters/http/bcs-http/tests/`，附录 B 的精确映射；
附录 E 的七个超长入口及拆分子模块。

- [ ] 按附录 E 先拆超长入口，每次保持用例完整、名称可追溯。将拟合并入口
  移入 `tests/cases/`，现有 `*_parts/` 及 support 可保留原位置并修正引用。
- [ ] 每个 suite 在根声明一次 `#[path = "support/mod.rs"] mod support;`（仅
  有消费者的 suite），case 中原 `mod support;` 替换成 `use crate::support;`。
  case 专有 helper 留在自己的模块，避免同名 fake 被错误共用。
- [ ] 创建附录 B 的八个入口。最小完整例子 `tests/suite_cli.rs`：

```rust
#[path = "cases/cli_service_integration.rs"]
mod cli_service_integration;
#[path = "cases/cli_session_integration.rs"]
mod cli_session_integration;
```

- [ ] `request_observations.rs` 与新增 `network_tracing_otel.rs` 保留独立 target，
  两者都修改 OpenTelemetry 全局 propagator。保留新增测试的 `blocking`、
  `reqwest13` feature、provider 接线及无 provider 行为断言。测试模式不改。
- [ ] `bot_chat_contract.rs`、`bot_events_contract.rs` 也设置 W3C propagator，
  现计划在同一 suite_bots 内运行；核对所有成员用例没有要求不同的进程初始
  propagator，并用普通 cargo test 验证。若存在依赖冲突，保留独立 target
  并修订计数，不全局串行化整个 crate。
- [ ] 每完成一个 suite，执行对应 cargo test 和 nextest，再运行包级验证：

```bash
cargo test --locked -p bcs-http --test suite_cli
cargo nextest run --locked -p bcs-http --test suite_cli --retries 0
cargo test --locked -p bcs-http
cargo nextest run --locked -p bcs-http --retries 0
```

- [ ] 执行任务 1 的完整身份核对及任务 8 的本批前后对照。确认 HTTP 入口预计为 10 个，
  每个未合并例外均有理由。提交 `refactor(bcs): consolidate HTTP contract test binaries`。

## 任务 7：修正引用并跑完整门禁

**文件：** 仅实际引用变化处；重点检查 BCS `.config/nextest.toml`、
`scripts/ci_contract_test.py` 调用者、`scripts/ci/check-r25-conformance.sh`、
`scripts/e2e_coverage.sh`，仓库根 `.github/workflows/unit-tests.yml` 和
`singlebox/ci/singlebox_coverage_modules.yaml`。

- [ ] 按附录 A/B 的旧 binary 名搜索仓库的脚本、配置、文档，更新实际失效引用。
  不改 `integration_collab_reachability` 的已有 nextest 资源限制。
- [ ] 重新导出 workspace metadata/inventory，执行任务 1 校验。检查每个旧
  conformance 用例能被新入口实际发现，不仅是源码文件还存在。
- [ ] 将新增 tracing 包及测试计入当前基线：`bcs-network-tracing` 四个独立
  integration target、`bcs-storage-baas::tracing_scope` 不在本期合并范围，
  但必须在完整测试清单中保持。新 `bcs-tracing-api` 和网络 adapter 的依赖
  边界、no-provider 语义、client default headers 传播均不可因精简改变。
- [ ] 运行架构 gate 和普通 workspace 构建；既有基线错误原样披露，不降低门槛：

```bash
cargo check --locked --workspace --all-targets
bash scripts/ci/arch-check.sh
bash scripts/ci/tests/conformance_layout_regression.sh
bash scripts/ci/tests/import_scope_regression.sh
```

上游 `check-import-rules.sh` 已按 Rule 5 排除 crate 根 `tests/**` 的合法组合，
仍检查生产 `src/**`（包括 src/tests）和契约内部边界。沿用这一实现及其
11 个正反例，不重复放宽规则，也不将本期迁移库加入生产扫描排除项。

- [ ] 使用现有 CI 单测构建复用入口及覆盖率 gate；runner 可能以 0 退出而报告
  中仍有失败，必须检查 JUnit 和 cov_gate 的结果，不能只看脚本退出码。

```bash
SERVER_ENV=dev bash scripts/ci_test.sh --coverage --reuse-build
python3 scripts/cov_gate.py --base-ref "$BCS_CLEANUP_BASE" --bcs-dir "$BCS_ROOT" \
  --pass-rate-min 100 --overall-line-min 87 --changed-line-min 85 --changed-line-min-lines 51
```

- [ ] 运行当前 workflow 中全部 MySQL 定向契约及完整迁移链；为每个要求独立
  数据库的契约准备空库，并通过 BCS_TEST_MYSQL_URL 注入现有 runner。第一个命令：

```bash
python3 scripts/ci_contract_test.py --package bcs-admin --binary bcs-admin \
  --test full_mysql_migration_chain_applies_and_preserves_history
```

  其余命令从当前 `.github/workflows/unit-tests.yml` 的
  `python3 scripts/ci_contract_test.py` 行逐项执行并登记；预期每项严格一个
  ignored 契约被显式执行且通过，0 个匹配不是成功。不得把所有契约指向同一
  已使用数据库。复用 instrumented binary，保留单测 XML 不受 MySQL 运行改写。
- [ ] 对照 coverage 报告源文件列表，确认新 `services/bcs-db-migrations/src/`
  仍在原生产分母内。只有实际路径映射遗漏时修改映射，不扩展 exclude。
- [ ] 从仓库根运行完整共享栈及 artifact verifier，不只运行独立 BCS E2E：

```bash
cd "$BCS_REPO_ROOT"
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

**预期：** 100% 单测通过、总体行覆盖率 >87%、满足现有 changed-line 规则；
Singlebox 的 BCS line ≥54%、method ≥48%、endpoint/CLI=100%，满足 E2E
changed-line 规则及全部 artifact 要求。数据库、工具或平台缺失时明确阻塞的
验证项，不标记本任务已完成。

## 任务 8：测量收益、记录结果与交付

**文件：** 本目录 `validation.md`；大型 JSON、日志、binary、profraw 留在临时目录。

- [ ] 对 before、每个测试收敛批次前后、最终 after，分别测普通开发与 instrumented
  构建。每组至少三次；相同工具链、features、job 数与已预取依赖。每个冷构建
  样本使用新建的专用 CARGO_TARGET_DIR；样本结束后仅清理本任务新建 scratch
  target，保留清单和尺寸数据，不删除用户 target 或 Cargo 下载缓存。
- [ ] 普通测试构建用 `cargo test --locked --workspace --no-run --message-format=json`
  捕获 compiler-artifact，记录 `profile.test=true` 且 executable 非空的路径。
  用文件 `(st_dev, st_ino)` 去重后汇总 `st_size`；同时保存每个 executable 的字节。
  服务 binary 单独 `cargo build --locked -p bcs --bin bcs`，记录实际产物路径和大小。
- [ ] 对同一 target 紧接着重复构建三次测无修改重建；受控增量实验分别在两个
  试点的一条原有 assertion 中做等价表达式变换，记录准确 patch，在对应 baseline
  与 after 文件位置施加同一变换。每次从未变更状态开始并恢复，只用于测量，
  不提交该 patch。不能拿修改文档或冷构建冒充增量源码重建。
- [ ] instrumented 构建用现有 `ci_test.sh --coverage --reuse-build` 记录总体时间
  和编译阶段日志；沿用该脚本的实际 target 布局采集尺寸，不假设 profraw 与
  binary 在同一目录。用产出的 cargo/test-binaries metadata 确定真实 target。
- [ ] 使用下面的一次性计时器包裹测量命令，参数为输出 JSON 文件和完整命令；
  退出码非 0 的样本保留失败状态，不纳入成功样本中位数：

```python
import json
import subprocess
import sys
import time
from pathlib import Path

output, command = Path(sys.argv[1]), sys.argv[2:]
started = time.perf_counter()
result = subprocess.run(command)
output.write_text(json.dumps({
    "command": command,
    "seconds": time.perf_counter() - started,
    "exit_code": result.returncode,
}, indent=2) + "\n")
raise SystemExit(result.returncode)
```

- [ ] `validation.md` 填入：目标数、成员数、真正删除的源码行、依赖处置表、
  用例映射摘要、migration 资源 hash 对照、测试 binary 字节、deps/build/incremental
  分类字节、服务 binary 字节、各时间样本及中位数/范围、完整验证结果。
  按同一口径排除日志、报告、profraw，注明硬链接/逻辑字节与 du 的差别。
- [ ] 若试点测试 binary 字节未下降，或确认耗时中位数回退 >5%，调整分组并重新
  运行受影响验证；不能用别批收益掩盖本批退步。无法改善则回滚该批并记录差额。
- [ ] 最后检查 `git diff --check`、所有新增/修改源文件行数、无 SQL 内容变化、
  无 fixture 删除、无新增忽略测试/覆盖排除。按 spec 逐项填写验收状态。
- [ ] 使用 `verification-before-completion` 和 `requesting-code-review` 技能完成
  交付检查，记录剩余问题。仅在实际满足验收时将 spec 状态改为完成；提交验证
  文档。任何未验证项目必须出现在最终报告，不宣称已有 GB 或耗时降幅。

## 回滚与停靠点

任务 2、任务 3、任务 4、任务 5、任务 6 各自独立提交。迁移库抽取必须连同全部
调用者替换在同一批验证，不保留双份执行器。回滚 suite 合并时同时恢复它的
路径、入口、fixture 引用、身份映射和 CI 选择器；migration 无数据逆迁移。
全局门禁或兼容性失败时停在最近通过的批次，不禁用 gate。

本计划不替代架构契约或历史迁移规则。成员数 112、targets 506 都是当前基线
下的预期；真正的验收依据是完整性、兼容性、测试通过与同条件构建收益。

## 附录 A：message-flow 精确入口映射

根：`crates/services/bcs-message-flow/tests/`。表内旧名称均指 `<名称>.rs`；
除标注保留的独立入口外，统一移动到 `cases/<名称>.rs`，并由目标入口通过
`#[path = "cases/<名称>.rs"] mod <名称>;` 声明。拆分文件的父入口保留同名。

| 目标入口 | 原入口（全部保留用例） |
| --- | --- |
| `suite_routing.rs` | `authority_cutover`、`bounded_context`、`caller_dm_web_send_contract`、`contract_message_flow`、`delivery_session_access`、`delivery_through_bot_bounds`、`delivery_via_controlled_bot`、`observe_fanout_contract`、`routed_reply_derivation_contract`、`speaking_lease_web_send_contract`、`web_send_session_membership` |
| `suite_caller_admission.rs` | `caller_admission_contract`、`caller_dispatch_access_revalidation`、`caller_dispatch_commit_contract`、`caller_dispatch_pipeline_contract`、`caller_required_routes_gate` |
| `suite_caller_execution.rs` | `caller_dispatch_cancel_contract`、`caller_dispatch_stage_timeout_contract`、`caller_execution`、`caller_execution_control_contract`、`caller_existing_events_contract`、`caller_handoff_lifecycle`、`caller_history_barrier_contract`、`caller_observe_fanout_wiring_contract`、`caller_owner_relay_contract`、`caller_protected_dispatch_runtime_contract`、`caller_terminal_receipt_contract` |
| `suite_delivery.rs` | `conformance_delivery_scheduling`、`conformance_managed_delivery`、`conformance_message_delivery`、`conformance_queued_group`、`conformance_queued_system`、`conformance_queued_task`、`delivery_failure_notifications`、`delivery_history_query`、`delivery_policy`、`delivery_query_visibility`、`delivery_runtime`、`managed_delivery`、`operation_output_admission`、`operation_output_lifecycle`、`operation_output_uncertainty`、`queued_provider_headers` |
| `suite_coordination.rs` | `a2a_deadline_cache`、`conformance_direct_a2a`、`conformance_message_flow_services`、`contract_a2a_chat`、`contract_bot_event`、`contract_coordination_chat_final`、`contract_coordination_tool_identity`、`contract_group_fusion`、`contract_human_notify_context`、`run_reply` |
| `suite_run_state.rs` | `conformance_run_budget`、`conformance_run_deadline`、`run_context_redis`、`run_store_engine` |
| 原名保留，单独编译 | `caller_p2c_e2e_contract`、`delivery_policy_audit`、`relay_capture_diagnostics_contract` |


## 附录 B：HTTP 精确入口映射

根：`crates/adapters/http/bcs-http/tests/`。移动和模块声明规则同附录 A。

| 目标入口 | 原入口（全部保留用例） |
| --- | --- |
| `suite_bots.rs` | `avatar_response_contract`、`avatar_write_contract`、`bot_chat_contract`、`bot_delivery_switch_contract`、`bot_events_contract`、`bots_contract`、`bots_my_contract`、`discover_contract` |
| `suite_providers.rs` | `a2a_card_routes`、`a2a_discovery_routes`、`a2a_management_errors`、`channel_provider_routes_contract`、`provider_caller_config_routes_contract`、`provider_routes_contract`、`provider_slug_contract`、`provider_webhook_routes_contract` |
| `suite_groups.rs` | `group_context_routes`、`group_messages_contract`、`group_no_session_contract`、`groups_contract`、`legacy_group_event_subscription_contract`、`legacy_group_patch_contract`、`organizations_contract` |
| `suite_sessions.rs` | `session_collection_contract`、`session_complete_contract`、`session_create_contract`、`session_create_request_dto`、`session_delete_contract`、`session_files_member_gate_contract`、`session_list_contract`、`session_members_contract` |
| `suite_auth.rs` | `actor_error_propagation`、`authority_cutover`、`authority_registration_routes`、`friend_connections_contract`、`friends_actor_auth_contract` |
| `suite_collaboration.rs` | `caller_e2e_route_contract`、`caller_ingress_extraction_contract`、`chat_message_fuse_contract`、`collaboration_drafts_contract`、`service_routes_contract`、`task_handle_contract` |
| `suite_routes.rs` | `delivery_policy_contract`、`manifest_contract`、`r7_adapter_isolation`、`route_boundary_contract`、`route_contract`、`secret_diagnostics`、`test_error_response` |
| `suite_cli.rs` | `cli_service_integration`、`cli_session_integration` |
| 原名保留，单独编译 | `request_observations`、`network_tracing_otel` |

## 附录 C：依赖候选完整清单

每个路径相对 BCS 根。包名见 manifest；普通依赖共 34 条，21 个包。
表中列名不出现于源码只是候选信号，任务 2 的编译验证才决定删除。

| Manifest | 候选声明 |
| --- | --- |
| `crates/bootstrap/bcs/Cargo.toml` | `dirs`、`form_urlencoded`、`if-addrs`、`native-tls`、`regex`、`tokio-native-tls` |
| `crates/application/v1/bcs-app-invitation/Cargo.toml` | `serde_json` |
| `crates/external-clients/bcs-a2a-http/Cargo.toml` | `a2a-client`（实际包名 `a2a-client-lf`，须显式保留 reqwest feature） |
| `crates/plugins/bcs-auth-github/Cargo.toml` | `axum`、`bcs-jwt`、`serde_json` |
| `crates/plugins/bcs-auth-google/Cargo.toml` | `bcs-jwt`、`serde_json` |
| `crates/plugins/bcs-auth-oauth/Cargo.toml` | `tracing` |
| `crates/plugins/bcs-auth-session/Cargo.toml` | `tracing` |
| `crates/services/bcs-chat-run-store/Cargo.toml` | `tracing` |
| `crates/tools/bcs-cli/Cargo.toml` | `base64`、`bcs-config-api`、`dirs`、`encoding_rs` |
| `crates/services/bcs-group/Cargo.toml` | `thiserror` |
| `crates/services/bcs-group-store/Cargo.toml` | `uuid` |
| `crates/services/bcs-invite-code-store/Cargo.toml` | `serde` |
| `crates/services/bcs-judge/Cargo.toml` | `tracing` |
| `crates/services/bcs-message-store/Cargo.toml` | `thiserror` |
| `crates/services/bcs-routing/Cargo.toml` | `serde`、`serde_json` |
| `crates/services/bcs-session-file/Cargo.toml` | `serde` |
| `crates/services/bcs-session-file-store/Cargo.toml` | `tracing` |
| `crates/services/bcs-session-store/Cargo.toml` | `fastrand` |
| `crates/plugins/bcs-storage-baas/Cargo.toml` | `thiserror`、`tracing` |
| `crates/plugins/bcs-storage-local/Cargo.toml` | `anyhow` |
| `crates/adapters/ws/bcs-ws/Cargo.toml` | `bcs-route-security` |

## 附录 D：迁移源码包含替换清单

下列 29 个文件相对 BCS 根，分别替换其源码包含为 `bcs_db_migrations` 库导入，
所属包加入 dev-dependency。实际别名沿用原模块名。

- `crates/application/v1/bcs-app-group/tests/authorization_snapshot.rs`
- `crates/application/v1/bcs-app-session/tests/authorization_snapshot.rs`
- `crates/services/bcs-bot/tests/bot_profile.rs`
- `crates/services/bcs-bot-store/tests/affiliation_scope.rs`
- `crates/services/bcs-bot-store/tests/bot_avatar_queries.rs`
- `crates/services/bcs-bot-store/tests/conformance_bot_profile.rs`
- `crates/services/bcs-collaboration-runtime/tests/runtime_progression/history_persistence.rs`
- `crates/services/bcs-collaboration-runtime/tests/runtime_progression/recovery_sqlite.rs`
- `crates/services/bcs-collaboration-store/tests/mysql_store.rs`
- `crates/services/bcs-event-store/tests/conformance_event_delivery_store.rs`
- `crates/services/bcs-event-store/tests/conformance_event_store.rs`
- `crates/services/bcs-event-store/tests/idle_claims.rs`
- `crates/services/bcs-group/tests/workbench_target_membership.rs`
- `crates/services/bcs-group-store/tests/authorization_snapshot.rs`
- `crates/services/bcs-group-store/tests/conformance_eventful_mutations.rs`
- `crates/services/bcs-group-store/tests/conformance_group_repo.rs`
- `crates/services/bcs-group-store/tests/conformance_human_notify_policy.rs`
- `crates/services/bcs-group-store/tests/group_card_templates.rs`
- `crates/services/bcs-group-store/tests/provider_scope.rs`
- `crates/services/bcs-message-flow/tests/conformance_direct_a2a.rs`
- `crates/services/bcs-message-flow/tests/conformance_queued_task.rs`
- `crates/services/bcs-message-flow/tests/conformance_run_deadline.rs`
- `crates/services/bcs-message-store/tests/conformance_delivery_repo.rs`
- `crates/services/bcs-message-store/tests/conformance_message_repo.rs`
- `crates/services/bcs-organization-store/tests/conformance_organization_repo.rs`
- `crates/services/bcs-session-store/tests/authorization_snapshot.rs`
- `crates/services/bcs-session-store/tests/conformance_session_repo.rs`
- `crates/services/bcs-session-store/tests/group_session_membership.rs`
- `crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs`

## 附录 E：本期超长测试文件的拆分职责

MF 根为 `crates/services/bcs-message-flow/tests/`，HTTP 根为
`crates/adapters/http/bcs-http/tests/`。最终父入口是 `cases/<原 stem>.rs`，
其子模块放在 `cases/<原 stem>/<下列名称>.rs`。原有测试与 helper 均完整移动；
父模块保留其余共有 imports/fixture，必要时放入同目录 `fixtures.rs` 并重新导出。

| 原入口 | 行数 | 子模块职责与确切文件名 |
| --- | ---: | --- |
| MF `contract_a2a_chat.rs` | 2,278 | `run_start.rs`（创建/提交/首事件）、`run_terminal.rs`（完成/取消/超时）、`event_parser.rs`、`access.rs`（visibility/friendship）、`organization.rs` |
| MF `delivery_runtime.rs` | 1,134 | `queue.rs`（worker/队列调度）、`lifecycle.rs`（完成/失败/恢复） |
| HTTP `bot_events_contract.rs` | 2,836 | `authentication.rs`、`task_events.rs`、`agent_events.rs`、`coordination.rs`、`redaction.rs` |
| HTTP `group_messages_contract.rs` | 2,224 | `history.rs`、`access.rs`、`sending.rs` |
| HTTP `groups_contract.rs` | 3,029 | `creation.rs`、`membership.rs`、`metadata.rs`、`visibility.rs` |
| HTTP `provider_routes_contract.rs` | 3,513 | `registration.rs`、`bot_lifecycle.rs`、`attributes.rs`、`management.rs`、`caller_caps.rs` |
| HTTP `route_contract.rs` | 2,762 | `bots.rs`、`groups.rs`、`identity.rs`、`chat_runs.rs`、`health.rs` |
| HTTP `service_routes_contract.rs` | 1,038 | `invocation.rs`、`access.rs`、`state_machine.rs` |
| HTTP `session_collection_contract.rs` | 1,392 | `collection.rs`、`authorization.rs`、`ordering.rs` |

另有三个不参与 suite 合并、但修改路径会触及的超长文件。它们保留原顶层入口，
子模块位于原 tests 目录下的 `<原 stem>/`，不放到上述 `cases/` 目录：

| 原入口（相对 BCS 根） | 行数 | 子模块职责与确切文件名 |
| --- | ---: | --- |
| `crates/bootstrap/bcs/tests/e2e_workbench.rs` | 1,106 | `group_chat.rs`、`protocol.rs`、`authorization.rs`、`broadcast.rs`；分别承载现有四个测试，共享 setup 留父模块 |
| `crates/services/bcs-group-store/tests/conformance_group_repo.rs` | 1,124 | `configuration.rs`（opening/notify/routing policy）、`atomic_mutations.rs`（provision/member/delete）；中央 harness 的顶层 conformance 用例保留原入口 |
| `crates/services/bcs-collaboration-store/tests/mysql_store.rs` | 1,938 | `definitions.rs`、`run_lifecycle.rs`、`failure_paths.rs`；fake DB/row builder 留父模块或同目录 `fixtures.rs`，现有 ignored MySQL 和 support 子模块保持挂载 |

拆分的 Rust 结构以完整的 `service_routes_contract` 父模块声明为例：

```rust
#[path = "service_routes_contract/invocation.rs"]
mod invocation;
#[path = "service_routes_contract/access.rs"]
mod access;
#[path = "service_routes_contract/state_machine.rs"]
mod state_machine;
```

每个子模块写入 `use super::*;` 后，把属于其职责的现有完整测试函数连同属性
搬入；不复制函数、不改变断言、不生成新的替代测试。仅被一个子模块使用的
helper 跟随消费者，共有 helper 由父模块提供。`super::*` 的现有孙模块需维持
父作用域。测试身份账本记录新增模块层级，不能简单剥掉前缀混淆同名测试。

边界检查以完整 item 为单位：一个职责仍超过 1,000 行时按该职责中的正常路径
与拒绝/失败路径拆为 `<职责>_success.rs` 和 `<职责>_failure.rs`，更新父模块声明
与账本并在 validation.md 登记。这是同职责拆分规则，不是绕过文件大小检查；
单个 item 若超限，需单独按 setup/执行/断言职责拆解并运行对应测试。

## 计划自查与执行交接

本计划覆盖 spec 的四批结构调整，以及独立的基线、结构门禁、全局验证和收益
测量任务。附录 A/B 已用固定远端 SHA 的 Cargo metadata 校验 60+53 个入口没有遗漏或重复；
附录 C/D 对应 34 条候选和 29 处实际源码包含。规划的 506 个目标包含 5 个保留的
需要隔离的独立入口，不代表实际构建或测试已经通过。

执行可选择在当前会话使用 `executing-plans` 逐批推进，或按任务使用
`subagent-driven-development` 并逐项审查；文档编写阶段不启动实施代理或构建。

本次计划文档已验证：本地链接、空白和占位项检查通过；Python/TOML/Bash 示例
通过语法检查；在临时目录中验证了嵌套 conformance regression 的修正前失败、
修正后通过；inventory 比较器能接受显式改名/迁移去重，并拒绝遗漏、新增未登记
用例和 ignored 状态变化。这些是计划示例的验证，不是 BCS 实现阶段的测试结果。
