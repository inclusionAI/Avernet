# Reader-first 实施记录（2026-11-25）

状态：**部分实施，拟提交 Draft PR；不是完整系统调用授权功能，不可启用生产 writer**。用户已在完成 spec 后授权实施修改和创建 PR。原 spec-only 记录保留作历史证据，当前阶段以此页为准。

## Git 基线与范围

- dedicated worktree/branch 继续使用 `Avernet/.worktree/system-agent-invoke-authorization-spec` / `codex/system-agent-invoke-authorization-spec`。
- 实施前 fetch 后 `origin/dev=4571a50c944ad02f91e0d002b22b73bbdfc2ec80`，已在本 worktree fast-forward 到此版本；与原 spec 基线的差异只在无关 BCSFuse Python 文件，没有覆盖已有规范或主 checkout WIP。
- 不改 migration、Cargo 依赖/构建配置，不新增公共 HTTP/SDK，不编辑 OCB。历史四类 grant 的字符串及 owner/manager 存储/查询规则不变。
- 本轮缺少完整生产资格/绑定 generation 的提供者与域隔离证明，不能把“系统标签”或 ref=0 当可信授权。按设计的 reader-first 顺序先实现可安全独立验证的兼容阶段，剩余任务不勾选。

## 实际实现

1. `GrantKind::SystemInvoke` / `RequestKind::SystemInvoke`，serde 与 DB mapping 均为 `system_invoke`，未知值仍失败；新纯 shape validator 检查 Bot-shaped 的非空不同 endpoints、ref=0、rules=None、SameAsFrom、无 policy data。
2. DB/Memory `list_active_grants` 与 `is_authorized` 加入新候选，始终排除 owner/manager；内部 repository insert 仅接受 canonical 新形状。**它们是受信存储操作，不是新的公开 grant/create/approve API**。
3. 新增 `list_runtime_grants_checked`，default 明确 unavailable，不能把 vec/bool 的读错误伪装成空。真实 SQL/Memory 实现；SQL 一次 pair/env 读取、二进制身份 residual、精确排除 authority，未知 kind、坏 JSON/shape/query error 整体失败，不返回部分候选。SystemInvoke 的 SQL rules/policy data 必须真 NULL，不接受 `''` 或 JSON `null`。
4. 一般 admission 明确只消费 profile/rules，原 bool candidate 不再变成默认 profile 放行；旧 AuthzContext 排除 SystemInvoke。独立已有 Rules/PublicDefault 行为保留，不伪称 system grant 命中。
5. friend `get/approve/reject/cancel/list` 限定 Connect，kind guard 位于幂等/approved writeback 之前；system request 不能变成好友或由旧路由修改。原 friend revoke 的 kind+default-ref 过滤保留，回归证明它不会撤销 system edge。
6. 公共共享 repo harness 在 SQLite/Memory 上验证方向、环境/身份、friend 排除、shape、kind/ref 共存和精确撤销；新增显式 ignored 的真实 MySQL driver，要求空 disposable DB + 全 migration chain。

## 本地实际验证

测试使用 Rustup stable 的明确 RUSTC/RUSTDOC 路径、`CARGO_INCREMENTAL=0`、jobs=2、dev/test debug=0。首次只运行 `rustup run stable cargo` 仍误取 PATH 中损坏的 Homebrew rustc（LLVM/dyld），修正编译器路径后执行下列测试。未改仓库 toolchain/config。

| 命令 / suite | 结果 |
| --- | --- |
| `cargo test -p bcs-domain --lib` | 96/96 passed |
| `cargo test -p bcs-service-api --lib` | 76/76 passed |
| `cargo test -p bcs-test-support --lib` | 17/17 passed |
| `cargo test -p bcs-edge-permission --lib` | 114/114 passed |
| `cargo test -p bcs-edge-permission-store --lib` | 58/58 passed |
| store `--test conformance_edge_grant` | 2/2 passed（原 profile/friend + 新 system harness） |
| store `--test conformance_memory` | 4/4 passed（含新 system harness） |
| store `--test conformance_system_invoke_mysql` | 0 executed / 1 ignored，未配置 BCS_TEST_MYSQL_URL，Docker daemon 不可用 |

以上不重复计首次定向运行，共 **367 passed / 0 failed；1 个 MySQL gate ignored，不算通过**。新功能回归覆盖 structural enums/shape、query/partial-row error、case-insensitive 表上的 byte-exact residual、system-only generic denial、独立 Rules/PublicDefault、pending/approved request 隔离、unfriend 不误撤销。

- `check-store-boundaries.sh`、`check-port-purity.sh`、`check-interceptor-chain.sh`、`check-protocol-compat.sh`：退出码均 0。
- 完整 `arch-check.sh` **未通过/未完成**：出现现有全域 dependency/import/trait naming 检查失败，CFG 运行受环境影响，后续脚本输出 `No space left on device`；已停止该次全量检查。未完成所有失败的 baseline 归因，不宣称“均与本 PR 无关”，不削弱检查或修改 allowlist。
- 本机磁盘余量从约 22 GiB 降至约 2–3 GiB；本 worktree 新建 target 约 1 GiB。没有删除用户其他 worktree、缓存或 WIP；未启动全栈/覆盖率门禁。
- `git diff --check`、逐修改 Rust 文件 <=1000 行、OpenSpec strict validation 与链接检查列为提交前检查。为避免扩大已有超长 `contract/repo/mod.rs`，新 harness 从短 `edge_grant.rs` 子模块导出，原超长文件无修改。
- Git hook 安装路径已按当前仓库核实为 `devops/hook/install_git_hooks.sh`（AGENTS 中旧 `scripts/...` 路径不存在），使用现有 installer/hook，不跳过 secret/pre-push 检查。

## 成本与兼容

- generic admission 仍一次候选 pair 读取（之前 bool 内部也是同一 read），只在内存筛 kind，无新增外部调用/profile 查询或 cache。
- checked read 一次 indexed pair/env SQL，rows 为该 pair 已批准 runtime candidates；不宣称这是有界治理分页，不做全图扫描/N+1 或逐边资格查询。复杂身份 residual 的 MySQL EXPLAIN 尚未实测。
- 新 kind 对旧 exhaustive Rust/DB/SDK readers 不是无感兼容。必须 reader-first 升级宿主，生产新 writer 仍不开放。下游自定义 repo 默认 checked read unavailable，需显式实现，不能默默降级为旧 infallible read。

## 尚未实现 / 未验证

- 新 management/application 生命周期及 HTTP contracts；审批/撤销事务、audits、idempotency receipts、approval generations 与失效持久化。
- production trusted inventory / tenant namespace 隔离、统一 operation-aware invocation hook、runtime grant generation 投影和真实 A2A 放行。
- 治理查询/degree/限额、索引迁移、真实 MySQL/OceanBase、HTTP router 回归与 singlebox E2E/coverage。
- 远端 CI/host integration/部署/live proof 均不能从上述本地库测试推断。PR 必须保持 Draft，后续实施需先明确资格代次和域隔离 contract，不能通过 raw repo upsert 绕过前置条件。

整体任务目前 **4/27 完成**。创建 PR 不等于完整功能已经交付、merge-ready 或已部署。
