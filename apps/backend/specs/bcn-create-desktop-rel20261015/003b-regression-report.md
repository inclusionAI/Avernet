# Backend 本地回归测试报告

## 结论

**PRECHECK PASS**。本次只修改 Backend Bot 生命周期编排与注入接线，未修改 engine/relay；采用完整 Backend community 测试与 ACI 兼容报告检查，未启动或部署真实 engine/relay/backend 服务，未改动线上 Bot。远端 ACI **PENDING**，须由编排器基于 PR 的真实 job 单独确认。

## 环境与版本

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-create-desktop-rel20261015`
- Base: `origin/REL20261015` @ `8d7effb48fd514d58d3d7086b0be64fb71b27891`
- Head: `838a2ce9a234cf82192c07a64c0b8a3240fc162d`
- 运行时间: 2026-10-15 18:19:01 +08:00 开始，206.82 秒。
- Python 3.12，8 xdist workers，DEPLOY_PROFILE=test，复用已安装依赖的旧 worktree venv；不执行 uv sync。
- 显式 PYTHONPATH 由当前 ci_test.sh 设置为当前 worktree 的 apps/backend/src 和 apps/backend。导入 community 的 origin、pytest worker 可执行路径、coverage source/XML 均核对为当前 worktree；共享依赖不意味着读取旧源码。

## 执行与结果

```bash
PATH="$PWD/apps/backend/.venv/bin:$PATH" \
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
bash apps/backend/scripts/ci_test.sh \
  --base origin/REL20261015 \
  --head 838a2ce9a234cf82192c07a64c0b8a3240fc162d
```

退出码 0；**22410 passed, 39 skipped, 0 failed, 0 errors**。原始 runner 75% 总行、80% 增量门槛通过。随后针对同一 XML/base/head 用 report_check.py 明确重验总行 >=70%、增量 >=90%，退出码 0。

| 指标 | 证据 | 判定 |
|---|---|---|
| 用例通过率 | 执行且未跳过用例 22410/22410=100%；JUnit total=22449、passed=22410、skipped=39、failed=0、errors=0 | PASS |
| 仓库 runner casePassRate | 22449/22449=100%；其实现分母包含 skipped，因此另列真实执行口径 | PASS |
| 总行覆盖率 | 113664 / 126832 = 89.62%，>=70%（亦>=runner75%） | PASS |
| 变更行覆盖率 | 31 / 33 = 93.94%，>=90% | PASS |

报告制品：`apps/backend/pytest_report/TEST-junit.xml`、`apps/backend/pytest_report/TEST-cov.xml`；本地完整输出 `/tmp/bcn-create-desktop-ci.log`（本机运行制品，不纳入提交）。

## 行为核验

完整 community 套件包含以下用例，均通过：

- create 引擎/类型矩阵覆盖 openclaw、hermes、moltis、deepseek_harness、claude_code、aicoding、teclaw 与 personal/desktop/service；openclaw personal/desktop 使用 plugin，其余省略 connection_mode 采用既有 gateway 默认值。
- BotService create 在 desktop/provision=False 提前返回前尝试注册；后续 provision 不重复注册；缺失/关闭 template capability、关闭旧 DRM 均不抑制创建注册。
- 注册 HTTP 失败不阻塞创建；环境配置跳过时不标记 bcn_registered；成功更新 ext 标记。
- 真实 DesktopBotService.create_after_authorization 路径经过 DesktopCreationLifecycle 注入 hook，最终 ext 写入后注册，成功标记与 passport 字段共同保留；不通过 HTTP router 添加领域副作用。
- desktop 创建重入成功 marker 跳过，未成功可重试；desktop restart 每次合法请求重试注册，即使已有 marker；openclaw desktop plugin 与其他 desktop gateway 均测试。
- start、同步 BaaS restart、独立 coding prepare 复用同一 resolver；durable restart intent 保持早返回，原 desktop 通用生命周期限制仍有效。

## 外部系统边界日志

既有 BcnService.register_provider_bot 的 HTTP 实现保持不变，并由本轮全量实际重跑 `test_registration_boundary_logs_business_fields_without_credentials`（200/409/500）。

- 出站事件 `event=request/success/failure`：system=BCN、direction=outbound、operation=register_provider_bot、POST/path、provider_id/provider_bot_ref、connection_mode、name/summary/owners；成功/失败包含 status、elapsed_ms，失败包含 error_type/reason。
- create 编排日志包含 bot_id、active_engine、bot_type、template_type、connection_mode；desktop 包含 `event=bcn_registration_requested/failed`、owner_id、引擎/模式、error_type。
- 请求/成功/失败事件与关键字段断言通过；admin token、runtime token、模拟异常秘密值不落日志的断言通过。沿用项目日志，不增加日志过滤基础设施。
- 未调用真实 BCN，故本报告证明本地行为及日志契约，不证明部署后某个 Bot 的实时入网状态。

## 未覆盖的变更行

`bot_service.py:1683`、`:1684` 为 create 最外层注册 helper 意外向上抛错后的保护分支。常规注册 HTTP 失败由 helper 既有 best-effort 异常分支处理并已测试；该外层保护未在本轮覆盖。其余 31 条可执行变更行均覆盖，保持 93.94% >=90%，未改阈值或排除规则。

## 范围与限制

- 39 项为套件现有 skipped，以 JUnit 为准，未将其声称执行通过。
- 没有真实容器部署/产品 QA/远端 ACI 的证据；远端最终结论由实际 PR job 给出。
- 已读取回归注册表与 memory；登记的历史 engine/relay WS/模型用例与本次 Backend-only 改动无关，未为它们启动共享服务。本次新行为已在仓库 Backend 测试持续注册，无需向 engine/scripts 添加与引擎无关的脚本。
