# BCS CLI Entry Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成超长文件拆分设计的第 3 单元：将 `bcs-cli/src/main.rs` 按参数、启动准备、命令族及测试职责拆分，保留原 CLI/内部宿主入口、参数、请求、输出和退出行为，使本单元所有修改/新增 Rust 文件 <=1,000 物理行。

**Architecture:** `main.rs` 保留 `main()`、`pub async fn run() -> Result<()>`、preview 早返回、日志/Caller 初始化和穷尽顶层分发。新 `cli/*.rs` 均由原入口通过 `#[path]` 挂成直接子模块；既有 sibling 模块/library 不动。将 `run()` 的鉴权表达式提取为明确区分“继续”和“已输出并返回成功”的内部函数，按原时刻创建 client，命令族只消费一次已有准备结果。

**Tech Stack:** Rust 2024、Clap、Tokio、现有 BcsClient/OAuth inventory 扩展；assert_cmd、wiremock 与既有 HTTP mock 测试。临时 Python 3.12 + tree-sitter 0.25.2/rust grammar 0.24.2 用于核对，不新增产品依赖。

---

## 0. 基准、授权与验收

- 设计：[超长 Rust 文件拆分](../specs/2026-10-28-bcs-large-rust-files-decomposition-design.md) §4、§9、§10；领域边界：`crates/tools/bcs-cli/CONTEXT.md`。
- 规划 HEAD：`f443d720a`，继承 `origin/dev` 基线 `b57b8e2d7`（含 #3240）。第 2 单元已有独立计划，本单元不以它的代码为前提，也不宣称它已实施。
- `crates/tools/bcs-cli/src/main.rs`：**8,049 行**；`run()` 3,106 行，包含 **23 个顶层命令分支**。
- SHA-256：`ea5f0c2e6a7563f4be30741e8b0887d9a7b8b93a37e2b7a69ab7ef354e7de363`。
- bin 单测实际发现 **115 项**：原 `tests` 模块的 **103 项**分成 8 个场景；其余 **12 项**（含原 sibling 模块及 `retired_service_policy_tests`）完整名称不变。保留所有 `#[serial]`、cfg、属性和断言。
- **范围：**29 个源码文件（修改原入口、创建 19 个职责模块、1 个测试装配及8个测试场景）+ 1 个新增集成回归文件，共 **30 个修改/新增 Rust 文件**。不修改 Cargo.toml/Cargo.lock/build.rs。
- 本轮仅生成文档。实施、commit、push/PR 分别按后续授权执行；不顺带实施第 2/4/5 单元。

验收不仅是编译：115 项 bin 测试一一映射，原外部 integration targets 不变，2 项新入口回归通过；debug/release 各自与原基线的完整 help/alias/leaf 清单一致；public 与临时嵌入式宿主鉴权/输出检查；原 HTTP mocks、统一 Singlebox/CLI coverage、changed-line gate 均有真实结果。未验证真实内部 SDK/宿主必须披露。

## 1. 固定文件结构

路径相对 `apps/bcs/crates/tools/bcs-cli/`。附录 A 是原声明、23 个 arm 和 103 项测试的完整归属清单，不按历史行号裁剪。

| 操作 | 文件 | 归属 |
| --- | --- | --- |
| Modify | `src/main.rs` | 原入口、3 个原 sibling 挂载、原常量/宏、初始化、preview、薄分发、retired test module |
| Create | `src/cli/args.rs` | Cli、Commands（全部 Clap 属性） |
| Create | `src/cli/session_args.rs` | SessionCommands、SessionFileCommands |
| Create | `src/cli/collaboration_args.rs` | CollaborationCommands，Draft 类型仍来自 library |
| Create | `src/cli/resource_args.rs` | ChatRun/GroupContext/Channel/Friend/Visibility/Service 子命令 |
| Create | `src/cli/environment.rs` | SessionInfo、NetworkEnv、URL/token/env 解析及网络探测 |
| Create | `src/cli/authentication.rs` | resolve_oauth_credentials、create_client、prepare_authentication |
| Create | `src/cli/output.rs` | 输出结构、structured 判断、日志路径、discover 渲染及 auth 错误分类 |
| Create | `src/cli/bot_commands.rs` | Health、Connect、Onboard、List、Get、Discover、UpdateStatus；parse_skills_input |
| Create | `src/cli/group_commands.rs` | Create/Get/Fuse/List/AddMember/Status/Terminate Group |
| Create | `src/cli/chat_commands.rs` | Chat、ChatRun；保留等待、退出及 Ack 语义 |
| Create | `src/cli/collaboration_commands.rs` | Collaboration 全部子命令，Preview 仍为不可达防御分支 |
| Create | `src/cli/collaboration_input.rs` | binding/participant tags/panel/validation 辅助 |
| Create | `src/cli/group_context_commands.rs` | GroupContext 请求与专属参数 helper |
| Create | `src/cli/relation_commands.rs` | Friend、Visibility |
| Create | `src/cli/channel_commands.rs` | Channel 处理、bind payload 与脱敏 |
| Create | `src/cli/session_commands.rs` | Session 分支；File 交给下行，不再创建第二个 client |
| Create | `src/cli/session_file_commands.rs` | 原 File 内层 match，接收已创建的 client |
| Create | `src/cli/service_commands.rs` | Invoke/Status/Wait 及 SID/等待/摘要 helper；不恢复上游退休字段 |
| Create | `src/cli/input.rs` | parse_json_arg |
| Create | `src/cli/tests.rs` | 原测试 imports、safe_set_var/safe_remove_var/write_session_file 和子模块装配 |
| Create | `src/cli/tests/{arguments,environment,group_context,collaboration,channel,session,service,output}.rs` | 103 项原测试，分别 19/17/17/9/8/14/14/5 项 |
| Create | `tests/cli_decomposition_contract.rs` | 2 项公开 binary 入口次序/鉴权终止回归，完整代码见附录 C |

保持只读：`src/lib.rs`、`src/client.rs`、`src/oauth.rs`、所有原 sibling 文件与其后代、原 integration tests、Cargo manifest/lock、build.rs、Service/Plugin API、迁移、coverage 脚本和阈值。尤其不能为了消重修改 4,864 行的 `client.rs`。

### 1.1 可见性、路径与日志

- 19 个职责模块在入口使用 `#[path = "cli/<name>.rs"] mod <name>;`，**不添加外层 `mod cli`**。
- 原常量 `AUTH_VIA_AGENT_PASS`、`DEFAULT_GROUP_BATCH_SIZE`、`BCS_CLI_VERSION` 留根；root 私有导入移动的类型和 helper，使原 sibling/retired test 仍能访问祖先名称。
- 新项只用实际需要的 `pub(super)`。Cli、GroupListOutput、StructuredResult 被跨模块读/构造的字段需相同可见性；SessionInfo 字段原本已为 `pub`，保持原样，不拼成 `pub(super) pub`。enum variants 无需额外字段修饰。
- `test_runtime` 不属于本单元。这里 `debug_request!`、`debug_response!` 两个宏原文留根且声明先于新模块；禁止 macro_export。
- 新模块依赖根使用 `super::...`；`cli/tests/*.rs` 通过其逻辑 `tests` 父级导入。不能引入假定 binary 根的 `crate::Cli` 等路径。
- 在原入口增加一个私有常量 `const CLI_LOG_TARGET: &str = module_path!();`。仅对从原 main 移走的 `info!`/`debug!`/`warn!` 增加 `target: CLI_LOG_TARGET,`，保留其他 token。这样独立 binary 和嵌入宿主各自保留原有效 target，不硬编码 `bcs_cli`。不修改仍在原 sibling 的日志。
- 原测试 `safe_*` 中既有 unsafe allow 属性保持；不新增 allow 来掩盖 warning。

### 1.2 run() 的结构变更白名单

**不动**：从 `Cli::parse()` 到鉴权表达式之前的全部初始化语句，顺序为 preview →日志/structured OAuth 标志→Caller 校验→env/URL/cookie/debug。特别是 preview 不创建日志、不读取 session/token、不校验 Actor、不探网。

**鉴权提取**：`run()` 原 `let (_network_env, oauth_headers) = if ...` 的 RHS 移到：

```rust
pub(super) async fn prepare_authentication(
    bcs_url: &str,
    debug: bool,
    structured_mode: bool,
    log_file: &Path,
) -> Result<ControlFlow<(), (NetworkEnv, Option<HashMap<String, String>>)>>
```

该函数第一条语句为 `let prepared =` 接**原 RHS 完整表达式**；表达式中两处处理完 structured 失败的
`return Ok(());` 仅改为 `return Ok(ControlFlow::Break(()));`，末尾为 `Ok(ControlFlow::Continue(prepared))`。
`Err`、OAuth callback/flush 和调用顺序不变，不能把已输出失败变成空 headers 继续发请求。
`ControlFlow` 来自 std，无自定义模式/状态配置。root 调用替换为以下完整语句：

