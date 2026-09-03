---
agent: tc-code-reviewer
status: completed
created: 2026-08-31T23:43:11+08:00
iteration: 2
---

# 代码评审报告

## 评审范围

- Avernet worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/cli-default-install-identity`
- DaaS worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/daas-script_worktree/cli-default-install-identity`
- 分支: 两仓均为 `feat/cli-default-install-identity`
- 改动: Avernet 39 个 working-tree 路径（含 manifest、scope builder、SQL、测试）；DaaS 7 个路径。
- 基线: Avernet `origin/dev=552de2932ed3bff0274f20483ac88043b7353e8c`；DaaS `origin/dev=04528bd71e0f36deb68e30ddfe613e9d700c47e9`。两仓 `HEAD` 均等于各自基线，尚无提交。

## 逐条评审意见

### 固定检查维度

| 维度 | 结论 | 说明 |
|---|---|---|
| 正确性 | PASS | Bootstrap、MCP sync、runtime projection、Default CLI 删除四个 overwrite writer 均先读完整 AgentPass snapshot，再经 `build_passport_resource_scope()` 写完整 MCP+CLI scope；回归覆盖 AgentPass-only MCP `caller` 身份不被降级。 |
| 安全性 | PASS | YAML 使用 `safe_load` 并校验 catalog/profile/argv；installer 仅用固定 argv、固定 allowlist URL，下载后校验 SHA-256 再执行临时脚本，无 `eval`、`sh -c` 或 `curl | bash`。CLI identity writer 仅接受 `owner/caller`，非法 scope fail closed。 |
| 性能 | PASS | 每次启动一次 scope 查询/合并，installer 每个受管 CLI 一次 probe；无 N+1 或用户可控无界循环。 |
| 代码风格 | PASS | 两仓 `git diff --check` 通过；Avernet 目标文件 `ruff check` 通过；DaaS `ruff check` 通过（仅项目既有 lint 配置弃用 warning）。 |
| 测试覆盖 | PASS | Backend 关联集 **369 passed**；Default CLI 核心模块覆盖 92%–97%，DaaS installer 为 **199/210 (95%)**。低文件级覆盖的旧大模块仅作候选缺口，新增 executable paths 均以 diff/coverage 交集复核。 |
| ACI 覆盖率门禁 | PENDING | worktree 尚未提交，无法得到真实 `base..head`、完整 JUnit/coverage XML 或远端 ACI job；不得标记为 PASS。 |
| 静态检查 | PASS | 实测无新增 ruff/import/whitespace 告警。 |
| 外部系统边界日志（如适用） | PASS | AgentPass scope、CLI call-type、DaaS probe/bootstrap/install 均记录 requested/succeeded/failed（或 probe）、关联 ID/status/error_type/duration；secret-bearing 异常的日志断言未发现原始敏感值。 |

### ACI 覆盖率证据

