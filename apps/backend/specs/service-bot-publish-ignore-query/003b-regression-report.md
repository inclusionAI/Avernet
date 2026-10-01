# Publish-ignore 查询接口独立回归报告

## 范围与环境

- 时间：2026-09-18，macOS / Python 3.12.13，Backend 与 Engine 各自项目 `.venv`。
- 工作树：`service-bot-publish-ignore-ops-rel20260917`；HEAD：`5cfd442f466a2ac2fdc341d183d50ba192a36d7a`。
- 仅执行本地 pytest（DI/TestClient、fake transport、临时文件）。没有启动真实 Bot、relay、模型网关或部署服务，没有操作 `/home/admin` 运行目录。
- 本报告仅由独立回归执行者更新；不修改生产实现。新增行为用例注册于仓库原有测试模块，不修改全局 agent/skill 注册表。
- 工作树已有未提交的取消签名、原地重启改动。下面的 HEAD → 工作树覆盖率包含这些改动，不能冒充纯查询 feature 或远端目标分支覆盖率。

## 全量测试

| 模块 | passed | skipped | failed/error | 耗时 | 结果 |
|---|---:|---:|---:|---:|---|
| Engine（实现冻结后重跑） | 2729 | 5 | 0 | 69.72 s | PASS |
| Backend | 18871 | 43 | 0 | 279.82 s | PASS |

两套全量合计 **21600 passed、48 skipped、0 failed/error**。Engine 20 warnings、Backend 545 warnings，主要为既有依赖弃用与 mock coroutine 警告，未作为成功用例隐藏；本次没有修改警告配置。

Engine 命令（在 `src/engine`）：

```bash
COVERAGE_FILE=/tmp/query-engine-final.coverage .venv/bin/python -m pytest -q -n 4 --cov=engine --cov-report=json:/tmp/query-engine-final-coverage.json --cov-report=xml:/tmp/query-engine-final-coverage.xml --junitxml=/tmp/query-engine-final-junit.xml
```

Backend 命令（在 `src/backend`）：

```bash
COVERAGE_FILE=/tmp/query-backend-final.coverage .venv/bin/python -m pytest tests -q -n 4 --cov=agentclaw.community --cov-report=json:/tmp/query-backend-final-coverage.json --cov-report=xml:/tmp/query-backend-final-coverage.xml --junitxml=/tmp/query-backend-final-junit.xml
```

Engine 首次运行 2727 passed / 5 skipped，期间主 agent 补充两个日志行为测试；已以上表第二次冻结后的全量结果替代，不混合两个快照。

Backend 全量运行期间主 agent 仅加强既有 runtime 查询日志测试断言，没有修改生产代码或测试数量；其随后独立重跑该模块 **24 passed**。全量证据绑定冻结后的生产实现，最终增强断言证据来自该次定向重跑。

## 行为与边界日志证据

- Engine `community/api/tests/test_publish_ignore.py`：真实 GET 路由、三阶段身份、缺失/空文件、CRLF/注释/重复规则/Unicode 分隔符、非法 UTF-8/规则、FIFO/目录/符号链接/大小限制、只读 `os.open` flags 与目录字节快照不变、原 POST 回归。
- Engine `test_query_logs_safe_success_and_failure`：`query_request/query_success/query_failure` 事件，GET、request_id、expected_target、结果和耗时；Authorization、Cookie、异常字符串凭据哨兵不出现在日志中。依赖异常返回安全错误码，不泄漏原始异常。
- Backend `test_publish_ignore_engine_contract.py`：复用实际 Engine 应用/DI，从 Backend runtime 经 BaaS/ARCA 两种传输执行三阶段 GET；缺失/有内容两类文件；验证查询后目录内容不变。
- Backend service/runtime/router/endpoint tests：权限、当前 binding、逐设备固定 UUID、ARCA 连接、无设备/无绑定、部分失败保留成功结果、非法响应不伪造成空列表、请求参数校验和统一端点注册。
- Backend `test_query_service_logs_success_failure_and_denies_credentials`、runtime 的 GET/safe-log 与 upstream-failure 用例：入站/出站请求、成功/失败事件，系统、GET、路由、关联 ID、状态、耗时、业务入参/结果和 credential 哨兵。主 agent 最终加强了出站请求/成功/失败字段断言并重新通过。不会记录 headers/connection 或原始异常文本。
- 超过 4096 UTF-8 字节的 paths 列表：仅日志省略并保留 `paths_omitted`、entry_count、revision，HTTP 响应仍保留完整 paths；Backend service、runtime 与 Engine 均有行为断言。

## 本地覆盖率与 ACI 边界

| 模块 | 实际执行用例通过率 | 仓库 report_check 用例指标 | 总行覆盖率 | HEAD→工作树变更可执行行 |
|---|---|---|---|---|
| Engine | 2729/2729（100%），另跳过 5 | 2734/2734（100%） | 16301/18586（87.71%） | 47/47（100%） |
| Backend | 18871/18871（100%），另跳过 43 | 18914/18914（100%） | 98664/110713（89.12%） | 113/113（100%） |

`scripts/ci/report_check.py` 已分别对 Engine、Backend JUnit/XML 验证 case >=100%、line >=70%，均通过。其当前实现将 skipped 计入 passed，因此报告明确保留 pytest 原始 passed/skipped，而不宣称跳过用例实际执行成功。

变更行通过 `git diff --unified=0 HEAD -- <module source>` 的新增行，与同次 coverage JSON 的 executed_lines/missing_lines 取交集计算；只计算可执行生产代码行，不用测试文件填充。Engine router、models、protocol、DI、plugin 的变更分别为 27/27、4/4、2/2、1/1、13/13，无漏覆盖。

Backend router 37/37、service API 1/1、deploy composer 1/1、managed composer 2/2、restart mixin 1/1、task 7/7、publish-ignore service 27/27、kernel 6/6、runtime plugin 31/31，无漏覆盖。部分变更仅为参数续行/删除行，没有独立可执行行，不能将其数量虚增。查询核心 service/runtime 文件整体覆盖率均为 100%，Engine 查询相关文件整体覆盖率均为 100%；大型 Backend router 整体为 99.16%。

当前变更尚未提交，不能用 HEAD..HEAD 的零变更结果充当 ACI 增量门禁；远端目标分支 base/head 检查 **NOT RUN**，远端 ACI **NOT RUN**。本地 pytest/覆盖率不是部署或线上验收。

本地工作树预检：**PASS**（case 100%、line >=70%、changed >=90%）；正式目标分支 commit base/head 的 ACI 兼容预检仍 **NOT RUN**。`git diff --check` 通过。

## 结论

**本地回归 PASS**。查询行为、现有功能全量用例、边界日志和本地覆盖率证据通过；未执行部署、真实 Bot 验收、提交、PR 或远端 ACI。