```rust
let (_network_env, oauth_headers) = match authentication::prepare_authentication(
    &bcs_url, debug, structured_mode, &log_file,
).await? {
    std::ops::ControlFlow::Break(()) => return Ok(()),
    std::ops::ControlFlow::Continue(prepared) => prepared,
};
```

**client 工厂**：原嵌套 `create_client` 完整提升到 authentication.rs，不提前创建/缓存 client，
保持各 arm 自己的 token/Actor/TRACEPARENT 求值时刻。Health/Connect 原本独立构造 client，
不能统一改成 create_client。原 main 的 `std::process::exit(1)` 不能改成错误返回。

**命令族**：每个 family 有一个 `pub(super) async fn execute(...) -> Result<()>`，函数内
`match command` 搬入附录 A 指定的全部原 arm，再加 `_ => unreachable!("wrong command family")`，
最后 `Ok(())`。root 顶层必须穷尽 23 个变体，不允许 `_`。family 的参数固定为：

```rust
command: Commands,
bcs_url: String,
bcs_cookie: Option<String>,
oauth_headers: Option<HashMap<String, String>>,
```

后接下表必要 bool（按表顺序），不增加通用 CommandContext/registry/新状态组件：

| family | 后续参数 |
| --- | --- |
| bot_commands / group_commands | `json: bool, structured_mode: bool, debug: bool` |
| chat_commands / collaboration_commands | `structured_mode: bool, debug: bool` |
| group_context_commands | 无 |
| relation_commands / channel_commands / session_commands / service_commands | `json: bool, debug: bool` |

原字符串与 Option 被唯一选中的 handler 消费，不 clone。不要直接改成 `bcs_url: &str`：
原 `BcsClient::new(&bcs_url)` 的泛型入参和 Connect 中两处 `bcs_url.clone()` 会改变类型。
所有 arm 中唯一机械变量改名为 `cli.json` → `json`；**不能将它替换成 structured_mode**：
多个旧命令的默认输出与 Chat/Health 不同，GroupContext 则始终 JSON。

完整 root match 用以下生成命令输出到 WORKDIR 后放入原 match 位置（不动其他语句）：

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json, pathlib, sys
w = pathlib.Path(sys.argv[1])
flags = {
    'bot_commands': ['json','structured_mode','debug'],
    'group_commands': ['json','structured_mode','debug'],
    'chat_commands': ['structured_mode','debug'],
    'collaboration_commands': ['structured_mode','debug'],
    'group_context_commands': [],
    **{k: ['json','debug'] for k in ['relation_commands','channel_commands','session_commands','service_commands']},
}
lines = ['match cli.command {']
for module, variants in json.loads((w / 'commands.json').read_text()).items():
    patterns = ' | '.join('Commands::' + v + ('' if v == 'Health' else ' { .. }') for v in variants)
    args = ['command','bcs_url','bcs_cookie','oauth_headers'] + ['cli.json' if x == 'json' else x for x in flags[module]]
    pattern = '(' + patterns + ')' if len(variants) > 1 else patterns
    lines.append(f'    command @ {pattern} => {module}::execute({", ".join(args)}).await?,')
lines.append('}')
(w / 'dispatch.rs.txt').write_text('\n'.join(lines) + '\n')
PY
```

**Session File**：只提取原 `SessionCommands::File { command }` 的 value，即完整
`match command { SessionFileCommands::... }`，放进 `session_file_commands::execute`，
其参数为 `(command: SessionFileCommands, client: &BcsClient, json: bool, debug: bool)`、返回
`Result<()>`，原 match 后 `Ok(())`。原 value 替换为：

```rust
session_file_commands::execute(command, &client, json, debug).await?
```

保留现有上传/下载流、client 和凭据，不额外解析 token 或延迟写文件完成。

## Task 1: 隔离工作区与可恢复基线

- [ ] **Step 1: 阅读规则，选择独立执行工作区并固定原文件。**

先读 root/local AGENTS、CLAUDE、CONTEXT-MAP、CLI CONTEXT 和设计；使用 using-git-worktrees，
复用合适工作区或按用户流程建立。不要在文档分支执行生产重构，不让并行代理写共享入口。

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
bash scripts/install_git_hooks.sh
git switch -c refactor/bcs-cli-entry-modules
export BCS_ROOT="$(git rev-parse --show-toplevel)/apps/bcs"
export BCS_PLAN_BASE="$(git rev-parse HEAD)"
export BCS_PLAN_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/bcs-cli-split.XXXXXX")"
{
  printf 'set -euo pipefail\n'
  printf 'export BCS_ROOT=%q\nexport BCS_PLAN_BASE=%q\nexport BCS_PLAN_WORKDIR=%q\n' \
    "$BCS_ROOT" "$BCS_PLAN_BASE" "$BCS_PLAN_WORKDIR"
  printf 'cd "$BCS_ROOT"\n'
} > "$BCS_PLAN_WORKDIR/env.sh"
printf 'Resume in Bash: source %q\n' "$BCS_PLAN_WORKDIR/env.sh"
source "$BCS_PLAN_WORKDIR/env.sh"
git show "$BCS_PLAN_BASE:apps/bcs/crates/tools/bcs-cli/src/main.rs" > "$BCS_PLAN_WORKDIR/original.rs"
```

若隔离工具已创建目标分支，不重复建分支。后续每个独立 Bash 调用 source 同一个 env.sh，
必须同时恢复目录、变量和 `set -euo pipefail`。

- [ ] **Step 2: 保存指纹、只读文件 hash，验证恢复语义。**

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import hashlib, json, pathlib, subprocess, sys
w = pathlib.Path(sys.argv[1])
assert hashlib.sha256((w/'original.rs').read_bytes()).hexdigest() == 'ea5f0c2e6a7563f4be30741e8b0887d9a7b8b93a37e2b7a69ab7ef354e7de363'
root = pathlib.Path('crates/tools/bcs-cli')
protected = [p for p in (root/'src').rglob('*.rs') if p != root/'src/main.rs']
protected += [p for p in (root/'tests').rglob('*') if p.is_file()]
protected += [root/'Cargo.toml',root/'build.rs',pathlib.Path('Cargo.lock')]
(w/'protected.json').write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(protected)},indent=2))
subprocess.run(['bash','--noprofile','--norc','-c','source "$1"; [[ "$PWD" -ef "$BCS_ROOT" ]]; [[ -o errexit && -o nounset && -o pipefail ]]','bash',str(w/'env.sh')],cwd='/',check=True)
r = subprocess.run(['bash','--noprofile','--norc','-c','source "$1"; (exit 101) | tee /dev/null; echo SHOULD_NOT_RUN','bash',str(w/'env.sh')],cwd='/',capture_output=True,text=True)
assert r.returncode == 101 and 'SHOULD_NOT_RUN' not in r.stdout
print('PASS: pinned source, protected files, restored cwd/options and pipeline failure')
PY
```

源码漂移必须重新核对原 arm、声明及测试清单，不仅修改 hash。保存工具链、环境变量名和
feature，但不要把实际 token/cookie/private endpoints 写日志；业务命令测试使用隔离 HOME 和假凭据。

- [ ] **Step 3: 保存实际 bin 发现、原套件结果、debug/release help。**

将附录 A 保存为 WORKDIR/inventory.json，按下面命令拆为工具输入；附录 B/D/E/F 分别保存为
`audit_cli.py`、`snapshot_help.py`、`build_host.py`、`probe_host.py`，均不入仓库。

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json,pathlib,sys
w=pathlib.Path(sys.argv[1])
for name,value in json.loads((w/'inventory.json').read_text()).items():
    (w/f'{name}.json').write_text(json.dumps(value,indent=2)+'\n')
PY
uv venv --python 3.12 "$BCS_PLAN_WORKDIR/tools"
uv pip install --python "$BCS_PLAN_WORKDIR/tools/bin/python" --default-index https://pypi.org/simple \
  'tree-sitter==0.25.2' 'tree-sitter-rust==0.24.2'
{ git rev-parse HEAD; rustc --version; cargo --version; } > "$BCS_PLAN_WORKDIR/environment.txt"
cargo test -p bcs-cli --bin bcs-cli -- --list > "$BCS_PLAN_WORKDIR/baseline-list.txt" 2> "$BCS_PLAN_WORKDIR/baseline-list.stderr"
cargo test -p bcs-cli 2>&1 | tee "$BCS_PLAN_WORKDIR/baseline-crate.log"
cargo build -p bcs-cli --bin bcs-cli --message-format=json > "$BCS_PLAN_WORKDIR/baseline-debug.jsonl" 2> "$BCS_PLAN_WORKDIR/baseline-debug.stderr"
cargo build -p bcs-cli --bin bcs-cli --release --message-format=json > "$BCS_PLAN_WORKDIR/baseline-release.jsonl" 2> "$BCS_PLAN_WORKDIR/baseline-release.stderr"
```

