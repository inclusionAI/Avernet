---
agent: tc-code
status: completed
created: 2026-10-29
iteration: 1
---

# 编码报告

## Worktree 与范围
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-upgrade-draft-restart`
- Branch: `fix/service-bot-upgrade-draft-restart`
- 编码基线: GitHub `inclusionAI/Avernet` dev `31bbfdc933ebf5e9361a273ea16b668326092b48`。
- 用户已确认 spec 并授权新 worktree、dev rebase、创建 PR 及修复 CI。按用户要求，不创建 OCB worktree、不更新 gitlink。
- 既有链路：HTTP interceptor / OpenAPI facade 权限及锁检查 → 发布单升级 → 持久化新草稿。
- 允许新增点：发布服务首次创建后接入普通草稿重启、安全部分成功异常、HTTP 错误映射、API 契约和测试。
- 禁止触碰点：普通重启内部设备策略、发布 ONLINE/VERIFY 容器、schema、状态机、Relay/BCS/Engine/Bootloader。

## 行为与逐文件变更
| 文件 | 必要变更 |
| --- | --- |
| `core/service_bot/services/bot_publish_service.py` | 新草稿持久化后调用局部 `_restart_upgraded_draft`；按 PK 校验源 Bot 身份，使用当前 owner；Teclaw 跳过；提交失败保留草稿。两个既有幂等分支不进入重启。 |
| `core/service_bot/services/publish_exceptions.py` | `UpgradeDraftRestartError` 携带已创建草稿及固定安全消息。 |
| `adapters/http/service_bot/router_publish.py` | 部分成功异常映射业务500并携带原草稿data；结构化请求和最终成功/失败事件；其余错误码保持。 |
| `core/service_bot/services/service_publication_facade.py` | 共享调用的重启失败映射既有 `ServiceContainerUpstreamError` 502契约；草稿可经生命周期查询取回。 |
| `api/bot_publish_service.py` | 补充原Protocol遗漏的 `upgrade_publish` 方法并说明新建副作用、Teclaw、幂等和失败恢复。 |
| `tests/.../test_upgrade_draft_restart.py` | 新建、当前真实owner、2种幂等、Teclaw、显式失败/异常、身份不匹配、日志及公共HTTP异常链安全。 |
| `tests/.../test_router_publish_coverage.py` | HTTP成功日志及敏感字段不输出、失败500+草稿data。 |
| `tests/.../test_service_publication_facade.py` | facade将保留草稿异常映射为既有上游错误，避免误报权限。 |
| `tests/community/repository/publishing/test_bot_publish_unified.py` | 真实SQLite仓储证明失败后草稿仍存在，下一次升级返回相同草稿且不重复重启。 |

非泛化重构、无依赖新增、无配置变更。仅新操作关联日志选取非敏感标量；不输出Bot/ext或provider原始异常。

## 验证
运行解释器：`apps/backend/.venv/bin/python`（Python3.12，主agent按锁文件创建）。

1. TDD RED：`python -m pytest apps/backend/tests/community/core/service_bot/services/test_upgrade_draft_restart.py -q --no-cov` 初始7 failed/2 passed，原因是缺少新重启行为/异常；HTTP/facade新增2例先失败。
2. TDD GREEN/最终受影响套件：
```bash
apps/backend/.venv/bin/python -m pytest   apps/backend/tests/community/core/service_bot/services/test_upgrade_draft_restart.py   apps/backend/tests/community/core/service_bot/services/test_bot_publish_service.py   apps/backend/tests/community/adapters/http/service_bot/test_router_publish_coverage.py   apps/backend/tests/community/core/service_bot/services/test_service_publication_facade.py   apps/backend/tests/community/repository/publishing/test_bot_publish_unified.py::test_upgrade_restart_failure_keeps_persisted_draft_for_idempotent_retry   -q --no-cov
```
结果：245 passed，17条既有Pydantic弃用warning；0 failed。
3. 原有生命周期和架构：
```bash
apps/backend/.venv/bin/python -m pytest   apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py   apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_baas_envs.py   apps/backend/tests/community/core/bot_management/services/test_restart_authorization_refresh.py   apps/backend/tests/community/architecture/test_no_oversized_modules.py -q --no-cov
```
结果：152 passed，17条既有Pydantic弃用warning；0 failed。
4. `ruff check` 本次8个源码/服务/HTTP测试文件通过；`git diff --check`通过。新增仓储测试所在旧文件整体ruff存在基线F841（第454行已有 `built` 未使用）；未修改无关代码，新增测试无lint问题。
5. 修改源码行数（首轮）：bot_publish_service1622、router_publish2218为既有超大文件allowlist，原架构测试通过，未新增豁免；facade849、publish_exceptions51、API Protocol43，其余新增文件均<1000。
6. 无远端发布/重启调用，无提交/push/PR由本agent执行。全量CI、覆盖率分子分母和远端门禁由主流程验证，未把上述focused测试冒充远端ACI PASS。

## 新操作边界日志
- `service_bot.upgrade.request`：system/backend、入站、POST路由、publish_id/operator。
- `service_bot.upgrade.response` / `.failure`：成功/错误码、draft_id/status/version/last_pub_id、耗时；不输出发布ext。
- `service_bot.upgrade.draft_restart.request`：原/新发布单、source_bot_pk/id、operator、draft stage；内部生命周期操作。
- `.draft_restart.response`：当前真实owner、engine、status、binding_id、耗时，表达受理而非健康完成。
- `.draft_restart.skipped`：Teclaw不支持；两种幂等复用分支均记录existing_successor、原/新发布单及source PK/id/operator，不会调用helper。
- `.draft_restart.failure`：安全固定reason、异常类型、耗时、draft_retained；不记录原始异常内容。真实ARCA/BaaS操作仍由原有普通生命周期执行，无新增出站客户端。
- 脱敏：不读取或记录完整URL query、headers、Bot/ext、原始provider异常；测试token哨兵、Bot secret字段哨兵均不出现在新日志。

## Review 回退修复
- 独立review发现原`raise ... from exc`可经公共HTTP错误日志打印provider原始异常链。新增真实`envelope_errors → facade → service`回归先复现凭据哨兵泄露，再在新安全异常边界改为`raise UpgradeDraftRestartError(draft) from None`，最终测试确认HTTP502且原始哨兵不入日志；不改全局错误日志设施。

## 兼容与限制
- 同步调用直到现有重启方法返回提交结果；不等待健康完成。原重启中的备份/准备仍可能耗时。
- 首次提交失败不回滚草稿；幂等upgrade不补重启，恢复走手动草稿重启。没有引入跨进程恰好一次或持久补偿承诺。
- 仅草稿运行态受影响，旧正式发布实例不进入此重启链路；Teclaw维持现有不支持普通重启边界。


## 日志字段复核补充
- 独立review后补HTTP出口version/last_pub_id及幂等existing_successor稳定事件；具体成功、失败、跳过事件/字段均有行为断言。
- 复用`get_logger()`现有factory；`community/log.py`文档明确corp/prod logger自带trace-ID注入。社区测试默认stdlib无请求追踪上下文，未新增获取/伪造trace或全局设施。
- 这轮日志字段增补后，两受影响测试文件43 passed；四个修改Python文件ruff及diff check通过。主agent全量CI另行记录最终head覆盖率。

## 本地凭据门禁测试数据修复
- 主流程提交后的凭据扫描命中4处合成哨兵常量（非真实凭据）。参照仓库scanner fixture规范，将测试值从字面凭据赋值改成分片拼接及变量引用；运行时仍包含secret/token哨兵，保留全部日志不泄露断言。
- 未改扫描规则、业务代码或忽略配置；仅修改两个测试文件的数据构造。
- 修复工作树后：受影响两文件43 passed；两文件ruff及`git diff --check`通过。
- 使用官方`scanner._findings(git diff --unified=0 github/dev --)`核验完整当前工作树源码差异，0 findings。
- `check_secrets.py --base github/dev --head HEAD`仍检出旧已提交版本的4处哨兵，因为修复尚未提交；由主流程提交本次修复后按相同命令复验，不把工作树扫描冒充已提交门禁PASS。

## 全量CI架构门禁修复
- 全量CI发现HTTP路由领域异常精确名单遗漏`UpgradeDraftRestartError`。按该门禁“Errors raised by services that the router translates to HTTP”现有规则，仅登记此精确异常名称；没有模块通配、禁令删除或服务类豁免。
- router改为直接从`publish_exceptions.py`定义模块导入新异常，避免经具体BotPublishService模块重导出。
- 完整`test_http_adapter_layer_is_http_only.py`及新草稿重启、router和原发布服务三个测试文件复验：183 passed，17条既有Pydantic弃用warning。
- 两个改动Python文件ruff、`git diff --check`通过；官方scanner._findings读取完整当前工作树相对github/dev差异为0 findings。未提交，交主流程提交后重跑最终全量CI。
