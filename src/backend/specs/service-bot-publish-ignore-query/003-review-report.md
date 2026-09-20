---
agent: tc-code-reviewer
status: completed
created: 2026-09-18
iteration: 1
---

# 查询 ignore 接口代码评审报告

## 范围

- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`
- 分支：`feat/service-bot-publish-ignore-ops-rel20260917`
- HEAD：`5cfd442f466a2ac2fdc341d183d50ba192a36d7a`；本轮代码尚未提交。
- 输入：001-spec-output.md、002-code-report.md、frontend-api.md，以及实际工作区 diff/源码/测试。
- 本轮涉及 10 个生产代码文件与 8 个测试/factory 文件。原有取消签名、原地重启的 dirty diff 不是本轮新增；只核对文档对这些接口的描述，不冒充重新完成其全部评审。

## 固定检查维度

| 维度 | 结论 | 证据 |
| --- | --- | --- |
| 正确性 | PASS | 三阶段、双 provider、固定目标、逐实例部分失败；缺文件与坏文件分开处理；解析保持 LF/CRLF、顺序、重复与原始字节摘要 |
| 安全性 | PASS | GET/POST 共用管理权限与共享 binding resolver；固定文件路径，读取前校验运行实例身份；NOFOLLOW/NONBLOCK/普通文件/大小限制复用 |
| 性能 | PASS | 单次有界读取，不创建锁或 journal；沿用逐实例网络访问与超时；大路径日志摘要不截断 API 结果 |
| 架构及最小变更 | PASS | GET 平放既有 router；DTO/协议同步，权限和目标解析在原 service；未扩展通用 resolver/传输/数据库 |
| 测试覆盖 | PASS | 独立 Backend 113/113、Engine 65/65；query 函数可执行行 91/91，详见下节；非远端 ACI 替代品 |
| 静态检查 | PASS | 生产及相关测试实跑 flake8 `F,E203,E265,E303`，无告警；git diff --check 通过 |
| 外部系统边界日志 | PASS | Backend 入站/Engine 出站及 Engine 入站请求、成功、失败事件；关联 ID/耗时/非敏感业务结果；异常类型安全映射、凭据哨兵测试和大列表摘要测试 |
| ACI 覆盖率门禁 | PENDING | 本轮尚无提交 head/远端 job；不得用上述 focused 覆盖率宣称 ACI PASS |

## 独立验证

Backend 使用当前 worktree `.venv/bin/python -m pytest`，运行以下六套相关测试并收集三个生产模块覆盖率：

- `tests/community/api/test_publish_ignore_router.py`
- `tests/community/core/service_bot/test_publish_ignore_service.py`
- `tests/community/core/service_bot/test_publish_ignore_runtime.py`
- `tests/community/core/service_bot/test_publish_ignore_engine_contract.py`
- `tests/community/contracts/test_publish_ignore_runtime.py`
- `tests/community/endpoints/test_publish_ignore.py`

结果：113 passed、0 failed、0 skipped。core service 83/83，runtime 79/79；包含整个既有巨型 router 时为 291/875（33.26%），未覆盖部分是本次 focused 测试不负责的旧发布接口，不能拿这个局部运行冒充模块总行覆盖率门禁。

Engine 独立运行 `src/engine/community/api/tests/test_publish_ignore.py`：65 passed、0 failed、0 skipped。plugin 125/125；router 加 plugin 为 179/194（92.27%），未覆盖 router 102–125 为既有 Bot config 逻辑。

从独立 coverage JSON 的 executed/missing lines 与源码 AST 函数范围求交：

| 本次新增函数 | 可执行行覆盖 |
| --- | --- |
| Backend query_publish_ignore | 10/10 |
| Backend PublishIgnoreService.query | 20/20 |
| Backend HttpPublishIgnoreRuntime.query | 30/30 |
| Engine query_publish_ignore | 22/22 |
| Engine FilePublishIgnoreService.query | 9/9 |

这是本地函数范围覆盖率，不是 base..head 变更行覆盖率。独立证据位于 `/tmp/query-review-backend-final-coverage.json` 和 `/tmp/query-review-engine-final-coverage.json`；全量回归由 003b-regression-report.md 单独记录。

### ACI 三项证据

- Base / Head：本轮尚无可发布提交对；上列 HEAD 不包含本轮未提交变更。
- casePassRate：远端分子/分母待提供；本地 focused 为 Backend 113/113、Engine 65/65。
- lineCoverage：远端分子/分母待提供；不能使用 focused scope 替代模块门禁。
- changeLineCoverage：远端分子/分母待提供；必须在提交后的实际 base..head 上计算。
- 阈值保持：100% / 70% / 90%，未降低、未添加排除或 no-cover。
- 远端 ACI job：PENDING；没有执行部署或真实 Bot 接口调用。

## Review Spec 对照

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | HTTP 薄适配与协议同步 | PASS | 没有 router 内 binding 编排；真实 DI endpoint 与 consumer 协议测试覆盖 |
| R-02 | 与 change 权限一致 | PASS | 提取原 `_resolve_targets`，可信 super_admin 与 ADMIN/OWNER；拒绝路径在读取前结束 |
| R-03 | 解析与快照一致 | PASS | 同一次 bytes 生成 paths/count/revision；空文件、重复、CRLF、Unicode 非 LF 分隔符及非法输入均有行为断言 |
| R-04 | 零状态写入 | PASS | 打开 flags 断言禁止创建/写入，查询前后目录内容字节相同；不存在文件不创建 ignore/lock/journal |
| R-05 | 模型、日志、异常 | PASS | 双层独立 request_id、逐实例结果；不存在合并去重、静默丢弃非法规则或错误空列表 |
| R-06 | 新增路径覆盖与静态质量 | PASS | 新增 query 函数 91/91；旧 POST 纳入 focused 回归；远端门禁单列 PENDING |

## 文档核验及审查闭环

frontend-api.md 的三接口方法、入参、响应封装与当前源码一致。已核对 ApiResponse 默认值、原地重启四种发布状态映射，以及 `POST restart_status` 的 `data.data.status` 嵌套。文档正确区分异步提交、发布单状态和本次重启结果，并注明当前接口没有唯一任务 ID 的关联限制。

审查中提出的问题已修复并独立复跑：Engine 意外依赖异常补安全 query_failure 与固定错误码；请求事件补耗时；Engine 和 Backend 两层对超过 4096 UTF-8 路径字节的日志使用摘要，API 保持完整；补充 query consumer 协议成功/失败测试。没有剩余必须修复项。

## 整体结论

**PASS（本轮代码审查）；ACI evidence PENDING。**

继续独立全量回归；提交后以实际目标 base/head 完成远端 ACI。未获得部署授权，不能将本报告解释为允许直接部署。
