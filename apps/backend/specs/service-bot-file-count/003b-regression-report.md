# Engine 回归测试报告

## 环境

- 测试时间：2026-09-21 16:48–16:50 Asia/Shanghai。
- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-file-count-rel20260922`。
- 基线：`github/REL20260922`，`b2014be448b887068881ffafa7d9e09a1e4272ac`。
- Python 3.12.13；复用 `service-bot-publish-ignore-ops-rel20260917/src/engine/.venv/bin/python`，当前 Engine 目录运行且 `PYTHONPATH=src`。未修改或同步借用环境。
- 不部署、不调用真实 Bot、不使用全局 Agent/Skill 中的旧 OCB 工作区与硬编码凭据。未启动 Relay/共享 Backend；HTTP 合同由进程内 TestClient 驱动，文件系统测试使用临时目录及真实子进程。

## 结果汇总

- 全量 Engine：2845 passed，0 failed，0 errors，0 skipped，5 deselected，耗时 97.25 秒；通过率 100%。
- 五项 deselect 原样沿用仓库 `src/engine/scripts/ci_test.sh` 的 community 发行形态配置，均为缺失 corp 包的 profile 专属断言；没有新增排除。
- 新增文件计数：50 项通过（36 scanner + 14 router），JUnit 累计用例耗时 1.654 秒，已包含在全量结果中，不重复计入总数。
- 17 条既有依赖弃用提示：Starlette/httpx 和 tar extract 未来默认行为；无测试失败。

## 逐条结果

| 范围 | 结论 | 证据与边界 |
| --- | --- | --- |
| Engine 全量与旧文件接口 | PASS | 2845/2845，既有 upload/read/remove/rmtree/list 及其它 Engine 测试一并执行 |
| TC-01/02/03 普通文件、隐藏、ZIP、硬链接、FIFO、链接、空目录、相对/绝对路径 | PASS | `plugins/tests/test_file_count.py`，真实临时文件系统 |
| TC-04/05 根边界、祖先链接、目录链接交换与 inode 替换 | PASS | fd 相对打开和不跟随链接；断言未打开根外目录 |
| TC-06 不存在、非目录、权限/I/O 错误与扫描中变更 | PASS | 错误分类断言；权限及竞态通过确定性故障注入验证，不返回部分计数 |
| TC-07 超时、取消、重复取消、启动期间取消与 SIGTERM 无效 | PASS | 真实子进程 terminate/kill/reap；检查 returncode、PID 消失、后续请求可运行 |
| TC-08 并发与槽恢复 | PASS | 2 个活跃 worker，上限外立即 busy；取消回收后恢复；无排队模式 |
| TC-15 Engine 日志 | PASS | 请求/成功/稳定失败、原始异常脱敏、取消与 cleanup 关联，详见下节 |
| TC-17 Engine HTTP/adapter/filesystem 合同 | PASS | TestClient → router → OpenClawFileAdapter → 生产文件端口 mixin → worker；不代表真实远端全链路 |
| TC-18 不支持实现 | PASS | Claude Code 与本地内存 OpenClaw 明确 unsupported；缺 FILE_LIST 的 HTTP 501 |
| Backend 权限/阶段/多副本/provider/ignore 独立 | 独立报告 | 主 agent 执行 Backend 回归，本报告不重复宣称这些检查已由 Engine 验证 |
| 全局注册表旧 Relay、模型、Bot 创建、消息/会话 E2E | NOT RUN | 依赖外部模型、旧工作区或真实运行服务，超出本次明确授权的本地文件计数范围；不得计为通过 |

## 外部系统边界日志

- 事件：`engine.file_count.request`、`engine.file_count.response`、`engine.file_count.failure`、`engine.file_count.cleanup`。
- 入站/响应字段：system、operation、direction、engine、path、request_id、status、elapsed_ms；成功含 response.path/file_count/elapsed_ms，失败含安全 error_code 和 null file_count；cleanup 含 reaped、pid 与同一 request_id。
- 断言：成功 HTTP 计数及 request_id 一致；十种稳定错误映射；原始异常内模拟凭据不出现在响应、最终 `caplog.text` 或结构化 LogRecord；取消后 cleanup 和 failure 使用同一 request_id 且进程已退出。
- 递归脱敏测试覆盖大小写 Token、AUTHORIZATION、Cookie、password、secret、api_key、credential、session，嵌套列表/字典脱敏并保留 path/request_id。未打印真实凭据。
- Backend 出站 BaaS/ARCA 请求与失败日志由 Backend 报告给出；此处未访问真实 provider。

## ACI 兼容覆盖率预检

| 指标 | 结果 | 阈值 |
| --- | --- | --- |
| casePassRate | 2845/2845 = 100%，skipped=0，failed=0 | 100% |
| lineCoverage | 41150/43922 = 93.69% | ≥70% |
| changeLineCoverage | 604/606 = 99.67% | ≥90% |

证据：`src/engine/pytest_report/TEST-junit.xml`、`src/engine/pytest_report/TEST-cov.xml`；隔离覆盖数据 `src/engine/.coverage.regression`。执行与 CI 相同的完整 `pytest src`、五项 deselect、JUnit/XML coverage 参数，仅使用已安装解释器避免 `uv sync` 修改依赖环境。

独立复跑 `scripts/ci/report_check.py`，阈值 100/70/90，退出码 0，结论 **PRECHECK PASS**。Base 为 `b2014be448b887068881ffafa7d9e09a1e4272ac`，Head 为已 stage 并冻结的 Git tree `e445d39d6a4e43d538d0705289b3a0533bbb4226`。`git cat-file -t` 确认对象类型为 tree，而非 commit；此树包含实际功能变更，因此不是空 `base..HEAD` 的伪通过。检查时该树与当前 `src/engine/src` 的 diff 为空。最终 commit 后由主 agent 确认源码相同并以实际提交重跑门禁。

未覆盖变更行：`src/engine/src/engine/community/api/file/router.py:68`、`src/engine/src/engine/community/plugins/file_count_worker.py:115`。前者是 CapabilityNotSupportedError/NotImplementedError 兼容转换分支，可补插件抛出上述异常时稳定返回 unsupported 的断言；后者是 worker 作为主模块运行时的入口调用（真实 subprocess 已执行，但父进程 coverage 未记录该入口）。不降低阈值，不添加覆盖率豁免。

Backend 数字由主 agent 提供且不计入本报告的 Engine 分母：19626 passed、43 skipped。仓库 checker 把无 failure/error 的 19669 项显示为 19669/19669；这不等同于 19669 个实际通过用例，本报告没有独立复跑 Backend 全量或其门禁。

## 远端 ACI 状态

NOT RUN：本任务不创建或运行远端 job，本地结果不代表远端 ACI PASS。

## 结论

本地 Engine 回归 **PASS**，冻结待提交树的 ACI 兼容预检 **PASS**。最终 commit 门禁由主 agent 复核；模型/Relay 和远端环境未验证。
