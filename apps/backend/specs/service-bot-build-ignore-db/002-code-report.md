# 编码与验证报告：service-bot-build-ignore-db

状态：实现和本地验证通过，待提交与远端 PR 门禁。

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`
- Branch: `feat/service-bot-build-ignore-db`
- Base: `github/dev` @ `7d39e392b99d8bf8351b029c3128d97d1c230411`
- 范围：DB/API规则管理、构建三条复制链路排除、发布快照、对应测试和文档；不修改 Engine、DaaS 或旧实例接口。

## 已验证

- 修改前构建、恢复、实例规则相关基线：93 passed。
- 修改前 repository/API/conformance/module-boundary 架构基线：171 passed。
- API/DB worker：187 passed；详见 `002-code-api-report.md`。
- 新 HTTP 持久化回查用例先失败于路由404；集成后真实 SQLite 增删/幂等/权限验证通过。
- 构建聚焦回归：144 passed，含真实 rsync 文件树排除、额外根映射、补拷跳过和重试残留清理。
- 发布快照经真实 BuildStageRunner、PublishExtState、BotPublishService 和 SQLite repository 落库回查通过，原有 ext 字段保留。
- 第1轮全量发现8个测试接线/登记问题，修复旧fixture依赖、GET失败登记、将含producer stub的状态推进测试移至领域集成测试目录；没有降低门禁或修改无关业务。
- 第2轮全 Backend：19533 passed、0 failed、43 skipped（19576 collected），227.23s。总行覆盖率101652/113986（89.18%）；变更行254/257（98.83%），独立90%门禁通过。跳过项不计作已执行测试。
- 相关生产文件静态检查与 `git diff --check` 通过，详见独立review/regression报告。

## 兼容性与上线顺序

- 先应用 `core/service_bot/sql/2026_09_20_bot_build_ignore.sql` 再启用代码（自动建表环境由schema注册处理）。
- 原实例规则接口不变，不自动迁移容器规则；历史版本重启/回滚不重新读取当前构建规则。
- 新日志仅用于规则管理和构建排障，覆盖成功/失败及异常凭据不泄露；详见分项报告。

## 待完成

- 以正式 commit SHA 复核门禁、rebase 与 GitHub PR/ACI；远端状态 PENDING，未部署。