从两个 JSON 中各取 target 名为 `bcs-cli` 的 `executable`（不要猜 CARGO_TARGET_DIR），复制保存为
WORKDIR/baseline-debug-bin、baseline-release-bin，再调用附录 D。完整操作：

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json,pathlib,shutil,subprocess,sys
w=pathlib.Path(sys.argv[1])
for profile in ['debug','release']:
    rows=[json.loads(line) for line in (w/f'baseline-{profile}.jsonl').read_text().splitlines()]
    binaries={r['executable'] for r in rows if r.get('executable') and r['target']['name']=='bcs-cli'}
    assert len(binaries)==1,binaries
    dest=w/f'baseline-{profile}-bin';shutil.copy2(next(iter(binaries)),dest)
    subprocess.run([sys.executable,str(w/'snapshot_help.py'),str(dest),str(w/f'help-before-{profile}.json')],check=True)
PY
```

help 使用 canonical `scripts/cli_command_coverage.py` 的递归发现，原文件不修改。
每个 profile 和自己基线比较；release 正常没有 `--debug`，不能拿 debug/release help 互相要求相同。
version 的 Git hash/build date 是构建事实，单独记录，不通过冻结 build.rs 来伪造相同版本字符串。

## Task 2: 先建立结构红灯和入口兼容回归

- [ ] **Step 1: 结构 gate 必须因超长原文件失败。**

```bash
set +e
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/audit_cli.py" verify > "$BCS_PLAN_WORKDIR/red.log" 2>&1
result=$?
set -e
test "$result" -ne 0
grep -F 'Root exceeds 1000 physical lines' "$BCS_PLAN_WORKDIR/red.log"
```

这是结构重构，业务基线应为绿灯，不伪造业务失败。ImportError/AST 失败不算合格红灯。

- [ ] **Step 2: 添加附录 C 的两个入口回归测试，并在拆分前运行。**

把附录 C 原文写入 `crates/tools/bcs-cli/tests/cli_decomposition_contract.rs`，不改原有测试：

```bash
cargo test -p bcs-cli --test cli_decomposition_contract \
  2>&1 | tee "$BCS_PLAN_WORKDIR/entry-before.log"
```

预期 2 passed。它们验证 preview 不触发 Caller/auth/log 初始化，以及 public Office auth 的
默认/JSON“输出 auth_failed 且退出 0”与 human“stderr/退出 1”契约，且不能进入 health 请求。
这是既有语义，不因直觉“失败应该退出 1”改动生产行为。

- [ ] **Step 3: 构建原入口嵌入宿主，并测试 linked OAuth 的成功及 callback/失败。**

```bash
python3 "$BCS_PLAN_WORKDIR/build_host.py" before
python3 "$BCS_PLAN_WORKDIR/probe_host.py" "$BCS_PLAN_WORKDIR/embedding-before"
```

宿主是 WORKDIR 中的独立 Cargo 工程，全部依赖来自本 crate 已有公开依赖并额外引用本 library，
不改主 manifest。直接以 `#[path]` 引入真实 main.rs，链接本地 fake OAuth provider，
由自己的 Tokio runtime 调用 `embedded::run()`。success 用回环 HTTP，failure 不联网、不打开浏览器。
分 debug/release 验证 12 组 profile×success/failure×默认/JSON/human 组合。

宿主 `embedded::main` 未被调用可能产生基线 dead-code warning：只记录并比较这份临时
宿主的原/后诊断，不给生产代码加 allow；产品 build/test 仍要求零新增 warning。
真实企业 overlay/SDK 未执行，不把本 smoke 报告成真实内部宿主验收。

- [ ] **Step 4: 在生产源码尚未移动时保存 fresh 统一 coverage 基线。**

```bash
cd "$(git rev-parse --show-toplevel)"
git fetch origin dev
export SINGLEBOX_BASE_REF="$(git merge-base origin/dev HEAD)"
export BCS_E2E_CHANGED_BASE_REF="$SINGLEBOX_BASE_REF"
export BCS_E2E_CHANGED_LINE_MIN="$(python3 -c 'import os; print(max(60.0,float(os.environ.get("BCS_E2E_CHANGED_LINE_MIN","60"))))')"
export BCS_E2E_CHANGED_LINE_MIN_LINES="$(python3 -c 'import os; print(min(20,int(os.environ.get("BCS_E2E_CHANGED_LINE_MIN_LINES","20"))))')"
bash singlebox/ci/singlebox_coverage.sh --coverage-root "$BCS_PLAN_WORKDIR/coverage-baseline" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox-baseline.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir "$BCS_PLAN_WORKDIR/coverage-baseline/reports" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/coverage-baseline-verify.log"
cd "$BCS_ROOT"
```

记录 main.rs 原有 E2E 覆盖及未覆盖分支，作为搬迁后逐文件分母评估依据。
若该基线不可运行，先记录原因并安排可追踪远端验证，不宣称所有实施前检查通过。

## Task 3: 搬运类型、helper 与原测试，暂不拆 run()

- [ ] **Step 1: 按附录 A.helpers 完整迁移声明到指定文件。**

每个声明连同 derive/Clap/serde/cfg/doc 注释移动，不重写内部语句；新函数/类型按 §1.1 的
受限可见性供根和 sibling 使用。root 私有具名导入各模块的原名称，暂时让原 run() 直接调用。
`create_client` 此步也从原 run() 提升，保持原四参数签名和内部时序。新的 log target 只加在
移动的 11 处 tracing 宏，不改日志字段或 debug_request/response 的 eprintln 内容。

- [ ] **Step 2: 将 103 个测试按附录 A.tests 迁移，保留其余 12 项身份。**

根将原 `#[cfg(test)] mod tests { ... }` 替换为：

```rust
#[cfg(test)]
#[path = "cli/tests.rs"]
mod tests;
```

`cli/tests.rs` 留原 imports、三个原辅助函数；通过 `#[path = "tests/<group>.rs"] mod <group>;`
装配八个文件。属性包括无参数的 `#[serial]` 必须原样保留，不改命名锁组或安全环境变量 helper。
原 `retired_service_policy_tests` 保持在 main.rs 原逻辑父级，不与 service 测试合并。
旧 sibling 单测也不移动。新文件用明确导入，parent helper 私有可由后代读取，无需增加 pub。

- [ ] **Step 3: 编译过渡态，解决实际依赖，不提交半成品。**

```bash
cargo test -p bcs-cli --bin bcs-cli --no-run
cargo build -p bcs-cli --bin bcs-cli --release
```

原 main 此时可能仍超过 1,000 行，不能交付该中间态。新模块未使用依赖先删除，不能扩大为
`pub(crate)`、修改 sibling 或用 allow 消除问题。不要运行全局 formatter。

## Task 4: 提取鉴权、命令族与 File 内层分发

- [ ] **Step 1: 按 §1.2 提取 auth 表达式，区分 Continue/Break。**

原表达式两处 structured early return 必须保留终止整个 CLI 的效果；原 `Err` 原样传播。
提升 create_client 后的 traceparent 校验时机仍在命令 arm 内，不能塞入启动统一准备。
通过 Task 2 两个入口回归和临时宿主 smoke 检查后才继续。

- [ ] **Step 2: 按附录 A.commands 搬运全部 23 个 arm，使用 §1.2 的固定签名和生成 root match。**

每个 arm 的模式、所有内层 match、循环/提前返回、错误/退出与打印保持。只允许 `cli.json → json`
和 §1.1 的日志 target 注入。Session File 唯一例外按 §1.2 提取并复用 client。不得额外 clone、spawn、
改 timeout 或改成一次性读取 stream。不要合并 Health/Connect 特有的 client 创建与凭据处理。

- [ ] **Step 3: 运行保持性工具并人工检查薄分发。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/audit_cli.py" verify
git diff --check
```

工具核对原声明/103 测试/23 arm/原初始化前缀、auth RHS、File match、旧 sibling hash 和行数。
它不是完整 Rust 语义证明：仍须人工逐项核对 root dispatch 的 23 个变体各出现一次、无 catch-all，
参数 bool 顺序与本计划生成文件一致、各 handler 尾部 Ok、root ControlFlow 映射、唯一 client 调用次数。
不能只凭 AST 成功跳过编译或业务测试。

规划临时草稿使用 29 个源码文件，入口约352行、最大 bot_commands 约558行；最终 imports 与注释
可能改变数字，验收以完整实现后实测为准。不要为复现这个数字压缩代码。

- [ ] **Step 4: 清理 imports 并验证 debug/release 无新增 warning。**

```bash
cargo fix -p bcs-cli --bin bcs-cli --allow-dirty --allow-staged \
  2>&1 | tee "$BCS_PLAN_WORKDIR/import-fix.log"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/audit_cli.py" verify
