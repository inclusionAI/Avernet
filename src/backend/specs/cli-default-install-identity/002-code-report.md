---
agent: tc-code
status: completed
created: 2026-08-31T23:00:03+08:00
iteration: 4
---

# 编码报告

## Worktree 信息

- Avernet: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity`
- DaaS: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/daas-script_worktree/cli-default-install-identity`
- 分支: `feat/cli-default-install-identity`

## 已实现

- 新增版本 `2026-08-31.1` 的 canonical CLI manifest，并在 DaaS 放置 SHA-256 字节一致副本。仅精确匹配 `openclaw` 与 `claude_code/generalCC`，默认补齐 `dataphin`、`deepinsight-cli`。
- `CliItem`/Passport extractor 无损透传并 fail-closed 校验 `identity_mode`；Default 能力集响应也返回该字段。
- Bootstrap 收敛历史 AgentPass CLI、YAML 默认项、CLI sparse caller override 和 MCP sparse identity，并只在全量 scope 变化时一次写回 MCP + CLI；Bootstrap 响应兼容保留 `agent_code`，新增低敏 manifest/code 投影。
- 增加 `ac_bot_cli_call_config`（含 env/tenant/唯一和查询索引）以及 Caller/Owner API。CLI Caller 更新不修改 Bot/MCP aggregate、`caller_config_revision` 或 IAM；AgentPass 全量 scope 写失败时补偿 sparse 行。
- DaaS installer 采用 safe YAML、严格 argv、固定 allowlisted URL、SHA-256 校验和原子 state；在 Bootstrap/Join 后、两种目标引擎启动前执行，probe 成功会跳过安装。

## 改动文件列表

| 范围 | 改动类型 | 说明 |
|---|---|---|
| `configs/cli-capabilities.yaml`、`core/mcp/services/cli_{capabilities,passport_scope}.py` | 新增 | manifest 校验、精确 profile 与完整 Passport scope 收敛 |
| `plugin_api/passport.py`、skill-center schema、`_defaults.py` | 修改 | CLI identity 无损传输、UI 展示；保持 generalCC 创建时注册既有 aicoding CLI，Bootstrap 额外补齐 YAML CLI |
| `core/caller_identity/{models,contracts,service,sql/...}.py`、identity repository/protocol、DI、OpenAPI router/schema/authorization | 新增/修改 | CLI sparse caller/owner、锁、补偿及 API |
| `core/devices/services/device_service_router.py` 与 four device DI modules | 修改 | Bootstrap 运行 scope reconcile 并输出低敏 installer metadata |
| `confs/cli-capabilities.yaml`、`bootstrapping/install_managed_clis.py` | 新增 | DaaS byte-identical artifact 与受控安装器 |
| `bootstrapping/{bootstrap_device_auth,start_service}.sh`、`pyproject.toml`、`uv.lock` | 修改 | 受限 Bootstrap 投影与引擎启动前安装挂点 |
| 对应 `tests/community/...`、`tests/test_managed_cli_installer.py` | 新增/修改 | manifest、scope、Bootstrap、API、稀疏回退、installer 和 UI identity 回归 |

## TDD 证据

- RED: `uv run pytest tests/community/core/mcp/services/test_cli_capabilities.py -q` 初始因 `cli_capabilities` 模块缺失失败；实现后该目标 suite 通过。
- RED: `uv run pytest tests/test_managed_cli_installer.py -q` 初始因安装器缺失失败；实现后覆盖 acli/CLI probe skip、manifest mismatch、受控 bootstrap/install/state。
- RED: `uv run pytest tests/community/contracts/gateway/test_rule15_skillsets.py::TestSkillsetResources::test_resources_schema_includes_default_set_clis -q` 证明 `identity_mode` 被 response schema 丢弃；加字段后通过。

## 测试执行结果

- Avernet focused: `uv run pytest tests/community/core/mcp/services/test_cli_capabilities.py tests/community/core/mcp/services/test_cli_passport_scope.py tests/community/core/mcp/test_defaults_per_engine.py tests/community/core/caller_identity/test_service.py tests/community/repository/identity/test_caller_identity_repository.py tests/community/core/devices/services/test_device_service_router.py tests/community/adapters/http/openapi_v1/test_caller_identity_endpoints.py tests/community/endpoints/test_caller_identity_router.py tests/community/contracts/gateway/test_rule15_skillsets.py -q` → **144 passed**。
- DaaS focused: `uv run pytest tests/test_managed_cli_installer.py -q` → **4 passed**。
- DaaS shell: `bash -n bootstrapping/bootstrap_device_auth.sh && bash -n bootstrapping/start_service.sh` → PASS。
- 静态检查: 两仓 `git diff --check` → PASS；Avernet changed-file `ruff check` → PASS；DaaS changed-file `ruff check` → PASS（仅 pyproject 现有弃用 warning）。

## 外部系统边界日志

