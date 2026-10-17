---
agent: tc-code-reviewer
status: completed
created: 2026-10-15T18:19:39+08:00
iteration: 1
---

# 代码评审报告

## 范围与整体结论

**代码评审 PASS；正式 ACI evidence PENDING。** 本结论允许进入后续全量回归与 PR 门禁，不代表远端 CI 已通过。

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-create-desktop-rel20261015`
- Base: `origin/REL20261015` / `8d7effb48fd514d58d3d7086b0be64fb71b27891`。
- Head: `838a2ce9a234cf82192c07a64c0b8a3240fc162d`；测试针对随后冻结到该提交的任务修改执行，包含新增 DI 测试。
- 输入: `001-spec-output.md`、`002-code-report.md`、AGENTS.md、架构规范、reviewer skill/memory；`/tmp/CODE_REVIEW.md` 不存在。
- 现有链路: BotService create/provision 与独立 DesktopCreationLifecycle / DesktopBotService.restart；新增范围仅生命周期注册编排、共享 mode resolver、既有 lifecycle hook 和延迟依赖接线。
- 无 BCS、schema、runtime、router 或部署改动。capabilities 和 live-task 测试辅助文件仅修正本次删除资格 helper 后失真的直接相关描述/测试 mock。

## 固定维度

| 维度 | 结论 | 证据 |
| --- | --- | --- |
| 正确性 | PASS | 普通、deferred、BotService desktop early return 均在持久化和初始化后注册；provision 不重复。真实 Desktop 在最终完整 ext 写入后注册，避免 marker 被覆盖。 |
| 安全性 | PASS | 沿用 owner-scoped user_id；不使用可能 staff_ 前缀的 binding.entity_id 作为 owner_workno；HTTP 凭据仍由 BcnService 管理，新增日志仅记录业务字段和异常类型。 |
| 性能 | PASS | 每次新建一次注册，无新增遍历/数据库扫描；成功 Desktop 创建重放通过 marker 跳过；restart 明确每次尝试。 |
| 架构/风格 | PASS | 真实 Desktop 入口通过既有 lifecycle hook 接线，复用既有 Callable[[], BotService] provider；TYPE_CHECKING 避免运行时 import 循环，DI 实测解析同一共享实例。未引入新 HTTP 客户端或凭据机制。 |
| 行为测试 | PASS | 独立最终 296/296 passed，含相关 create/desktop/start/restart/BCN transport/DI 与模块边界、文件大小架构测试。独立覆盖运行 291/291 passed。 |
| ACI | PENDING | 已有本地冻结 head，尚无 PR 或远端 job；focused 文件覆盖不是全量 ACI。后续必须独立验证三项门禁。 |
| 静态检查 | PASS | ruff `--preview --select F401,F841,E203,E231,E265` 对修改源码及新增/核心测试通过；git diff --check 通过。无新增未使用 import/变量、冒号前空格或 block comment 告警。 |
| 外部边界日志 | PASS | BcnService 既有 event=request/success/failure 保留 method/path/provider_bot_ref、mode、状态和耗时；新增 Desktop requested/failed 事件复用共享 helper，不打印完整 ext/凭据。相关 BCN 日志测试及注入敏感值的 Desktop 测试通过。 |

## Review Spec 逐项

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | 所有 create 路径 | PASS | 七引擎 × personal/desktop/service deferred matrix、normal create、真实桌面授权后创建均有行为断言。 |
| R-02 | capability / DRM | PASS | create 移除历史资格 gate，respect_drm=False；环境凭据 skip 仍由原 BcnService 决定，skip 不标成功。service 本身创建资格规则未被放宽。 |
| R-03 | plugin / gateway | PASS | openclaw personal/desktop 返回 plugin，其他返回 None，wire 省略后沿用 gateway；真实 Desktop create/restart 和 coding BaaS preparation 都有 payload 断言。 |
| R-04 | 幂等和 marker | PASS | 普通 deferred 后 provision 不重复；Desktop retained success 不再注册，missing marker 重试；真实持久化 fake 验证完整 ext 和后续 polling 更新保留 marker。 |
| R-05 | 最佳努力 | PASS | 原 helper 保留异常语义，Desktop callback 额外捕获 provider/调用异常；失败不阻断创建或重启且不泄露异常正文。 |
| R-06 | 生命周期规则 | PASS | 保留 BotService desktop restart 拒绝、专用 Desktop owner/binding 检查、BaaS/审批失败语义与 durable restart 早返；Desktop 注册位于 BaaS 接受/审批成功之后。 |
| R-07 | 依赖与范围 | PASS | 复用 composition-root 延迟 provider，真实 injector 测试证明无循环；仅删除新孤立资格 helper/import/mock 并修正其直接相关陈旧描述。 |
| R-08 | 日志 | PASS | Desktop 生命周期日志带 bot_id/owner/engine/type/mode，BCN 原边界日志负责请求/响应/失败，运行时 token 只打印 presence。 |

## 独立验证与覆盖诊断

- 最终独立 pytest: **296 passed**, 17 个既有依赖 deprecation warnings，3.62s；JUnit `/tmp/bcn-create-review-final-junit.xml`。
- 独立测试范围: `test_bot_service_create_bcn_register.py`、`test_desktop_bot_service.py`、`test_bot_service_stop_start.py`、`test_bot_service_restart_baas_envs.py`、`test_bcn_service_register_provider.py`、`test_desktop_bcn_wiring.py`、`test_no_oversized_modules.py`、`test_module_boundaries.py`。
- 局部 coverage 同前六文件: **291/291 passed**，四模块行覆盖 **1245/2770 = 44.95%**。这是 focused 诊断，BotService 大量不相关行为未执行；不能当作全量 lineCoverage 门禁失败或通过。
- 通过 diff 新增行与 coverage.xml 可执行行交集计算本地候选变更覆盖: **31/33 = 93.94%**。未覆盖 `bot_service.py:1683-1684` 为外层防御性 callback-exception 日志；底层 helper 的最佳努力失败及真实 Desktop provider 异常均有行为断言。
- 覆盖文件 `/tmp/bcn-create-review-coverage.xml`，日志 `/tmp/bcn-create-review-cov.log`。正式 ACI 不使用这些 focused 分母替代全量结果。
- 静态检查及 diff whitespace: PASS；源码行数 BotService 6294、DesktopBotService 1925、DesktopCreationLifecycle 448、desktop_bot_module 74。前两者已有 allowlist，本次未新增豁免，架构文件大小检查通过。
- 早期独立运行发现两个 passport 期望遗漏 `status=ISSUED` 的测试失败，已修正并在上述最终运行验证；实现完成前新增 Desktop restart 的四个 RED 亦已在最终运行通过。

## 正式 ACI 证据

- Base / Head: `8d7effb48fd514d58d3d7086b0be64fb71b27891` / `838a2ce9a234cf82192c07a64c0b8a3240fc162d`（后续 rebase/push 后需核对实际 PR head）。
- casePassRate: 远端分子/分母 **PENDING**，阈值 100%。
- lineCoverage: 远端 covered/total **PENDING**，阈值 >=70%。
- changeLineCoverage: 远端 changed-covered/changed-lines **PENDING**，阈值 >=90%。
- 远端 job: **PENDING**。必须由回归与主编排取得实际最终 base/head 和 job 证据。

## 兼容边界与非阻塞事项

- 无历史批量回填或 BCN 既有 mode 迁移；已有记录是否接受 mode 变更遵循既有 BCN 注册语义，不能把请求中 mode 修正表述为历史记录已迁移。
- 旧版本创建后仅调用 provision_bot 的 pending 记录不会得到已移动的 create 注册步骤，后续受支持 start/restart 仍尝试；该边界已在 spec/编码报告说明。
- 无必须修复项。继续全量本地回归、rebase 与最终 PR/ACI 门禁。