cargo test -p bcs-cli --bin bcs-cli --no-run --message-format=json \
  > "$BCS_PLAN_WORKDIR/after-test-compile.jsonl" 2> "$BCS_PLAN_WORKDIR/after-test-compile.stderr"
cargo build -p bcs-cli --bin bcs-cli --message-format=json \
  > "$BCS_PLAN_WORKDIR/after-debug.jsonl" 2> "$BCS_PLAN_WORKDIR/after-debug.stderr"
cargo build -p bcs-cli --bin bcs-cli --release --message-format=json \
  > "$BCS_PLAN_WORKDIR/after-release.jsonl" 2> "$BCS_PLAN_WORKDIR/after-release.stderr"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json,pathlib,sys
for name in ['after-test-compile.jsonl','after-debug.jsonl','after-release.jsonl']:
    rows=[json.loads(line) for line in (pathlib.Path(sys.argv[1])/name).read_text().splitlines()]
    assert not [r for r in rows if r.get('reason')=='compiler-message' and r['message']['level'] in {'warning','error'}],name
    assert any(r.get('reason')=='build-finished' and r['success'] for r in rows),name
print('PASS: debug/release/bin tests compile with zero compiler diagnostics')
PY
```

`cargo fix` 不会自动把通配导入变为明确导入；最终新模块不可保留 `use super::*`，
按实际 rustc 诊断保留 trait/macro 必需名称。仅被测试使用的根导入应加 `#[cfg(test)]`
或在 cli/tests.rs 从相应 sibling 显式导入，不因非 test 构建 unused 就删除测试依赖。若 fix 触及只读文件，停止并只撤回自己的越界修改，
不 reset 其他人的改动。release 的 ArgAction/debug cfg 必须与基线一致。

## Task 5: 发现、help、输出/鉴权与行为回归

- [ ] **Step 1: 对比实际 bin 测试发现，串行与默认并发执行。**

```bash
cargo test -p bcs-cli --bin bcs-cli -- --list > "$BCS_PLAN_WORKDIR/after-list.txt" 2> "$BCS_PLAN_WORKDIR/after-list.stderr"
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/audit_cli.py" list
cargo test -p bcs-cli --bin bcs-cli -- --test-threads=1 2>&1 | tee "$BCS_PLAN_WORKDIR/bin-serial.log"
cargo test -p bcs-cli --bin bcs-cli 2>&1 | tee "$BCS_PLAN_WORKDIR/bin-parallel.log"
cargo test -p bcs-cli --test cli_decomposition_contract 2>&1 | tee "$BCS_PLAN_WORKDIR/entry-after.log"
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import pathlib,sys
w=pathlib.Path(sys.argv[1])
for n in ['bin-serial.log','bin-parallel.log']:
    assert 'test result: ok. 115 passed; 0 failed; 0 ignored;' in (w/n).read_text(),n
assert 'test result: ok. 2 passed; 0 failed; 0 ignored;' in (w/'entry-after.log').read_text()
PY
```

- [ ] **Step 2: 每个 profile 对比完整 help，不只检查 leaf 总数。**

```bash
python3 - "$BCS_PLAN_WORKDIR" <<'PY'
import json,pathlib,subprocess,sys
w=pathlib.Path(sys.argv[1])
for profile in ['debug','release']:
    rows=[json.loads(line) for line in (w/f'after-{profile}.jsonl').read_text().splitlines()]
    binaries={r['executable'] for r in rows if r.get('executable') and r['target']['name']=='bcs-cli'}
    assert len(binaries)==1
    dest=w/f'help-after-{profile}.json'
    subprocess.run([sys.executable,str(w/'snapshot_help.py'),next(iter(binaries)),str(dest)],check=True)
    assert json.loads(dest.read_text()) == json.loads((w/f'help-before-{profile}.json').read_text()), profile
print('PASS: full help/alias/leaf identity preserved within both profiles')
PY
```

version 的格式及 package version 保留；Git hash/build date 随真实构建变化可接受，记录原/后
`.version` 文件并检查，不做宽泛文本归一化隐藏 help 或输出差异。

- [ ] **Step 3: 重建嵌入式宿主，再运行公开 mock/crate 测试。**

```bash
python3 "$BCS_PLAN_WORKDIR/build_host.py" after
python3 "$BCS_PLAN_WORKDIR/probe_host.py" "$BCS_PLAN_WORKDIR/embedding-after"
cargo test -p bcs-cli 2>&1 | tee "$BCS_PLAN_WORKDIR/crate-after.log"
cargo test -p bcs-cli --release 2>&1 | tee "$BCS_PLAN_WORKDIR/crate-release.log"
```

既有 `e2e` target 确实由 Cargo 发现，覆盖 preview/chat/discover/group/context 等 HTTP mock；
不要仅执行 `cli_tests` 的 help。`oauth_contract` 与 failure binary 的 inventory 仍各自隔离，
不把 provider 注册到公共 bcs-cli binary；caller_context/file_transfer 的隔离与重定向用例仍需通过。
旧 health/chat 的 stdout/stderr、成功/错误 code 和默认模式以原测试为准。

编译器诊断、help 比对和两项新入口测试不足以证明 WS/SSE 行为；本 crate 原 mock 主要 HTTP，
真实 WS/流式路径继续由下一任务的统一 E2E 及逐文件 changed-line coverage 验证。

## Task 6: 覆盖率风险、跨模块门禁与完整提交

- [ ] **Step 1: 先检查当前改动范围与行数。**

```bash
"$BCS_PLAN_WORKDIR/tools/bin/python" "$BCS_PLAN_WORKDIR/audit_cli.py" verify
git diff --check
python3 - <<'PY'
from pathlib import Path
files=[Path('crates/tools/bcs-cli/src/main.rs'),*Path('crates/tools/bcs-cli/src/cli').rglob('*.rs'),Path('crates/tools/bcs-cli/tests/cli_decomposition_contract.rs')]
assert len(files)==30
for path in files:
    count=len(path.read_bytes().splitlines()); assert count<=1000,(path,count)
print('PASS: 30 source files <=1000 lines')
PY
```

此单元移动生产代码，不能沿用第 1/2 单元的 changed-line N/A。本基线中 CLI 文件曾被实际覆盖
多少路径须在实施前检查 fresh E2E 工件，搬迁后大量旧分支会成为新增行。不能通过 wrapper、
测试直接调用内部函数、增加排除项或把源码放 tests 来提高覆盖数字。

- [ ] **Step 2: 运行工作区编译、架构及全工作区测试并保存真实退出码。**

```bash
cargo build --workspace --all-features 2>&1 | tee "$BCS_PLAN_WORKDIR/build-all.log"
cargo test --workspace --no-run 2>&1 | tee "$BCS_PLAN_WORKDIR/compile-default.log"
cargo test --workspace --all-features --no-run 2>&1 | tee "$BCS_PLAN_WORKDIR/compile-all.log"
set +e
bash scripts/ci/arch-check.sh > "$BCS_PLAN_WORKDIR/arch.log" 2>&1
arch_exit=$?
cargo test --workspace --no-fail-fast > "$BCS_PLAN_WORKDIR/workspace.log" 2>&1
workspace_exit=$?
set -e
printf 'architecture=%s\nworkspace=%s\n' "$arch_exit" "$workspace_exit" | tee "$BCS_PLAN_WORKDIR/status.txt"
```

逐日志核对 compiler warning；保存非零不代表通过。任何疑似基线失败在同工具链的干净
BCS_PLAN_BASE 工作区重现并比较，不凭 #3209/#3240 的旧失败记录跳过。不得改架构 baseline。

- [ ] **Step 3: 在真实 PR merge-base 上运行统一 Singlebox 并核验工件。**

Task 2 已保存 fresh 基线工件；本步骤以 `after` 保存最终结果，使用不同输出目录，
不把旧报告当本轮结果：

```bash
cd "$(git rev-parse --show-toplevel)"
git fetch origin dev
export SINGLEBOX_BASE_REF="$(git merge-base origin/dev HEAD)"
export BCS_E2E_CHANGED_BASE_REF="$SINGLEBOX_BASE_REF"
export BCS_E2E_CHANGED_LINE_MIN="$(python3 -c 'import os; print(max(60.0,float(os.environ.get("BCS_E2E_CHANGED_LINE_MIN","60"))))')"
export BCS_E2E_CHANGED_LINE_MIN_LINES="$(python3 -c 'import os; print(min(20,int(os.environ.get("BCS_E2E_CHANGED_LINE_MIN_LINES","20"))))')"
label=after
coverage_root="$BCS_PLAN_WORKDIR/coverage-$label"
bash singlebox/ci/singlebox_coverage.sh --coverage-root "$coverage_root" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/singlebox-$label.log"
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir "$coverage_root/reports" \
  2>&1 | tee "$BCS_PLAN_WORKDIR/coverage-$label-verify.log"
cd "$BCS_ROOT"
```