- Backend Bootstrap/AgentPass: `cli_passport_reconcile_*`、`agentpass_cli_scope_update_*` 和 `bootstrap_device_auth_*` 记录 bot/device、engine、CLI code 数量、状态、耗时和错误类型；不记录 token、agent_code、完整 Passport/Bootstrap body。
- CLI caller API: `cli_call_type_update_*` 记录 bot、CLI code、目标类型、阶段和错误类型；补偿也有低敏状态日志。
- DaaS: `managed_cli_probe`、`managed_cli_bootstrap_*`、`managed_cli_install` 记录 code、manifest version/digest、exit code、状态和耗时；不记录下载内容、环境变量或命令输出。

## 已知限制

- 本仓未在真实 OpenClaw 和 Claude Code 容器中验证 CLI 执行面是否消费 AgentPass CLI `identity_mode`。因此已实现的 API/AgentPass scope 是授权配置闭环，最终 caller token 的引擎消费仍需目标环境验证；本实现没有把 CLI 误接入 MCP aggregate/IAM。
- 未 commit、push、deploy 或重启容器；覆盖率总门槛由独立回归/ACI 继续验证。

## Review 回退修复（iteration 2）

- CLI caller/owner 的 overwrite 不再自行拼 MCP scope；它经唯一的 `CliPassportScopeReconciler` 从一次 `query_agent_passport()` 完整 MCP+CLI snapshot 构建。回归保留仅存在于 AgentPass 的 MCP `caller` identity。
- `extract_cli_items()` 会把 legacy 查询中缺失的 CLI `identity_mode` 正规化为 `owner`；`unpack_resource_scope()` 作为 overwrite writer 边界则 fail closed，拒绝非 mapping、无 code、重复 code、缺/非法 identity（仅 `owner|caller`）。
- DaaS manifest 测试改为本仓 artifact 的受控 SHA-256，不再引用任何 `/Users/...` Backend worktree；installer 增加 YAML/Bootstrap/state、SHA 不匹配、acli 重 probe、固定 argv 失败和 entrypoint 脱敏日志回归。
- `cli_call_type_update_requested/succeeded/failed/compensated` 事件现记录 actor、锁是否提供、阶段/错误类型及耗时；AgentPass reconcile 和 DaaS failure 均有 secret/token 不进入日志的断言。补偿日志的 RED 证据是缺少 `actor_id`、`lock_epoch_supplied`、`duration_ms`，最小实现后转 GREEN。

## Iteration 2 验证

- Backend focused + coverage：**196 passed**，候选模块合计 **92%**；`cli_capabilities` 97%、`cli_passport_scope` 92%、Passport boundary 92%、CLI caller service 92%、CLI repository 95%。`device_service_router.py` 整文件为 85%，但本期 Bootstrap 增量的成功、scope 失败和 Passport token 失败分支均有行为测试；其未覆盖行属于既有多实例委托与非本期 provider 分支。
- DaaS installer + coverage：**24 passed**，`install_managed_clis.py` **95%**；`bash -n bootstrapping/bootstrap_device_auth.sh`、`bash -n bootstrapping/start_service.sh` 均通过。
- 静态：Avernet/DaaS 目标文件 `ruff check`、`pyflakes`、两仓 `git diff --check` 全部通过。DaaS Ruff 仅输出项目既有 `pyproject.toml` lint 配置弃用 warning。
- ACI 仍需在真实提交后计算 `base..head` change-line coverage；本 worktree 仍未提交，不能把本地模块覆盖率伪称为 ACI 结果。

## Iteration 2 TDD 证据

- RED：`uv run pytest tests/community/core/caller_identity/test_service.py::test_cli_scope_failure_compensates_the_sparse_override -q` → 1 failed，补偿事件缺少 `actor_id`。
- GREEN：同一命令在补偿日志补齐 actor、锁状态和耗时后 → 1 passed；随后 Backend focused 196 passed、DaaS 24 passed。

## Iteration 3：DaaS probe-first 幂等修复

- 发现并修复：原 installer 仅在 `state.manifest_digest` 命中时才 probe；state 丢失或 digest 轮换时，即使 CLI 已存在仍会执行 `acli install`。现改为每个受管 code 一律先 probe；probe 成功立即写入当前 `manifest_digest` 与 `installed[code]` state 后跳过，不调用 acli bootstrap/install。
- RED：`uv run pytest tests/test_managed_cli_installer.py::test_successful_probe_without_state_skips_install_and_records_current_digest -q` → 1 failed；原调用序列包含 `acli --version` 和 `acli install dataphin`。
- GREEN：同一命令 → 1 passed，仅调用 `dataphin --version` 并写入 state。DaaS full suite `uv run --with pytest-cov pytest tests/test_managed_cli_installer.py --cov=install_managed_clis --cov-report=term-missing -q` → **25 passed**, **95%**。
- 质量：DaaS `ruff check`、`pyflakes`、`git diff --check`、两个启动 shell `bash -n` 均通过；Ruff 仍仅输出项目既有 `pyproject.toml` lint 配置弃用 warning。

