---
agent: tc-code-reviewer
status: completed
created: 2026-09-21T17:08:43+08:00
iteration: 1
---

# 代码评审报告

## 评审范围

- Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-file-count-rel20260922`
- 分支：`feat/service-bot-file-count-rel20260922`
- Base：`b2014be448b887068881ffafa7d9e09a1e4272ac`，对应 `github/REL20260922`。
- 已测源码树：`e445d39d6a4e43d538d0705289b3a0533bbb4226`，41 个改动文件（含文档与测试）。这是不可变 Git tree，不是功能提交 HEAD。评审时当前生产源码与该 tree 的 diff 为空。
- 读取 Review Spec、两个编码分报告、汇总报告、回归报告、前端合同及仓库架构规则。审查涵盖 staged/uncommitted 功能增量；未使用空 base..HEAD 作为覆盖率通过证据。
- Claude Code 与无物理根的内存 local OpenClaw 返回 unsupported，按主编排确认并已同步的最终 Spec/合同评审；生产 OpenClaw 文件端口提供计数能力。

## 逐条评审意见

| 维度 | 结论 | 说明 |
| --- | --- | --- |
| 正确性 | PASS | 管理授权先于运行绑定与实例调用；三阶段明确解析 CALLER_SERVICE；副本分别返回，失败 null 与成功 0 不混淆；畸形 provider 身份及 bool/负数响应被拒绝。 |
| 安全性 | PASS | 固定 HTTP 路由，参数不构造命令；扫描从根开始逐段 O_NOFOLLOW，目录 fd 与 inode 验证抵抗链接替换；不读取文件内容，不记录原始异常或连接凭据。 |
| 性能 | PASS | Engine 每进程两个扫描槽、无排队、10 秒截止；Backend 每请求两个实例调用、每实例 30 秒截止。扫描不生成文件清单；非原子快照限制已说明。 |
| 代码风格/架构 | PASS | 独立 Service/Plugin 合同与 DI；core 无新增 HTTP/具体插件/env 依赖；新 router 在 app 根注册，未修改既有 publish router；改动可追溯本功能。 |
| 测试覆盖 | PASS | 全量 XML 与新增行为测试覆盖授权、绑定、双 provider、扫描语义、竞态及真实进程退出；新增实现模块超过 90%。既有大型文件不声称全部超过 90%。 |
| ACI 覆盖率门禁 | PENDING | 本地对不可变 tree 的三项兼容检查通过，精确指标见下；最终功能 commit 复核与远端 ACI 未完成，不能标为远端 PASS。 |
| 静态检查 | FAIL（基线阻塞） | 本次全部改动 Python 文件独立 Ruff 检查通过，diff --check 通过；Backend 全模块 SAST 因 4 条既有错误失败。问题文件相对 base 无修改。 |
| 外部系统边界日志 | PASS | Backend 请求/响应/失败与 Engine 出站结果记录经过脱敏的业务字段；Engine 请求、结果、失败、取消及 cleanup 使用 request_id 关联。测试检查最终渲染日志与结构化记录；取消后回收实际进程。 |

### 独立执行与证据复核

- 预审独立执行 Backend 合同/runtime：32 passed；Engine 计数/router：33 passed。
- 冻结后独立执行 Engine 计数/router：61 passed，1 条既有依赖 warning，1.70 秒；未覆盖或改写全量 coverage artifact。
- 冻结后对全部变更 Python 文件执行 Ruff：All checks passed；`git diff --check base tree` 通过。
- 独立读取标准 JUnit/coverage XML 并运行仓库 `scripts/ci/report_check.py`。source-root 严格使用 `src/backend/src` 与 `src/engine/src`，与各模块 CI 一致，阈值 100/70/90。
- 全量测试由主编排/独立回归执行，review 复核 XML 与报告：Backend 19,626 passed、43 skipped、0 failed；Engine 2,845 passed、0 skipped、0 failed，5 deselected 沿用 community CI 现有配置。

### ACI 覆盖率证据

| 模块 | casePassRate（仓库脚本口径） | 总行覆盖率 | 变更行覆盖率 |
| --- | --- | --- | --- |
| Backend | 19,669/19,669 = 100% | 102,057/114,400 = 89.21% | 205/205 = 100% |
| Engine | 2,845/2,845 = 100% | 41,150/43,922 = 93.69% | 604/606 = 99.67% |

Backend 脚本将 skipped 包含在“无 failure/error”分子内；实际执行通过是 19,626，跳过是 43，不能把 19,669 写成全部执行通过。报告保留仓库既有计算口径，不修改阈值或排除规则。

Base/tree 同上。输入为 `src/{backend,engine}/pytest_report/TEST-junit.xml` 与 `TEST-cov.xml`。两次兼容检查 exit code 均为 0。远端 ACI job：**PENDING**；最终功能提交尚未产生，后续需核验生产源码与已测 tree 一致，再按真实 commit 重跑检查。

未覆盖变更行：

- `src/engine/src/engine/community/api/file/router.py:68`：插件抛 CapabilityNotSupportedError/NotImplementedError 的兼容转换分支，可补返回 501/unsupported 的行为断言。现有显式 FileCountError unsupported 与能力缺失路径已有测试。
- `src/engine/src/engine/community/plugins/file_count_worker.py:115`：作为 `__main__` 执行的入口调用；真实子进程测试已运行，但父进程 coverage 未计入此行。不添加 no-cover 或虚假覆盖。

### Review Spec 检查项

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | 权限先于 targets/连接/扫描 | PASS | anonymous 与普通协作者拒绝；ADMIN/super_admin 行为断言覆盖，未授权无 transport 调用。 |
| R-02 | 三阶段绑定及 CALLER_SERVICE | PASS | draft 与 verify/online fixture 使用不同绑定；现有 resolver 显式服务目标，无个人 Caller 回退。 |
| R-03 | 固定 provider 实例与严格协议校验 | PASS | BaaS device_uuid 固定，字段存在时不借 uuid 掩盖非法值；ARCA 复用可信绑定连接；错误码 allowlist、严格整数校验。 |
| R-04 | 方法职责与架构边界 | PASS | kernel 值类型、独立协议、领域编排及 provider 传输分层；装配集中 DI。 |
| R-05 | null/0、多副本、关联与错误分类 | PASS | 无副本总数；失败非成功 error_code；同 request_id 贯穿 Backend/Engine。 |
| R-06 | fd 防竞态及完整资源关闭 | PASS | 根祖先/目标链接被拒绝，子目录 inode 验证和后验父项检查；目录变化失败；finally 关闭 fd。 |
| R-07 | 超时/取消真实退出 | PASS | 实际子进程 returncode/PID 断言；重复取消、spawn 期间取消、SIGTERM 无效转 kill、并发 busy 与槽恢复均覆盖。 |
| R-08 | 根级 router 与旧接口兼容 | PASS | app 并列 include_router，旧 publish router 无增量；HTTP/DI 注册用例与旧接口全量回归通过。 |
| R-09 | 单测/合同/架构/lint/SAST/Singlebox | FAIL/PENDING | 单测、合同、架构与新增文件 lint 通过；Backend 全模块 SAST 基线错误阻塞；Singlebox 尚未执行，远端 ACI PENDING。 |

### 具体问题列表

#### 问题 1：Backend 全模块静态检查被基线错误阻塞

- 严重程度：交付门禁阻塞；非本次引入的功能缺陷。
- `src/backend/src/agentclaw/community/core/task/task_runner/client/__init__.py:22`：F821，返回注解中的 `TaskExecutor` 在模块作用域未定义。
- `src/backend/tests/community/core/workspace/test_path_factory_skills_dir.py:6`：E702，分号串接语句。
- `src/backend/tests/community/plugins/test_resource_tenant_guard.py:20`、`:22`：E702，分号串接语句。
- 主编排执行 `scripts/ci/python_sast_local.sh src/backend 1` 失败；review 独立以 Ruff F821/E702 复现全部 4 条，且这三个文件相对 base 的 diff 为空。
- 建议：由主编排决定是否另行做最小基线修复，完成对应验证后重跑全模块 SAST。不得删除门禁、降低阈值或把本次增量 lint 通过写成全模块通过。

## 整体结论

**结论：REJECT（当前交付门禁阻塞）**。

本次功能增量未发现尚未修复的阻塞性代码缺陷；预审提出的设备 ID 类型校验及取消/cleanup 关联问题均已修复并有行为断言。REJECT 原因为必要的 Backend 全模块静态检查仍失败，不能以问题来自基线替代通过证据。

必须完成：处理上述基线 SAST 阻塞并重跑检查；后续补 Singlebox、最终提交对应门禁和远端 ACI。未执行项目保持 PENDING，不得据此部署。

非阻塞建议：补充 router.py:68 的兼容异常分支行为断言；app.py 当前 992 行，未来功能应继续使用独立职责模块，避免超过 1,000 行限制。