gate 要求：line>=41%、method>=36%、HTTP endpoint/CLI leaf=100%，适用 changed-line 严格>60%
（至少20可测变化行）；不覆盖更严配置。查看 moved CLI 每个文件的 changed-line 报告，不仅 summary。
leaf inventory 必须来自构建后的 binary 递归 help；不能以附录 snapshot 冒充 CLI 调用覆盖。
若正常/失败路径覆盖不足，只能经公开 CLI/协议入口补充有意义的 acceptance story；**本计划默认
不授权修改 E2E 脚本**，需先报告缺口并确认扩展写入边界，再更新计划和验证，不擅自豁免或扩大业务改动。

本单元应先在未改生产代码的基线跑上述 gate；基线无法运行则记录阻塞/可追踪远端结果，
不要到全部搬完才发现覆盖无法验收。端口占用采用 runner 支持的隔离配置，不杀其他 worktree 服务。

- [ ] **Step 4: 全部文件达标后提交完整单元，不提交超长中间态。**

```bash
git add -- crates/tools/bcs-cli/src/main.rs crates/tools/bcs-cli/src/cli \
  crates/tools/bcs-cli/tests/cli_decomposition_contract.rs
python3 - <<'PY'
import subprocess
files=subprocess.check_output(['git','diff','--cached','--name-only','--relative'],text=True).splitlines()
assert len(files)==30,files
assert all(p=='crates/tools/bcs-cli/src/main.rs' or p.startswith('crates/tools/bcs-cli/src/cli/')
           or p=='crates/tools/bcs-cli/tests/cli_decomposition_contract.rs' for p in files),files
PY
git diff --cached --check
git commit -m "refactor(bcs): split CLI entry by responsibility"
```

coverage 计算若采用 committed-only 范围，先生成此完整提交再执行最终 gate；最终以该提交重新
计算 merge-base、验证工件，不能拿未包含本次代码的差异报告当验收。必需门禁未通过不得报告可合并。

## Task 7: 自检与交接

- [ ] 运行附录 B verify/list，核对提交范围、30 个文件行数、protected hash 和干净工作区。
- [ ] 按 `/tmp/CODE_REVIEW.md`（存在时）和 requesting-code-review 审查：root 穷尽分发，
  bool 参数无互换，ControlFlow 不吞错误，client 生命周期，动态 log target，旧 test 属性不变。
- [ ] PR 使用 Problem/Solution/Validation/Compatibility and risk/Spec。记录 debug/release
  help、115 项映射、2 项新回归、mock/host/真实 E2E/coverage 实际结果、warning 和未验证边界。
- [ ] 单元可普通 revert，不需要 schema/config migration。等待用户发布指令，不自动 push 或建 PR。


## 附录 A：完整迁移归属清单

保存为 WORKDIR/inventory.json；Task 1 将三个键拆成工具读取的独立 JSON。

```json
{
  "helpers": {
    "args": [
      "Cli",
      "Commands"
    ],
    "session_args": [
      "SessionCommands",
      "SessionFileCommands"
    ],
    "collaboration_args": [
      "CollaborationCommands"
    ],
    "resource_args": [
      "ChatRunCommands",
      "GroupContextCommands",
      "ChannelCommands",
      "FriendCommands",
      "VisibilityCommands",
      "ServiceCommands"
    ],
    "environment": [
      "SessionInfo",
      "NetworkEnv",
      "get_current_env",
      "session_file_path",
      "get_optional_session_file_path",
      "load_session_info_from_path",
      "load_optional_session_info",
      "normalize_bcs_api_url",
      "normalize_bcs_ws_url",
      "normalize_url_from_source",
      "resolve_env_bcs_url",
      "resolve_session_bcs_url",
      "resolve_bcs_url",
      "discover_token",
      "get_token",
      "resolve_my_bot_uuid",
      "detect_network_env",
      "is_localhost_url"
    ],
    "authentication": [
      "resolve_oauth_credentials",
      "create_client"
    ],
    "output": [
      "GroupListOutput",
      "StructuredResult",
      "is_structured_mode",
      "build_log_file_path",
      "emit_structured_result",
      "render_discover_result",
      "classify_auth_error_message"
    ],
    "bot_commands": [
      "parse_skills_input"
    ],
    "collaboration_input": [
      "build_panel_opening_message",
      "parse_custom_group_bindings",
      "apply_group_participant_tags",
      "validate_custom_group_bindings",
      "emit_collaboration_validation"
    ],
    "group_context_commands": [
      "parse_group_context_policy",
      "parse_group_context_owners",
      "group_context_query",
      "group_context_insert_optional_string",
      "group_context_insert_optional_timestamp",
      "resolve_group_context_scope_levels",
      "group_context_add_body",
      "group_context_update_body"
    ],
    "channel_commands": [
      "build_channel_bind_payload",
      "redact_channel_bind_debug_payload"
    ],
    "service_commands": [
      "split_service_sid",
      "wait_for_service_completion",
      "service_session_summary_lines",
      "print_service_session_summary"
    ],
    "input": [
      "parse_json_arg"
    ]
  },
  "commands": {
    "bot_commands": [
      "Health",
      "Connect",
      "Onboard",
      "List",
      "Get",
      "Discover",
      "UpdateStatus"
    ],
    "group_commands": [
      "CreateGroup",
      "GetGroup",
      "Fuse",
      "ListGroups",
      "AddMember",
      "GroupStatus",
      "TerminateGroup"
    ],
    "chat_commands": [
      "Chat",
      "ChatRun"
    ],
    "collaboration_commands": [
      "Collaboration"
    ],
    "group_context_commands": [
      "GroupContext"
    ],
    "relation_commands": [
      "Friend",
      "Visibility"
    ],
    "channel_commands": [
      "Channel"
    ],
    "session_commands": [
      "Session"
    ],
    "service_commands": [
      "Service"
    ]
  },
  "tests": {
    "arguments": [
      "test_create_group_accepts_manager_instead_of_driver",
      "test_create_group_rejects_driver_and_manager_together",
      "test_create_group_requires_driver_or_manager",
      "test_create_group_rejects_empty_participant_tag_components",
      "test_add_member_accepts_only_group_and_bot_uuid",
      "test_add_member_rejects_role_argument",
      "test_leave_command_is_not_available",
      "test_chat_command_timeout_ms_unset_by_default",
      "test_chat_command_defaults_to_after_last_tool_call_response_mode",
      "test_discover_command_accepts_organization_scope",
      "test_discover_command_accepts_repeated_skill_filters",
      "test_discover_command_rejects_legacy_plural_skills_filter",
      "test_chat_command_accepts_organization_code",
      "test_chat_command_accepts_detach_flag",
      "test_chat_command_accepts_explicit_timeout_ms",
      "test_chat_command_accepts_repeated_tags",
      "test_chat_command_accepts_after_last_tool_call_response_mode",
      "test_chat_command_rejects_zero_timeout_ms",
      "test_chat_command_rejects_timeout_ms_over_limit"
    ],
    "environment": [
      "test_normalize_bcs_api_url_from_ws_endpoint",
      "test_normalize_bcs_api_url_keeps_http_base",
      "test_normalize_bcs_ws_url_from_http_base",
      "test_normalize_bcs_ws_url_from_https_base",
      "test_normalize_bcs_ws_url_keeps_existing_ws_endpoints",
      "test_token_discovery_priority_cli_first",
      "test_token_discovery_from_env_var",
      "test_token_discovery_from_session_file",
      "test_token_cli_overrides_env",
      "test_token_env_overrides_file",
      "test_token_not_found_returns_empty",
      "test_resolve_bcs_url_from_session_file",
      "test_resolve_bcs_url_uses_distribution_default_or_local",
      "test_resolve_bcs_url_uses_pre_distribution_default_or_local",
      "test_detect_network_env_localhost",
      "test_detect_network_env_127",
      "test_is_localhost_url"
    ],
    "group_context": [
      "group_context_add_parses_valid_range_flags",
      "group_context_add_rejects_change_reason_flag",
      "group_context_update_requires_change_reason_flag",
      "group_context_list_parses_plural_scope_levels_flag",
      "group_context_query_omits_missing_and_empty_group_id",
      "group_context_query_includes_non_empty_group_id",
      "group_context_commands_accept_non_group_scope_without_group_id",
      "group_context_scope_level_flags_are_mutually_exclusive",
      "group_context_add_body_includes_valid_fields_only_when_provided",
      "group_context_add_cli_accepts_missing_collect_from",
      "group_context_update_body_omits_unprovided_patch_fields",
      "group_context_update_body_includes_owners_when_provided",
      "group_context_add_rejects_owners_flag",
      "group_context_update_parses_owners_flag",
      "group_context_update_rejects_invalid_owners_shapes",
      "group_context_insert_optional_timestamp_converts_numeric_strings",
      "group_context_add_body_sends_timestamps_as_numbers"
    ],
    "collaboration": [
      "collaboration_validate_command_parses_yaml_path",
      "collaborate_permission_alias_parses_current_session",
      "collaborate_run_alias_parses_yaml_session_bindings_and_input",
      "collaborate_run_parses_panel_arguments",
      "collaborate_run_requires_panel_component_for_dependent_arguments",
      "panel_opening_message_requires_object_params",
      "collaboration_create_command_parses_repeated_bindings",
      "custom_group_binding_parser_preserves_bot_uuid_colons",
      "custom_group_binding_validation_requires_assigned_slots_and_driver"
    ],
    "channel": [
      "test_channel_bind_command_parses_defaults",
      "test_channel_bind_payload_builds_group_dingtalk_normal",
      "test_channel_bind_payload_builds_bot_streaming_card",
      "test_channel_bind_payload_requires_card_template_for_streaming_card",
      "test_channel_bind_debug_payload_redacts_client_secret",
      "test_channel_list_command_parses",
      "test_channel_conversation_id_command_parses_session",
      "test_channel_unbind_command_parses_id"
    ],
    "session": [
      "test_session_create_parses_minimal_args",
      "test_session_create_accepts_group_context_delivery",
      "test_session_create_rejects_invalid_group_context_delivery",
      "test_session_list_accepts_filters",
      "test_session_get_requires_sid",
      "test_session_chat_round_trips",
      "test_session_messages_accepts_view_bot_and_limit",
      "test_session_patch_parses_args",
      "test_session_complete_parses_args",
      "test_session_complete_output_parse_json_arg",
      "test_session_add_member_parses_args",
      "test_session_remove_member_parses_args",
      "test_session_set_member_mode_parses_args",
      "test_session_invite_link_parses_args"
    ],
    "service": [
      "test_service_invoke_parses_minimal_args",
      "test_service_command_accepts_bot_token",
      "test_service_invoke_parses_full_args",
      "test_service_invoke_rejects_zero_timeout_ms",
      "test_service_status_parses_positional_sid",
      "test_service_status_accepts_group_override",
      "test_service_wait_parses_positional_sid_and_timeout",
      "test_service_session_summary_lines_include_state_machine_run",
      "test_split_service_sid_recovers_group_from_colon",
      "test_split_service_sid_explicit_group_wins",
      "test_split_service_sid_rejects_unparseable_sid_without_group",
      "test_parse_json_arg_supports_literal",
      "test_parse_json_arg_reads_file_with_at_prefix",
      "test_parse_json_arg_rejects_bare_at_sign"
    ],
    "output": [
      "test_structured_result_ok_serialization",
      "test_structured_result_serialization",
      "test_classify_auth_error_message_timeout",
      "test_classify_auth_error_message_expired",
      "test_classify_auth_error_message_request_failed"
    ]
  }
}
```