## Iteration 4：overwrite scope、低敏日志与一期 profile gate

- 完整 scope：`sync_service`、runtime projection 与 Default-CLI 删除均使用共享 `build_passport_resource_scope()` 从完整 AgentPass MCP+CLI snapshot 构造各自的 membership 写入；因此 AgentPass-only MCP `caller` identity 不会被无 local sparse row 的 writer 回写为 owner。
- 低敏可观测性：MCP sync snapshot/update、runtime projection update、Default-CLI remove query/build/update 均记录 stable requested/succeeded/failed 事件，字段仅包括 bot、engine、branch、stage、MCP/CLI 数量、status、`error_type` 与 `duration_ms`。原始异常、Passport body、token/secret 均不记录；HTTP remove 路径用 `from None` 消除异常链泄漏。
- 一期 gate：`CliPassportScopeReconciler.supports_profile()` 复用 manifest 的精确 profile；CLI caller/owner 在查询 Passport 或写 sparse 行之前，仅允许 `openclaw` 和 `claude_code/generalCC`，`aicoding`、`claude_code/normalCC` 以既有 `CallerIdentityReadOnlyError` 拒绝。

### Iteration 4 TDD 与验证

- RED：`uv run pytest tests/community/core/mcp/services/test_sync_service.py::TestRefreshMcpScope::test_does_not_update_passport_when_cli_scope_query_fails -q` 原先日志/返回拼接 `passport-token-secret`；runtime 与 remove-CLI 的 secret-bearing snapshot logger tests 同样因没有 structured events 失败。`uv run pytest tests/community/core/caller_identity/test_service.py::test_cli_update_rejects_profiles_outside_phase_one -q` 初始 **2 failed**，错误走到了 CLI-not-found 而非 profile gate。
- GREEN focused：`uv run pytest tests/community/core/mcp/services/test_sync_service.py tests/community/core/mcp/services/test_cli_passport_scope.py -q` → **58 passed**；`uv run pytest tests/community/core/skill_center/test_skill_set_management_service.py tests/community/contracts/gateway/test_rule15_skillsets.py tests/community/endpoints/test_skillset_cli_resources.py -q` → **115 passed**；`uv run pytest tests/community/core/caller_identity/test_service.py tests/community/core/mcp/services/test_cli_capabilities.py -q` → **71 passed**。
- GREEN coverage：七个 focused 文件的 `pytest --cov` 命令 → **246 passed**。模块行覆盖：CLI scope **97%**、runtime projector **97%**、caller service **93%**、sync service **89%**、skillsets router **29%**（旧 router 的未涉改 handler 拉低整文件）。R-12 追加 shared-builder failure 的不泄密测试后，`diff-cover /tmp/cli-default-r09-coverage.xml --compare-branch=origin/dev --diff-range-notation=...` 对本期变更行给出 **93%**（124 行，8 行未覆盖）；其中 sync 与 runtime executable diff 均 **100%**，满足本地变更行门槛，不能将其表述为远端 ACI 结果。
- Passport boundary R-12：`uv run --with pytest-cov pytest tests/community/plugin_api/test_passport_resource_scope.py -q --cov=agentclaw.community.plugin_api.passport --cov-report=term-missing --cov-report=xml:/tmp/cli-passport-r12-coverage.xml` → **21 passed**、整文件 **97%**；其后不带 path filter 的 `diff-cover /tmp/cli-passport-r12-coverage.xml --compare-branch=origin/dev --diff-range-notation=...` → 本期 Passport changed executable lines **39/39（100%）**。覆盖 legacy `clis=None`、非 list、非法 name/desc 与 non-mapping overwrite scope 的 fail-closed 边界。
- Final combined coverage：将上述 Passport suite 并入七个 writer/caller focused 文件运行同一 `pytest --cov` 命令 → **267 passed**；`diff-cover /tmp/cli-default-final-coverage.xml --compare-branch=origin/dev --diff-range-notation=...` → **95%**（163 行，8 行未覆盖）。`sync_service`、runtime projector、Passport boundary 的本期 executable diff 均 **100%**。
- 静态：Avernet changed-file `ruff check`、`pyflakes`、`pycodestyle --select=E203,E211,E265`、`git diff --check` → PASS。
- DaaS replay：`uv run --with pytest-cov pytest tests/test_managed_cli_installer.py -q --cov=install_managed_clis --cov-report=term-missing` → **26 passed**, installer **95%**；DaaS `ruff`、`pyflakes`、两个启动 shell `bash -n` 和 `git diff --check` → PASS（仅既有 Ruff 配置弃用 warning）。测试产生的 DaaS `.coverage` 已移至废纸篓。
