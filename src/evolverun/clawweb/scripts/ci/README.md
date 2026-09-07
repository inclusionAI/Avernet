# ClawWeb test CI

From `src/evolverun/clawweb`, use Node 20.19.0:

```sh
npm ci --no-audit --no-fund
npm run ci:build
npm run ci:check
node --test scripts/ci/run-tests.test.mjs
npm run test:ci
```

This workflow only needs this repository. Packages are discovered from the existing
workspace list. Build precedes tests because package exports reference `dist`.
The CI runner runs packages serially and retains every package result; an earlier
failure cannot be overwritten by a later success. Timeouts, missing reports and
failed suites return a nonzero status. A package with no tests is `NO_TESTS`, not
a passing test.

Reports: `test-results/summary.{md,json}`, `junit.xml`, `coverage/` (LCOV,
Cobertura and Istanbul JSON), and `packages/*/` (individual JSON/JUnit/HTML).
Coverage measures each package's own server/web/src source, including untouched
files, excluding tests, fixtures and generated output. Shared code executed only
through another package is not counted as that package's own coverage. Incomplete
coverage is labelled explicitly; do not interpret it as complete application coverage.

GitHub publishes the summary and uploads reports even after test failure. Only
the added test step is advisory during observation; install/build/check remain
required. No coverage threshold is introduced. Existing business test failures
must be tracked separately; this change does not repair or skip them.

## 本地统一入口

在本仓库 `src/evolverun/clawweb` 执行：

```sh
npm run test:local
```

自动安装依赖 → build → check → 所有包的 test/coverage。Public 使用 npm ci；
OCB 使用 npm install（组合 lock 不提交）。OCB 先沿用现有本地开发方式关联 Avernet；
此命令不 checkout、不切换分支、不复制源码。已有依赖和构建，只想重跑用例可用 `npm run test:ci`。
安装/构建/检查失败立即停止并明确 tests NOT_RUN；测试断言失败仍收集其他包，最终返回非零。
本轮目标是运行测试、暴露问题，不修业务用例，不要求全绿。