## 附录 B：声明、分支与测试保持性核对工具

保存为 `$BCS_PLAN_WORKDIR/audit_cli.py`，不提交。

```python
import argparse, hashlib, json, os, re
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_rust

WORK = Path(os.environ['BCS_PLAN_WORKDIR'])
ROOT = Path('crates/tools/bcs-cli/src/main.rs')
HASH = 'ea5f0c2e6a7563f4be30741e8b0887d9a7b8b93a37e2b7a69ab7ef354e7de363'
LANGUAGE = Language(tree_sitter_rust.language())
PARSER = Parser(LANGUAGE)
TREES = []


def nodes(data):
    tree = PARSER.parse(data)
    assert not tree.root_node.has_error, 'Rust parse error'
    TREES.append(tree)
    return tree.root_node


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def named(node):
    field = node.child_by_field_name('name')
    return field.text.decode() if field else ''


def text(node, attrs=True):
    prefix = []
    previous = node.prev_named_sibling
    while attrs and previous and previous.type in {'attribute_item', 'line_comment', 'block_comment'}:
        prefix.insert(0, previous.text)
        previous = previous.prev_named_sibling
    return b'\n'.join([*prefix, node.text])


def normalize(data, visibility=False, tracing=False, cli_json=False):
    if visibility:
        data = data.replace(b'pub(super) ', b'')
    if tracing:
        data = data.replace(b'target: CLI_LOG_TARGET,', b'')
    if cli_json:
        data = data.replace(b'cli.json', b'json')
    # Lexical tokens preserve strings, comments, attributes and macro contents.
    root = nodes(data)
    def leaves(node):
        if not node.children:
            return [(node.type, node.text)]
        return [token for child in node.children for token in leaves(child)]
    return leaves(root)


def source():
    data = (WORK / 'original.rs').read_bytes()
    assert hashlib.sha256(data).hexdigest() == HASH, 'Source drift: re-plan'
    return nodes(data)


def fn_node(root, name):
    return next(n for n in root.named_children if n.type == 'function_item' and named(n) == name)


def command_arms(root):
    result = {}
    for node in walk(root):
        if node.type != 'match_arm':
            continue
        pattern = node.child_by_field_name('pattern').text
        matched = re.match(rb'Commands::(\w+)', pattern)
        if matched:
            name = matched[1].decode()
            assert name not in result, ('duplicate command arm', name)
            result[name] = node
    return result


def test_functions(root):
    module = next(n for n in root.named_children if n.type == 'mod_item' and named(n) == 'tests')
    return {named(n): text(n) for n in module.child_by_field_name('body').named_children
            if n.type == 'function_item'}


def verify():
    assert len(ROOT.read_bytes().splitlines()) <= 1000, 'Root exceeds 1000 physical lines'
    before = source()
    after = nodes(ROOT.read_bytes())
    helpers = json.loads((WORK / 'helpers.json').read_text())
    tests = json.loads((WORK / 'tests.json').read_text())
    families = json.loads((WORK / 'commands.json').read_text())
    original_items = {named(n): text(n) for n in before.named_children
                      if n.type in {'function_item', 'struct_item', 'enum_item'}}
    run = fn_node(before, 'run')
    client = next(n for n in walk(run) if n.type == 'function_item' and named(n) == 'create_client')
    original_items['create_client'] = text(client)
    for module, names in helpers.items():
        moved = nodes((ROOT.parent / 'cli' / f'{module}.rs').read_bytes())
        actual = {named(n): text(n) for n in moved.named_children
                  if n.type in {'function_item', 'struct_item', 'enum_item'}}
        for name in names:
            assert name in actual, (module, name)
            assert normalize(original_items[name]) == normalize(actual[name], visibility=True, tracing=True), name
    old_tests = test_functions(before)
    mapped = {name for names in tests.values() for name in names}
    assert len(mapped) == 103
    assert set(old_tests) == mapped | {'safe_set_var', 'safe_remove_var', 'write_session_file'}
    for module, names in tests.items():
        new = nodes((ROOT.parent / 'cli/tests' / f'{module}.rs').read_bytes())
        actual = {named(n): text(n) for n in new.named_children if n.type == 'function_item'}
        assert set(actual) == set(names), (module, set(actual) ^ set(names))
        for name in names:
            assert normalize(old_tests[name]) == normalize(actual[name]), name
    support = nodes((ROOT.parent / 'cli/tests.rs').read_bytes())
    for name in ['safe_set_var', 'safe_remove_var', 'write_session_file']:
        assert normalize(old_tests[name]) == normalize(text(fn_node(support, name)), visibility=True), name
    old_arms = command_arms(run)
    assert len(old_arms) == 23
    moved_names = []
    for family, names in families.items():
        new = nodes((ROOT.parent / 'cli' / f'{family}.rs').read_bytes())
        arms = command_arms(new)
        assert set(arms) == set(names), (family, set(arms) ^ set(names))
        for name in names:
            old = old_arms[name].text
            if name == 'Session':
                file_arm = next(n for n in walk(old_arms[name]) if n.type == 'match_arm'
                                and n.child_by_field_name('pattern').text.startswith(b'SessionCommands::File'))
                value = file_arm.child_by_field_name('value')
                file_module = nodes((ROOT.parent / 'cli/session_file_commands.rs').read_bytes())
                file_match = next(n for n in walk(fn_node(file_module, 'execute')) if n.type == 'match_expression')
                assert normalize(value.text, cli_json=True) == normalize(file_match.text), 'File match changed'
                a, b = value.start_byte - old_arms[name].start_byte, value.end_byte - old_arms[name].start_byte
                old = old[:a] + b'session_file_commands::execute(command, &client, json, debug).await?' + old[b:]
            # Parse arms in a complete match expression, not as invalid top-level Rust.
            wrap = lambda body: b'fn check() { match command { ' + body + b' } }'
            assert normalize(wrap(old), cli_json=True) == normalize(wrap(arms[name].text), tracing=True), name
        moved_names += list(arms)
    assert len(moved_names) == len(set(moved_names)) == 23
    # Keep existing mount points, macros, constants and retired-policy module byte-identical.
    kinds = {'macro_definition', 'const_item', 'mod_item'}
    fixed = [n for n in before.named_children if n.type in kinds and named(n) != 'tests']
    new_fixed = {named(n): text(n) for n in after.named_children if n.type in kinds}
    for node in fixed:
        assert new_fixed[named(node)] == text(node), ('root identity', named(node))
    assert normalize(text(fn_node(before, 'main'))) == normalize(text(fn_node(after, 'main')))
    # Only the owned preparation expression moves; its two handled failures become Break.
    old_auth = next(n for n in walk(run) if n.type == 'let_declaration'
                    and n.child_by_field_name('pattern').text == b'(_network_env, oauth_headers)')
    old_expr = old_auth.child_by_field_name('value').text
    assert old_expr.count(b'return Ok(());') == 2
    expected_expr = old_expr.replace(b'return Ok(());', b'return Ok(ControlFlow::Break(()));')
    auth = nodes((ROOT.parent / 'cli/authentication.rs').read_bytes())
    prepare = fn_node(auth, 'prepare_authentication')
    value = next(n for n in prepare.child_by_field_name('body').named_children if n.type == 'let_declaration')
    expr = value.child_by_field_name('value')
    wrap = lambda body: b'fn check() { let x = ' + body + b'; }'
    assert normalize(wrap(expected_expr)) == normalize(wrap(expr.text), tracing=True), 'authentication expression changed'
    # Initial parse/preview/log/caller/config steps stay in run(), in the same order.
    old_body = run.child_by_field_name('body')
    new_run = fn_node(after, 'run')
    new_auth = next(n for n in walk(new_run) if n.type == 'let_declaration'
                   and n.child_by_field_name('pattern').text == b'(_network_env, oauth_headers)')
    old_prefix = run.text[old_body.start_byte-run.start_byte+1:old_auth.start_byte-run.start_byte]
    new_body = new_run.child_by_field_name('body')
    new_prefix = new_run.text[new_body.start_byte-new_run.start_byte+1:new_auth.start_byte-new_run.start_byte]
    assert normalize(b'async fn check(){'+old_prefix+b'}') == normalize(b'async fn check(){'+new_prefix+b'}'), 'initialization order changed'
    protected = json.loads((WORK / 'protected.json').read_text())
    for path, digest in protected.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
    changed = [ROOT, *sorted((ROOT.parent / 'cli').rglob('*.rs'))]
    assert len(changed) == 29, len(changed)
    assert all(len(p.read_bytes().splitlines()) <= 1000 for p in changed)
    print('PASS: 103 tests, 23 command arms, helpers/types/auth expression, unchanged sibling files; 29 source files <=1000')


def lists():
    mapping = {f'tests::{name}': f'tests::{group}::{name}'
               for group, names in json.loads((WORK / 'tests.json').read_text()).items() for name in names}
    def names(file):
        return re.findall(r'^(.+): test$', (WORK / file).read_text(), re.M)
    before, after = names('baseline-list.txt'), names('after-list.txt')
    assert len(before) == len(set(before)) == 115
    assert len(after) == len(set(after)) == 115
    assert set(after) == {mapping.get(name, name) for name in before}
    print('PASS: bin tests 115-to-115; 103 mapped and 12 unchanged')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['verify', 'list'])
    mode = parser.parse_args().mode
    verify() if mode == 'verify' else lists()
```

