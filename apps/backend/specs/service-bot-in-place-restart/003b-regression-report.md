# 原地重启独立本地回归报告

## 环境与范围

- 时间：2026-09-17；独立 regression agent 执行。
- Avernet：当前 worktree，HEAD `5cfd442f466a2ac2fdc341d183d50ba192a36d7a` 加未提交变更。
- daas：`/Users/helloworld/Desktop/codes/teamclaw/agentclaw-daas-scripts` 当前工作区。
- 解释器：Avernet worktree `src/backend/.venv/bin/python`。
- 本功能无 Engine adapter / relay 改动，未启动无关模型会话和本地服务，未操作远端 Bot、部署或 PR。已有 publish-ignore 去签名变更保留，后端全量测试也覆盖当前组合工作区。
- 等待 Backend 两位实现者冻结生产代码后执行全量。期间仅 router 测试增加正向日志断言；追加独立重跑该文件 32 例验证最终测试版本。

## 结果

| 项目 | 结果 | 耗时/证据 |
|---|---|---|
| Backend 全量 tests，4 workers，coverage | PASS：18815 passed，43 skipped，0 failed | 266.18s，545 warnings |
| 最终 router 测试文件重跑 | PASS：32 passed | 0.13s |
| daas 新增 startup 11 例 + 既有 transfer 17 例 | PASS：28 passed | 0.66s |
| `bash -n bootstrapping/start_service.sh` | PASS | exit 0 |
| 已修改 Python 文件指定语法/未使用检查 | PASS | flake8 E999,E902,E117,F405,E712,E701,E702,F821,F822,F823,F831,F401,F841 |
| 两个仓库 `git diff --check` | PASS | exit 0 |

Backend 总收集 18858，执行通过 18815，跳过 43。不得将 skipped 描述为已验证通过。

## 行为与日志证据

- 新路由复用权限与发布单服务；真实 DI endpoint 场景包含可访问与权限拒绝。服务层及任务测试覆盖缺省/false/true 参数、旧 payload、正常升级和既有 recreate 透传。
- `test_in_place_restart_deploy.py` 的 6 例使用真实 async Build → BaaS create/upgrade → managed composer，仅 stub HTTP 边界；断言最终出站 payload 中 true 才有 `--in_place_restart true`，None/false 不新增标志，并保留 source_dir/useNas/start_service 命令。
- Shell 测试执行真实解析、reexec 和迁移/只读代码片段，不只是源码字符串匹配；true 在 source_dir 有/无时均不调用迁移脚本，但继续调用 readonly。false 保持普通迁移成功及失败退出1、FAILED marker 语义；非法 flag 值保留迁移。
- 最终 router 测试正向断言 request/result/failed 日志与关联 publish_id、in_place、结果和耗时字段，并通过异常注入断言敏感 `private-value` 不落日志。Shell 断言跳过日志含 `in_place_restart=true`、普通失败日志含 `migration failed`。
- 本轮没有新增 HTTP transport，出站沿用既有 BaaS 请求路径；未以这些断言宣称所有历史 transport 的所有字段/所有凭据类型已独立审计或远端日志已验证。

## 注册本轮回归命令

```bash
# cwd: Avernet worktree/src/backend
.venv/bin/python -m pytest tests -q -n 4 --cov=agentclaw --cov-report=json:/tmp/service-bot-in-place-restart-coverage.json --cov-report=term --junitxml=/tmp/service-bot-in-place-restart-junit.xml
.venv/bin/python -m pytest -q tests/community/adapters/http/service_bot/test_router_publish_coverage.py
# cwd: /Users/helloworld/Desktop/codes/teamclaw/agentclaw-daas-scripts
/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917/src/backend/.venv/bin/python -m pytest -q tests/test_start_service_in_place_restart.py tests/test_service_bot_transition_transfer.py
bash -n bootstrapping/start_service.sh
```

预期：全部执行测试通过；新模式跳过迁移不跳过 readonly，旧模式不变。本轮只在任务报告注册，不修改全局 Engine 用例或 skill/agent 定义。

## 覆盖率与 ACI 边界

- 工作区总行覆盖：98593 / 110564 = 89.1728%，满足 >=70% 的数值要求。
- 本地已执行用例通过率：18815 / 18815 = 100%；另有 skipped=43，failed=0。
- 变更行覆盖：由独立 reviewer 基于该 JSON 与本轮 diff 另行计算；本报告不把总行覆盖替代增量覆盖。
- 尚无包含本轮变更的 commit head/base 对，ACI 兼容提交级预检 NOT RUN；远端 ACI NOT RUN。上述工作区数据不是 ACI PASS。
- 原始证据：`/tmp/service-bot-in-place-restart-backend-full.log`、`/tmp/service-bot-in-place-restart-coverage.json`、`/tmp/service-bot-in-place-restart-junit.xml`。

## 结论

本轮本地功能回归 PASS；未发现失败用例。未做远端环境验证，尚未通过提交级 ACI 门禁。Backend 与 daas 需配套交付，不能以本地通过推断远端已生效。
