# 本地回归报告：service-bot-upgrade-draft-restart

- Agent：tc-engine-regression（独立定向验证）。
- 日期：2026-10-29（Asia/Shanghai）。
- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-upgrade-draft-restart`。
- 本轮基线：`github/dev` / `31bbfdc933ebf5e9361a273ea16b668326092b48`；测试运行于尚未提交的功能工作区。
- 既有链路：HTTP 权限/锁 → BotPublishService 创建或复用后继草稿 → 普通源 Bot 的 draft restart。
- 允许新增点：发布服务重启接线、部分成功异常、adapter/facade 映射及必要测试/契约。
- 禁止触碰点：ONLINE/VERIFY 发布实例、普通重启内部策略/锁、Teclaw 重启、Engine、Relay、Bootloader、数据库 schema。

## 实际执行

在 `apps/backend` 使用新 worktree 的 `.venv/bin/python`（Python 3.12，`uv sync --frozen` 安装依赖）：

```bash
DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -m pytest \
  tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py \
  tests/community/core/bot_management/services/test_bot_service_restart_baas_envs.py \
  tests/community/core/bot_management/services/test_bot_service_aix_extra_envs_restart.py \
  -q --junitxml=/tmp/service-bot-upgrade-restart-lifecycle.xml

DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -m pytest \
  tests/community/endpoints/test_endpoint_runner.py \
  -k 'upgrade_mints_next_version or upgrade_requires_success or upgrade' \
  -q --junitxml=/tmp/service-bot-upgrade-restart-endpoint.xml
```

| 范围 | 通过 | 失败/错误/跳过 | 耗时 | 证据 |
| --- | ---: | --- | --- | --- |
| 既有普通 restart 锁幂等、BaaS env、coding extra env 生命周期 | 136 | 0/0/0 | 0.62s | `/tmp/service-bot-upgrade-restart-lifecycle.xml` |
| 真实 DI/SQLite/HTTP endpoint runner 中 upgrade 场景 | 9 | 0/0/0 | 2.86s | `/tmp/service-bot-upgrade-restart-endpoint.xml` |

独立读取 XML 确认累计 145/145 passed、0 failures、0 errors、0 skipped；endpoint 另有 1712 deselected，不计为通过或跳过。`git diff --check` 通过。仅存在项目预有的 Pydantic/Starlette 弃用 warning。

## 证据边界与日志审计

- 既有 endpoint `upgrade_mints_next_version` 使用 Teclaw，证明真实 HTTP → 发布服务 → 真实草稿持久化且跳过普通 restart 的兼容路径；不能把此结果扩张为新非 Teclaw 重启链路已做真实远端验证。
- 新增 service 用例审计覆盖新建重启真实 owner、两类后继复用、Teclaw 跳过、异常/显式失败保留草稿、源主键缺失或不一致拒绝重启。其外部生命周期调用使用替身，未访问真实 BaaS/ARCA。
- 已见事件断言：`service_bot.upgrade.draft_restart.request/response/failure/skipped`（Teclaw reason），HTTP `service_bot.upgrade.request/failure`；测试凭据哨兵不出现在成功结果日志或失败异常日志。
- 已向主编排反馈需补强 HTTP 成功响应日志及关键业务字段断言，以及真实仓储保留草稿的新路径证据。等待编码阶段补测后追加此报告。
- 本任务没有 Engine/Relay 实现变更；不启动无关 claude_code 环境，不运行需要真实模型/凭据的历史 Engine 用例，不修改全局回归注册表。

## 覆盖率与远端门禁

本报告的两组定向运行未采集覆盖率，不把定向文件覆盖率当作 Backend 总行覆盖率。

| 指标 | 当前状态 |
| --- | --- |
| 独立定向 casePassRate | 145/145 = 100%，skipped=0，failed/errors=0 |
| Backend 总行覆盖率（>=70%，仓库 CI 当前阈值75%） | 待主编排完整 CI 产物核验 |
| 变更行覆盖率（本任务>=90%） | 待主编排相同 base/head 产物核验 |
| 远端 ACI/CI | PENDING：尚无本轮实际 PR/job 证据 |

## 当前结论

**独立既有生命周期与 HTTP 定向回归 PASS；新增链路补测及全量覆盖门禁待补证。** 未部署、未触发真实 Bot 重启、未把 PENDING 描述为健康完成。

## 补充回归：持久化、HTTP 日志与异常链

编码方补齐 SQLite 仓储及 HTTP 成功日志用例，并修复 OpenAPI 错误日志可能携带 provider 异常 cause 链的问题后，独立再次执行：

```bash
DEPLOY_PROFILE=test PYTHONPATH=src:. .venv/bin/python -m pytest \
  tests/community/core/service_bot/services/test_upgrade_draft_restart.py \
  tests/community/repository/publishing/test_bot_publish_unified.py \
  tests/community/adapters/http/service_bot/test_router_publish_coverage.py \
  -k 'upgrade or provider_exception_chain' -q \
  --junitxml=/tmp/service-bot-upgrade-restart-new-path.xml
```

结果：**15 passed、76 deselected、0 failed/errors/skipped，0.34s**。累计独立执行 160 个已选定用例，均通过；不得把 deselected 计为通过。

新增可核验证据：

- 真实 SQLite 发布单仓储中，新草稿在生命周期提交异常后仍为 DRAFT，并保持 last_pub_id/source_bot_pk；再次 upgrade 返回相同记录且生命周期调用数保持 1。
- Router 成功路径断言 `service_bot.upgrade.request/response`，失败路径断言 request/failure；包含凭据哨兵的 ext 没有被完整写入日志。
- 新的 `test_openapi_error_logging_does_not_include_provider_exception_chain` 使用真实发布服务、facade 与 `envelope_errors` 装饰器（生命周期提交为替身），返回 502 且 provider 异常的凭据哨兵不出现在 logger.exception 输出；领域异常使用 `from None` 抑制不安全下游异常链。
- 本轮仍没有访问真实 BaaS/ARCA，PENDING 仍仅表示提交受理；SQLite持久化及错误包装证据不等于线上重建完成。

**更新结论：独立定向回归 PASS。** 先前“新增链路补测待补证”已由上述用例补齐；Backend 总行/变更行覆盖门禁及远端 CI 仍按主编排实际完整产物另行核验，不在本报告伪称通过。
