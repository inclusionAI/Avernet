# 2026-11-25 Stage 1/2 审查修复与复核

基线：`564a2b7e309bd512380fb874f9d469391691b9bc`。本记录对应其上的未提交修复，不是部署验收。未修改编号迁移、未清理共享缓存、未 commit/push。

## 修复及回归

| 问题 | 修复与证据 |
| --- | --- |
| 同 messageId 跨 Sid 重复受理 | 请求行 CAS 只恢复原完整请求；Inbox 冲突不授予许可。`message_cannot_install_in_two_contexts` RED→GREEN，并增加 prepared 窗口双 context 竞争覆盖。 |
| 严格来源阻断 Connector | 源隔离排除经过既有校验的 Connector 入口；`strict_round_accepts_authenticated_connector_terminal` RED→GREEN，旧 RPC 负测保持通过。 |
| did 不兼容 | 稳定 did 改为 `<tid>.fsm:...`；前缀回归 RED→GREEN，原重试身份断言同步。 |
| final 节点 Canceling 后不完成 | late Final 输出一次 Completed；`final_node_final_after_timeout_finishes_once` RED→GREEN。 |
| 归档冒充终态授权 | 冷转存验证独立 checkpoint terminal authority、Done/result_ref、closure 身份和摘要；归档后未提交拒绝测试 RED→GREEN。集中 archive harness 明确先拒绝、再提交权威、再确认；历史成功夹具不再使用虚构 closure。 |
| attempt 效果先于意图 | checkpoint 先登记所有潜在在途 attempt，再执行效果；第二个 sink 冲突后不能终态的反例 RED→GREEN。 |
| 目录回收遗漏并发 Accepted | checkpoint CAS 先永久 retire Sid，再删目录；检查后暂停并发受理的反例 RED→GREEN。retired Sid 不复用。 |
| 冷压缩后 Feed 前驱丢失 | 独立保留 feed_tail；A 压缩→B 完成→依序 append A/B 回归 RED→GREEN。 |
| MySQL 迁移版本断言过时 | 两项实际失败测试的 50/30 改为 53/33；不修改迁移文件。 |
| API CI 缺 nightly | 工作流安装 nightly-2025-11-30 和 cargo-public-api 0.52.0；CI 缺依赖返回失败而非跳过。 |
| linked worktree 泄漏 | `.git` 使用存在性而非目录检测；清理回归 RED→GREEN。 |
| 旧 wire 回归漏跑 | API 脚本同时执行 group_round_wire 和 persistence_shape。 |

## 实际命令与结果

Rust 命令在仓库根执行，共同环境：`CARGO_TARGET_DIR=/dev/shm/bcs-review-target CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 CARGO_INCREMENTAL=0`，`umask 077`，编译 `-j1`，测试 `--test-threads=1`。

- `cargo test --manifest-path apps/bcs/Cargo.toml -j1 -p bcs-dist-runner-fsm -p bcs-dist-store -p bcs-dist-worker -p bcs-dist-model -p bcs-service-api -p bcs-test-support -p bcs-db-migrations -p bcs-admin --no-fail-fast -- --test-threads=1`：**1078 passed、0 failed、6 ignored，138 target，exit 0**。日志 `.superpowers/sdd/plan/review-affected-final.log`。首次完整执行还暴露第二个迁移版本断言失败，修正后完整重跑，未隐去该失败。
- 6 ignored：bcs-admin 的 5 项真实 MySQL 环境测试，及 bcs-test-support 的既有 cache_plugin_contract_tests doctest；不是本轮跳过失败用例。
- `python3 apps/bcs/scripts/ci/test_public_api_gate.py`：4 passed。验证 CI 依赖声明、必需性、worktree 清理和旧格式测试入口，不等价于真正 API 清单比较。
- `bash -n apps/bcs/scripts/ci/check-public-api.sh apps/bcs/scripts/ci/check-r25-conformance.sh`：通过。
- `git diff --check`、修改文件 1,000 行上限：通过。
- `cargo check --manifest-path apps/bcs/Cargo.toml --workspace --all-targets -j1`：**exit 0**，13m23s；日志 `review-workspace-check.log`。
- `check-port-purity.sh`、`check-store-boundaries.sh`：**PASS**（使用本机 rg 包装器）；日志 `review-boundary-gates.log`。
- `cargo test --manifest-path apps/bcs/Cargo.toml -j1 -p bcs-dist-store --test group_terminal_authority --test conformance_group_round -- --test-threads=1`：最终追加 closure 损坏断言后 **14 passed、0 failed，exit 0**；日志 `review-final-regressions.log`。

## 验证边界和状态

- 磁盘约剩 19 GiB，低于此前 20 GiB 保护阈值。本次创建独立 tmpfs target，未修改用户 Cargo 配置或删除共享 target；内存构建单并发，保护阈值为可用 RAM 2 GiB、tmpfs 余量 1 GiB。
- 本轮未执行全 workspace tests、真实 baseline/worktree API 清单比较、完整 R25（包含全 workspace 测试链接）。不能用脚本单测或历史 GREEN 替代；现有内存盘容量也不能保证完整工作区链接。历史 R25 红/中止与全量红的记录仍保留，不自动豁免。
- 未运行真实 MySQL 全迁移链与 repo harness；没有 disposable MySQL 配置。未交付/认证外部 P（持久协调 provider），未做跨进程恢复。
- 终态权威映射已明确且测试可执行，但 T7 生产写者仍未安装；真实停止事实汇聚、冷转存与业务下一轮/Feed 非阻塞闭环不能由注入 checkpoint 的测试替代。

**核定：D0 原冻结结论暂停，待当前修订的必需门禁重新闭环；Stage 2 有实现和本地回归证据，但未达到退出条件，不放行 Stage 3。** 修复审查问题不自动完成外部依赖或全部阶段验收。