## 附录 C：新增的 binary 入口回归测试

写入 `crates/tools/bcs-cli/tests/cli_decomposition_contract.rs`。

```rust
//! Binary-entry ordering regressions for the CLI decomposition.
use assert_cmd::Command;
use std::net::TcpListener;
use std::path::Path;
use std::time::Duration;

fn isolated(home: &Path) -> Command {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.env_clear()
        .env("HOME", home)
        .env("NO_COLOR", "1");
    cmd.timeout(Duration::from_secs(5));
    cmd
}

fn assert_no_request(listener: &TcpListener) {
    match listener.accept() {
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
        other => panic!("unexpected API/probe connection: {other:?}"),
    }
}

#[test]
fn preview_skips_caller_validation_auth_and_log_creation() {
    let home = tempfile::tempdir().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let yaml = home.path().join("definition.yaml");
    std::fs::write(&yaml, "name: 离线预览\n").unwrap();
    let bot = home.path().join("bot");
    std::fs::create_dir_all(bot.join(".bcs")).unwrap();
    std::fs::write(bot.join(".bcs/session.json"), "not JSON").unwrap();
    let output = isolated(home.path())
        .env("BOT_DATA_DIR", &bot)
        .env("BCN_ACTOR_BOT_ID", " ")
        .env("BCN_AUTH_MODE", "agentpass-subbots")
        .env("tc_sdb_nenv", "office")
        .args(["--url", &url, "--json", "collaboration", "preview"])
        .arg(yaml)
        .args(["--document-id", "11111111-1111-4111-8111-111111111111",
               "--snapshot-id", "22222222-2222-4222-8222-222222222222"])
        .assert().success().get_output().clone();
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["snapshot"]["definitionYaml"], "name: 离线预览\n");
    assert!(output.stderr.is_empty());
    assert!(!home.path().join(".bcs/logs").exists());
    assert_no_request(&listener);
}

#[test]
fn handled_office_auth_failure_does_not_dispatch_health() {
    for mode in [None, Some("--json"), Some("--no-json")] {
        let home = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let mut cmd = isolated(home.path());
        cmd.env("tc_sdb_nenv", "office").args(["--url", &url]);
        if let Some(flag) = mode { cmd.arg(flag); }
        cmd.arg("health");
        let assertion = if mode == Some("--no-json") {
            cmd.assert().failure().code(1)
        } else {
            cmd.assert().success()
        };
        let output = assertion.get_output();
        if mode == Some("--no-json") {
            assert!(output.stdout.is_empty());
            assert!(String::from_utf8_lossy(&output.stderr).contains("not available in the public build"));
        } else {
            let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
            assert_eq!(value["status"], "auth_failed");
            assert!(value["message"].as_str().unwrap().contains("not available in the public build"));
            assert!(output.stderr.is_empty());
        }
        assert_no_request(&listener);
    }
}
```

## 附录 D：canonical help/leaf 快照工具

保存为 `$BCS_PLAN_WORKDIR/snapshot_help.py`，不提交。

```python
import importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path

binary, output = sys.argv[1:]
binary = str(Path(binary).resolve())
spec = importlib.util.spec_from_file_location('cli_coverage', 'scripts/cli_command_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)
# Same discovery code as canonical E2E; keep the algorithm read-only.
with tempfile.TemporaryDirectory(prefix='bcs-cli-help-') as home:
    remove = [k for k in os.environ if k.startswith(('BCS_', 'BCN_', 'MOLTIS_', 'OAUTH_'))
              or k in {'BOT_DATA_DIR', 'AGENTCLAW_ENV', 'SERVER_ENV', 'env', 'tc_sdb_nenv', 'FORCE_COLOR', 'CLICOLOR_FORCE'}]
    for k in remove:
        os.environ.pop(k, None)
    os.environ.update(HOME=home, NO_COLOR='1', COLUMNS='100', TERM='dumb')
    leaves = coverage.leaf_commands(binary)
    assert leaves and len(leaves) == len(set(leaves))
    prefixes = {()}
    for leaf in leaves:
        words = tuple(leaf.split())
        prefixes.update(words[:i] for i in range(1, len(words) + 1))
    records = {}
    for prefix in sorted(prefixes):
        r = subprocess.run([binary, *prefix, '--help'], text=True, capture_output=True, timeout=10)
        assert r.returncode == 0, (prefix, r.stderr)
        records[' '.join(prefix)] = [r.returncode, r.stdout, r.stderr]
    for alias in ['collaborate', 'collab']:
        r = subprocess.run([binary, alias, '--help'], text=True, capture_output=True, timeout=10)
        # Capture even rejected aliases; don't introduce aliases during decomposition.
        records['alias:' + alias] = [r.returncode, r.stdout, r.stderr]
    version = subprocess.run([binary, '--version'], text=True, capture_output=True, check=True).stdout
    Path(output).write_text(json.dumps({'leaves': leaves, 'help': records}, indent=2, ensure_ascii=False) + '\n')
    Path(output + '.version').write_text(version)
    print(f'PASS: {len(leaves)} canonical leaves; {len(records)} help/alias snapshots')
```

## 附录 E：独立嵌入宿主构建工具

保存为 `$BCS_PLAN_WORKDIR/build_host.py`，不提交。