- Base / Head: Avernet `552de293...` / `552de293...`；DaaS `04528bd...` / `04528bd...`。改动仍在 working tree，真实 ACI diff 不存在。
- 用例: **PENDING**；本地关联回归为 Backend `369/369`、DaaS `26/26`，但不是 ACI 全量 JUnit job。
- 总行覆盖率: **PENDING**；未生成面向整个 PR 的 ACI coverage XML。
- 变更行覆盖率: **PENDING**；本地候选复核：sync/runtime/device 新增 executable lines 100%，skillsets 19/21 (90.5%)，Passport 边界 97% 文件覆盖且新增 fail-closed 分支已覆盖；不能替代远端 ACI。
- 未覆盖变更行: ACI 尚不可计算；本地 R2 发现的 sync builder-failure 与 Passport malformed-input 缺口均已补测并复跑。
- 远端 ACI job: **PENDING**。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
|---|---|---|---|
| R-01 | YAML profile 精确匹配 | PASS | 仅 `openclaw`、`claude_code/generalCC` 命中；`normalCC`、`aicoding` fail closed，caller API 同样受该 gate 限制。 |
| R-02 | 所有 overwrite writer 保留完整 MCP + CLI identity | PASS | 共享 pure builder 接入 Bootstrap、sync、runtime、remove-CLI；回归验证 sparse row 不存在时仍保留历史 MCP caller。 |
| R-03 | CLI caller/owner 与 AgentPass 配置闭环 | PASS | sparse row 写入后强制完整 Passport 更新；owner 删除 row；CLI 不接入 MCP aggregate/IAM。按用户澄清，一期开放 AgentPass authorization configuration，真实 engine principal 消费留作 E2E 验收。 |
| R-04 | CLI 不影响 MCP aggregate/IAM | PASS | CLI repository 不修改 `ac_bots.call_type`、`caller_config_revision` 或 IAM 换签；相关断言通过。 |
| R-05 | CLI HTTP endpoint 边界 | PASS | endpoint 位于既有 caller router，`OWNER + EDIT_LOCK` 由授权表声明；业务查 Bot、profile、CLI scope、锁和补偿均在 service。 |
| R-06 | Bootstrap 不接受任意 CLI code | PASS | Backend 只返回 AgentPass 已授权 scope；DaaS 仅安装受控 YAML catalog 的交集。 |
| R-07 | manifest/hash/argv/路径事实 | PASS | 两仓 manifest byte-identical，SHA-256 为 `af08444ff08130d1f2b0b74b7b222f5ce1ff59447feabe1c76009c6cbef23da2`；acli URL/hash allowlist、managed bin、atomic state、probe-first 均有测试。 |
| R-08 | 安装时机与两个 engine | PASS | `start_service.sh` 在 Bootstrap/Join 后、任一 engine/finalize 前调用 installer，重启也会 probe；未接入既有 `eval` 并行框架。 |
| R-09 | 外部边界日志与脱敏 | PASS | sync/runtime/remove-CLI 成功、失败和 DaaS early failure 都记录低敏结构化字段；日志捕获测试覆盖 AgentPass/installer secret 异常且不泄露。 |
| R-10 | 方法职责边界 | PASS | reconciler 负责读取与收敛，pure builder 负责合并，caller service 负责授权、持久化、补偿和触发 reconcile。 |
| R-11 | 领域模型与不变量 | PASS | `ac_bot_cli_call_config` 有 bot/CLI/engine/env 唯一键与索引、revision/CAS 补偿、tenant guard；Owner 通过删除 sparse row 表达，且不改 MCP 表或 Bot aggregate。 |
| R-12 | 改动行覆盖 >90% | PASS（本地候选） | R2 逐项实测补齐 sync build failure 与 Passport null/non-list/name/desc/non-mapping fail-closed 分支。远端 ACI 的正式 change-line 指标仍为 PENDING。 |

## 验证命令与证据

```bash
# Backend focused regression
uv run pytest tests/community/core/mcp/services/test_cli_capabilities.py \
  tests/community/core/mcp/services/test_cli_passport_scope.py \
  tests/community/core/mcp/services/test_sync_service.py \
  tests/community/core/mcp/test_defaults_per_engine.py \
  tests/community/core/caller_identity/test_service.py \
  tests/community/repository/identity/test_caller_identity_repository.py \
  tests/community/core/devices/services/test_device_service_router.py \
  tests/community/adapters/http/openapi_v1/test_caller_identity_endpoints.py \
  tests/community/endpoints/test_caller_identity_router.py \
  tests/community/contracts/gateway/test_rule15_skillsets.py \
  tests/community/plugin_api/test_passport_resource_scope.py \
  tests/community/core/skill_center/test_skill_set_management_service.py \
  tests/community/endpoints/test_skillset_cli_resources.py -q
# 369 passed

# DaaS installer / shell syntax
uv run pytest tests/test_managed_cli_installer.py -q
bash -n bootstrapping/bootstrap_device_auth.sh
bash -n bootstrapping/start_service.sh
# 26 passed
```

`install_managed_clis.py` local coverage: `199/210 (95%)`。`test_passport_resource_scope.py` 为 `21 passed`、Passport 97%；`test_sync_service.py` 覆盖 scope builder error 后，sync 本期 executable diff `33/33`。

## 整体结论

**结论: PASS（代码评审）**

此前 REJECT 项均已由行为断言验证：完整 snapshot 合并、CLI identity fail-closed、DaaS 可移植 probe-first 安装、profile fail-closed，以及无凭据的可观测日志。

### 后续门禁（非本轮代码 REJECT）

1. 提交两仓代码后执行远端 ACI，取得 `casePassRate >= 100%`、`lineCoverage >= 70%`、`changeLineCoverage >= 90%` 的正式证据。
2. 在真实 OpenClaw 与 `claude_code/generalCC` 容器验证 AgentPass CLI `identity_mode` 的实际 principal 消费，以及真实 acli artifact 对 `dataphin`/`di` 的安装目录和 `--version` 探测。
