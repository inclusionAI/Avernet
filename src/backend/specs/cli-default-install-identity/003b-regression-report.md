# Engine 回归测试报告（iteration 2）

## 环境

- Avernet worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity`
- DaaS worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/daas-script_worktree/cli-default-install-identity`
- Avernet Base / Head: `origin/dev` / `HEAD` 均为 `552de2932ed3bff0274f20483ac88043b7353e8c`（实现尚未提交）
- DaaS Base / Head: `origin/dev` / `HEAD` 均为 `04528bd71e0f36deb68e30ddfe613e9d700c47e9`（实现尚未提交）
- 测试时间: 2026-08-31 23:28 +0800

## 结果汇总

- 最新 Backend 回归: **246 passed**（scope 60、writer 115、caller/manifest 71）。
- Default CLI 删除 endpoint: **4 passed**（另有 1,268 个无关 endpoint case 被 `-k` 排除）。
- DaaS managed CLI installer: **26 passed**，独立 coverage **199/210 = 95%**。
- Backend 核心新增模块 coverage: **716/756 = 95%**（单模块 92% / 97% / 97% / 92% / 95%）。
- 失败: 0；跳过: 0（上述定向集）。
- 真实引擎/容器 E2E: **PENDING**。

## 逐条结果

| # | 用例/检查 | 结论 | 证据 |
|---|---|---|---|
| 1 | 默认 YAML、Bootstrap 收敛、CLI caller/owner、Passport fail-closed | PASS | legacy `identity_mode` owner 归一，以及 invalid/duplicate writer payload 拒绝均有回归 |
| 2 | AgentPass-only MCP caller 保留 | PASS | sync、runtime projection、remove-default-CLI 三条 overwrite writer 均断言历史 `caller` 不被写回 `owner` |
| 3 | 三 writer 可观测性与脱敏 | PASS | `agentpass_mcp_scope_*`、`agentpass_runtime_scope_*`、`agentpass_default_cli_scope_*` 均覆盖 requested/succeeded/failed，断言 status/error_type/duration 且 token secret 不落日志 |
| 4 | CLI caller 切换补偿与 profile gate | PASS | 仅 `openclaw`、`claude_code/generalCC` 可切换；补偿事件包含 actor、锁状态、耗时，AgentPass scope failure 不泄露 secret |
| 5 | DaaS installer | PASS | probe-first 幂等跳过、state 丢失后已安装 CLI 跳过、固定 argv、download-before-exec SHA、失败阻断、脱敏日志均覆盖 |
| 6 | Manifest / artifact 完整性 | PASS | 两份 manifest 字节一致，SHA-256=`af08444ff08130d1f2b0b74b7b222f5ce1ff59447feabe1c76009c6cbef23da2`；只读下载 acli 脚本 SHA-256=`aa7ec0dab2289f887a8626be6ae8bd9483bbac16cab0a9076699141fb6b9e9aa`，与 manifest 一致 |
| 7 | 静态与 shell 检查 | PASS | 两仓 `git diff --check`；目标 Python 的 `ruff check`；`bash -n bootstrapping/bootstrap_device_auth.sh` 与 `start_service.sh` 均通过（DaaS 仅有既有 Ruff 配置弃用 warning） |

本轮 Backend 命令：

```bash
uv run pytest tests/community/core/mcp/services/test_sync_service.py tests/community/core/mcp/services/test_cli_passport_scope.py -q
uv run pytest tests/community/core/skill_center/test_skill_set_management_service.py tests/community/contracts/gateway/test_rule15_skillsets.py -q
uv run pytest tests/community/core/caller_identity/test_service.py tests/community/core/mcp/services/test_cli_capabilities.py -q
uv run pytest tests/community/endpoints/test_endpoint_runner.py -k "skillsets and clis" -q
```

## 外部系统边界日志

- Backend Bootstrap / AgentPass CLI reconcile：`cli_passport_reconcile_*` 与 `agentpass_cli_scope_update_*` 记录 bot、逻辑 engine、scope 计数、状态、耗时和异常类型；不记录 Passport token、agent code 或原始响应。
- 三个既有 overwrite writer 经 shared full snapshot builder 恢复 AgentPass MCP/CLI identity；sync、runtime projection、Default CLI removal 都记录低敏 scope 计数、stage、状态、耗时和 error type。
- DaaS installer 只执行 allowlisted URL 与固定 argv；下载完成并匹配 SHA-256 后才执行 `bash <temp-file>`，没有 `curl | bash`、`eval` 或 YAML shell command。失败日志带 action、受控 manifest 元数据、状态、错误类别和耗时；测试断言不泄露 bootstrap token。

## ACI 兼容覆盖率预检

- Base / Head: 上述 SHA 相同；变更仍仅在未提交工作区，`base..head` 的 changed lines 为 0，不能有效运行 change-line coverage。
- 用例通过率: 定向集均 100%。
- 总行覆盖率: 本地核心模块 95%；DaaS installer 95%。这不是 ACI 总行或增量门禁证据。
- 工作区 diff-cover: **93%**（sync/runtime 的可执行变更行为 100%）；这是未提交工作区的本地预检，不能替代 ACI。
- 变更行覆盖率: **ACI NOT RUN**；提交形成真实 head 后，仍需按相同 base/head 执行项目 `scripts/ci/report_check.py` 或等价 ACI 检查，满足 100% / >=70% / >=90%。
- 远端 ACI: **PENDING**（没有 PR/job）。

## 真实引擎验证边界

本机检查时 Relay `:18900` 无监听，Engine `:20003/health` 和 Backend `:8888/api/system/readiness` 均连接失败。当前改动也不在 relay/engine 源码；DaaS Bootstrap 需要真实 device、Bot binding 和 AgentPass，不能用其他 OCB worktree 的本地 claude_code 启动脚本代替。因此以下保持 PENDING：

1. OpenClaw Bot 重启后的 Bootstrap -> manifest install -> `dataphin`、`di` 实际可执行；
2. `claude_code/generalCC` 同一路径；
3. 两个执行面实际消费 AgentPass CLI `identity_mode=caller` 的 execution principal。

## 结论

**本地功能回归 PASS。Release / ACI 为 BLOCKED（待真实容器 E2E、真实提交后的增量覆盖率和远端 ACI）。**