```python
import json, os, shutil, subprocess, sys, tomllib
from pathlib import Path

work = Path(os.environ['BCS_PLAN_WORKDIR'])
root = Path(os.environ['BCS_ROOT']).resolve()
label = sys.argv[1]
crate = root / 'crates/tools/bcs-cli'
package = tomllib.loads((crate / 'Cargo.toml').read_text())
workspace = tomllib.loads((root / 'Cargo.toml').read_text())['workspace']
metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--no-deps', '--format-version=1'], text=True))
version = next(p['version'] for p in metadata['packages'] if p['name'] == 'bcs-cli')
host = work / ('embedding-' + label)
host.mkdir(exist_ok=False)
deps = {}
for name, settings in package['dependencies'].items():
    if isinstance(settings, dict) and settings.get('workspace'):
        base = workspace['dependencies'][name]
        merged = dict(base) if isinstance(base, dict) else {'version': base}
        merged['features'] = list(dict.fromkeys(merged.get('features', []) + settings.get('features', [])))
        merged.update({k: v for k, v in settings.items() if k not in {'workspace', 'features'}})
        if 'path' in merged: merged['path'] = str((root / merged['path']).resolve())
        deps[name] = merged
    else:
        deps[name] = settings
        assert not isinstance(settings, dict) or 'path' not in settings, 'resolve crate-local path explicitly'
deps['bcs-cli'] = {'path': str(crate)}
def toml(value):
    if isinstance(value, dict): return '{' + ', '.join(json.dumps(k) + '=' + toml(v) for k,v in value.items()) + '}'
    if isinstance(value, list): return '[' + ', '.join(toml(v) for v in value) + ']'
    return json.dumps(value)
manifest = '[workspace]\n[package]\nname="bcs-cli-embedding-smoke"\nversion=' + toml(version) + '\nedition="2024"\n[dependencies]\n'
manifest += '\n'.join(toml(k) + '=' + toml(v) for k,v in deps.items()) + '\n'
(host / 'Cargo.toml').write_text(manifest)
lock = work / 'embedding-before/Cargo.lock' if label == 'after' else root / 'Cargo.lock'
assert lock.is_file(), lock
shutil.copyfile(lock, host / 'Cargo.lock')
(host / 'src').mkdir()
source = '''use std::collections::HashMap;
use bcs_cli::oauth::{AuthRequiredCallback, OAuthError, OAuthHeaderProvider, OAuthHeaderProviderRegistration};
struct FakeProvider;
#[async_trait::async_trait]
impl OAuthHeaderProvider for FakeProvider {
    async fn get_headers(&self, _: String, _: String, callback: Option<AuthRequiredCallback>)
        -> Result<HashMap<String, String>, OAuthError> {
        if std::env::var("SMOKE_OAUTH_FAIL").is_ok() {
            if let Some(callback) = callback { callback("https://idp.example.test/authorize"); }
            return Err(OAuthError { message: "OAuth timed out locally from smoke provider".into(),
                auth_url: Some("https://idp.example.test/authorize".into()) });
        }
        Ok(HashMap::from([("X-Smoke-OAuth".into(), "fixture-value".into())]))
    }
}
static PROVIDER: FakeProvider = FakeProvider;
inventory::submit! { OAuthHeaderProviderRegistration { provider: &PROVIDER } }
#[path = "SOURCE_PATH"]
mod embedded;
fn main() -> anyhow::Result<()> {
    tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(embedded::run())
}
'''
(host / 'src/main.rs').write_text(source.replace('SOURCE_PATH', str(crate / 'src/main.rs')))
env = os.environ | {'GIT_COMMIT_HASH': subprocess.check_output(['git','rev-parse','--short','HEAD'], text=True).strip(),
                    'BUILD_DATE': '2000-01-01'}
for profile in ['debug', 'release']:
    command = ['cargo','build','--manifest-path',str(host / 'Cargo.toml'), '--message-format=json']
    if profile == 'release': command.append('--release')
    # Use Cargo to resolve exact target/build features, never choose arbitrary same-name rlibs.
    with (host / f'{profile}.jsonl').open('w') as out, (host / f'{profile}.stderr').open('w') as err:
        subprocess.run(command, env=env, stdout=out, stderr=err, check=True)
    rows = [json.loads(line) for line in (host / f'{profile}.jsonl').read_text().splitlines()]
    executable = next(r['executable'] for r in rows if r.get('executable') and r['target']['name']=='bcs-cli-embedding-smoke')
    (host / f'{profile}.binary').write_text(executable)
print(host)
```

## 附录 F：嵌入宿主的鉴权与输出 probe

保存为 `$BCS_PLAN_WORKDIR/probe_host.py`，不提交。

```python
import http.server, json, os, subprocess, sys, tempfile, threading
from pathlib import Path

host = Path(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
    requests = []
    def do_GET(self):
        self.requests.append((self.path, dict(self.headers)))
        body = b'{"status":"ok"}'
        self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
url = f'http://127.0.0.1:{server.server_port}'
try:
    for profile in ['debug','release']:
        binary = (host / f'{profile}.binary').read_text().strip()
        for fail in [False, True]:
            for flag in [None, '--json', '--no-json']:
                with tempfile.TemporaryDirectory(prefix='bcs-embedding-home-') as home:
                    env={'HOME':home,'NO_COLOR':'1','tc_sdb_nenv':'office'}
                    if fail:env['SMOKE_OAUTH_FAIL']='1'
                    before=len(Handler.requests)
                    command=[binary,'--url',url]+([flag] if flag else [])+['health']
                    r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
                    label=f'{profile}-{fail}-{flag or "default"}'
                    (host / f'{label}.json').write_text(json.dumps({'code':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2))
                    assert r.returncode == (1 if fail and flag=='--no-json' else 0), (label,r)
                    if fail:
                        assert len(Handler.requests)==before, (label,'request after auth failure')
                        if flag=='--no-json':
                            assert not r.stdout and 'timed out locally' in r.stderr
                        else:
                            values=[json.loads(line) for line in r.stdout.splitlines()]
                            assert [v['status'] for v in values]==['auth_required','auth_timeout'],values
                            assert values[-1]['auth_url']=='https://idp.example.test/authorize'
                    else:
                        assert len(Handler.requests)==before+1
                        path,headers=Handler.requests[-1]
                        assert path=='/health' and {k.lower():v for k,v in headers.items()}['x-smoke-oauth']=='fixture-value'
                        if flag=='--no-json':assert 'healthy at' in r.stdout
                        else:assert json.loads(r.stdout)['status']=='healthy'
    print('PASS: embedded entry debug/release, linked OAuth success/failure, default/JSON/human contracts')
finally:
    server.shutdown();server.server_close();thread.join()
```

## 计划自检与验证边界

| 设计目标 | 本计划落实 |
| --- | --- |
| 入口/公开契约不变 | run 签名、main、常量、原 sibling 保留；独立 embedding host |
| 职责与文件大小 | 19 个 production 子模块 +8 test 场景；完整30文件范围检查 |
| 初始化/凭据顺序 | 原 run 前缀 token 核对、ControlFlow、原 client 工厂时刻、两项新回归 |
| 分支/测试不遗漏 | 23 arm、103项归属、115-to-115、12项身份保留 |
| CLI 可见行为 | debug/release help、alias、默认输出模式、错误码、mock/host 调用 |
| 覆盖率不规避 | 生产搬迁前 fresh 基线，最终真实 merge-base unified E2E 和逐文件报告 |
| 失败诚实披露 | 未验证真实内部 SDK，所有非零门禁与未运行项需追踪，不新增默认 waiver |

本轮做了源码/AST 清单核对，原 bin 实际发现115项，公开 debug binary 编译通过。
在仓库外临时草稿中按本计划边界组织29个源码文件，结构核对及 debug 编译通过；
其初始 imports 尚未完成最终清理，因此不是零 warning 验收。debug 草稿与基线的
66个 canonical leaf、79份 help/alias快照一致；public Office默认/JSON/human失败终止和
离线preview的隔离检查通过。附录C的原文已在仓库外编译成临时测试binary，分别针对
基线与草稿binary执行，两轮均2 passed；工作区尚未新增该Rust测试文件。文档30个
fenced code block中的Bash/Python/JSON语法检查通过，附录C也经过上述Rust编译。

未在工作区修改 Rust 文件；未运行正式 release 构建、完整 bin/crate 测试、临时Cargo宿主
完整debug/release矩阵、架构、workspace或Singlebox。附录B是检查器而非自动重写器；
临时草稿只用于验证提取边界，不替代人工分发审核及正式实现的完整验收。

实施方式待后续指令：Inline Execution，或经用户明确授权后使用Subagent-Driven。
不得仅因计划包含多个模块就并发修改同一入口或执行多个搬运脚本。
