---
agent: tc-code-reviewer
status: completed
created: 2026-10-29
iteration: 3
---

# 代码评审报告

## 评审范围
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-upgrade-draft-restart`
- Branch: `fix/service-bot-upgrade-draft-restart`
- Base: GitHub `inclusionAI/Avernet` dev `31bbfdc933ebf5e9361a273ea16b668326092b48`。
- Head: 当前尚未提交的任务变更；包括tracked diff与新增测试，不使用main替代目标基线。
- 范围：5个源码/契约文件、4个测试文件，以及本任务spec/reports；不含OCB/gitlink、Engine、Relay、schema或部署。
- 已读AGENTS、批准的001 Review Spec、002编码报告、架构context/protocol约束与reviewer历史经验。`/tmp/CODE_REVIEW.md`不存在。

## 整体结论

**代码评审：PASS。远端 ACI/CI：PENDING。**

首次审查发现一项P1异常链凭据泄露，已经修复并完成独立复验；最终diff无未修复的阻塞问题。此结论不替代主流程正在执行的Backend全量CI、提交后base/head覆盖率检查及远端PR门禁。

## 固定检查维度

| 维度 | 结论 | 证据与范围 |
| --- | --- | --- |
| 正确性 | PASS | 仅创建后继草稿分支调用普通生命周期；两个已有后继分支不重启；Teclaw跳过；显式失败与异常均保留草稿并显式失败。 |
| 安全性 | PASS | 从不可变source_bot_pk读取当前Bot并核对source_bot_id，再传真实owner；不以operator或发布单资源entity地址执行重启；原HTTP interceptor及facade ADMIN检查保留。原始provider异常不进入新公共错误链。 |
| 架构与最小性 | PASS | 核心接线在原service；router仅响应映射与边界日志；facade复用既有502上游失败契约；Protocol补齐已有具体方法的真实签名。未扩张设备策略或新增通用日志设施。 |
| 性能 | PASS | 新增一次按主键读取；不循环等待health，不新增daemon线程。既有restart中的备份/准备耗时属于已明确的同步提交权衡。 |
| 代码风格 | PASS | 无邻近重构/格式化扩张；既有超大文件沿现有allowlist，本次不新增豁免。 |
| 行为测试 | PASS | 独立执行新增service和3组窄架构测试共228 passed；最后日志补齐后重新执行service/router共43 passed。包含真实OpenAPI错误包装器的凭据哨兵断言。002另提供真实SQLite留存与幂等恢复测试；回归agent独立执行受影响集。上述集合有重叠，不累加为总数。 |
| 静态检查 | PASS | 8个改动源码/服务/router测试文件完整ruff通过，git diff --check通过。仓储测试文件已有基线F841由002单列，未由本次引入或顺手清理。 |
| 外部边界日志 | PASS | HTTP请求、成功/失败、内部提交请求/结果/失败、Teclaw与幂等跳过事件存在，包含发布谱系、源身份、操作者、结果/错误码、耗时和version/last_pub_id；只枚举安全字段，不dump Bot/ext/provider异常。 |
| ACI覆盖率门禁 | PENDING | 本轮为未提交diff，独立focused测试未生成全模块覆盖报告；必须由主流程基于实际base/head及远端job记录三项分子/分母。 |

## Review Spec 逐项检查

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R01 | 允许新增点与分层 | PASS | 局部helper只负责源身份、引擎跳过与普通restart接线；无router领域编排。 |
| R02 | 鉴权/编辑锁与执行身份 | PASS | 现有装饰器/facade解析保留，真实owner只用作授权通过后的内部执行身份。 |
| R03 | 首次/已有草稿/失败/Teclaw语义 | PASS | 已有后继两分支直接返回且记录existing_successor；创建后失败草稿不删除；Teclaw不调用restart。 |
| R04 | 草稿与正式阶段隔离 | PASS | 调用BotService.restart_bot，未调用PublishFlowService的VERIFY/ONLINE restart；普通service重启既有stage为DRAFT。 |
| R05 | OpenAPI错误兼容 | PASS | legacy返回ApiResponse业务500并携带draft data；OpenAPI保留既有ServiceContainerUpstreamError HTTP502，草稿通过生命周期查询恢复。两者差异已在002说明。 |
| R06 | 低敏日志与字段 | PASS | 失败不输出原始provider message；修复cause链泄露；已补version/last_pub_id及两类幂等skipped，并有成功/失败/跳过字段断言。 |
| R07 | 未使用变量/import及质量 | PASS | 窄ruff与diff检查通过；未改无关的历史F841。 |
| R08 | 验证准确性 | PASS | 受理不写健康完成；不声称跨进程恰好一次；focused tests不冒充远端ACI。 |

## 首轮问题与修复证据

### P1：原始provider异常经公共HTTP日志的cause链泄露（已修复）
- 初始位置：`core/service_bot/services/bot_publish_service.py::_restart_upgraded_draft`使用`raise UpgradeDraftRestartError(draft) from exc`，facade再次包装为ServiceContainerUpstreamError并保留cause。
- 触发路径：provider异常 → 新局部异常 → facade上游异常 → OpenAPI `envelope_errors` → `log_public_error(exc_info=exc)`。即使helper自身不打印原始message，Python traceback仍会打印整条异常链。
- 独立初次复现：调用真实`log_public_error`并构造相同两层异常链，输出`raw_credential_in_http_error_log=True`；这是必须修复项，首轮不得PASS。
- 修复：新安全异常边界改成`raise UpgradeDraftRestartError(draft) from None`，不修改全局日志或公共错误包装器。
- 复验：`test_openapi_error_logging_does_not_include_provider_exception_chain`经过真实`envelope_errors → facade → service`，断言HTTP502、failure事件存在及原始凭据哨兵不出现；独立228例及最后43例中均通过。

### 日志契约补齐（已完成）
- 首次diff未带HTTP响应version/last_pub_id，幂等分支仅有旧文案。已补最小字段与稳定`draft_restart.skipped reason=existing_successor`事件，并完成精确字段断言。
- trace：复用项目既有`get_logger`工厂；`community/log.py`明确prod工厂负责trace-ID注入，社区stdlib本身没有该请求上下文。未为本次任务新增全局设施或伪造trace/request ID。业务发布单与source身份始终可关联；真实部署的trace关联由主流程/QA核验，不能以本地stdlib测试冒称已验证prod tracing。

## 独立执行命令与结果

从`apps/backend`运行：

```bash
.venv/bin/python -m pytest tests/community/core/service_bot/services/test_upgrade_draft_restart.py tests/community/architecture/test_api_layer_is_protocols_only.py tests/community/architecture/test_service_api_conformance.py tests/community/architecture/test_module_boundaries.py -q --disable-warnings
```

结果：228 passed，17条既有Pydantic弃用warnings。Protocol名称/具体实现、层依赖和context boundary门禁通过。

最终日志补齐后：

```bash
.venv/bin/python -m pytest tests/community/core/service_bot/services/test_upgrade_draft_restart.py tests/community/adapters/http/service_bot/test_router_publish_coverage.py -q --no-cov
```

结果：43 passed，17 warnings。

对router、API Protocol、publish service、exceptions、facade及三个相关service/router测试文件运行`.venv/bin/python -m ruff check <8 paths>`：All checks passed；`git diff --check`通过。

## ACI覆盖率证据
- Base / Head：已知初始base如上；最终提交head待主流程生成并rebase后核验。
- casePassRate：本地独立focused集合100%，不得当作完整远端job分母。
- lineCoverage：PENDING，完整Backend covered/total待主流程提供，最低要求>=70%（保留仓库更严格阈值）。
- changeLineCoverage：PENDING，最终base/head changed-covered/changed-total待主流程计算，要求>=90%。
- 远端ACI job：PENDING/未提供；没有远端PASS声明。

## 已知边界
- `create_publish`是plain INSERT，不会因upsert返回已有草稿；本次不存在“insert复用旧草稿仍触发restart”的新增路径。旧upgrade查后建及非唯一last_pub_id未改，本次不提供并发/跨进程恰好一次承诺；普通restart仍沿用既有durable锁。
- 首次提交失败后upgrade重试只返回既有草稿，恢复走手动草稿重启。这是已批准方案，不将幂等返回误记为补偿完成。
- 本review不访问真实Bot、不部署、不创建/更新OCB gitlink，也不替用户合并PR。


## 全量CI架构门禁修复复审

主流程首次Backend全量报告发现唯一失败：HTTP层依赖guard未登记新增领域异常名`UpgradeDraftRestartError`；其他26678 passed、40 skipped、2 subtests由主流程报告，本review不将该次失败全套记作PASS。

最终补丁仅两点：

1. router从异常定义模块`core.service_bot.services.publish_exceptions`直接导入该类型，避免通过具体service模块re-export导入。
2. `test_http_adapter_layer_is_http_only.py::_CORE_SERVICE_NAMES_OK`在既有“Errors raised by services that the router translates to HTTP”段精确登记`UpgradeDraftRestartError`。

**复审结论：PASS，此为原guard设计允许的领域异常登记，不是降低门禁。** Guard文件开头的第二条不变量明确允许core/services中的异常类型和helper，禁止的是具体service类。新类型仅继承`BotPublishServiceError`并携带草稿record/安全消息，不是service实现。此次没有添加模块级后缀豁免、路径豁免、正则通配或关闭具体service检查；现有非endpoint文件及flat-core-service反例测试仍原样运行。

独立验证（最终两文件补丁）：

```bash
apps/backend/.venv/bin/python -m pytest apps/backend/tests/community/architecture/test_http_adapter_layer_is_http_only.py apps/backend/tests/community/adapters/http/service_bot/test_router_publish_coverage.py apps/backend/tests/community/core/service_bot/services/test_upgrade_draft_restart.py -q --no-cov
```

结果：49 passed，17条既有warnings；包含guard全部6例及既有具体service导入反例。上述两个改动文件ruff通过，`git diff --check`通过。业务接线和P1修复无变化。

本地最终全量及PR3292新head远端CI仍交主流程完成；此处不把旧head运行中或首次唯一失败替换成远端PASS。
