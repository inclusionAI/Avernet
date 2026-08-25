# PR 收敛报告：bcs-provider-attributes-columns

## 范围

- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/ocb_worktrees/bcs-internal-bot-attributes-dev/ocb-public` / `git@github.com:inclusionAI/Avernet.git`
- Head / base: `fix/bcs-provider-attributes-columns` / `dev_refactory_collaboration`
- PR: https://github.com/inclusionAI/Avernet/pull/1352
- PR title: `fix(bcs): persist provider bot attributes in columns`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| 本地验证 | PASS | SQLite migration 9 项、`bcs-bot-store` control-plane 14 项、`bcs-app-bot` 20 项、Provider 路由 39 项及 `git diff --check` 均通过。MySQL 012 已用 `bcs-admin db migrate --emit-sql --only 12` 解析；全量文件检查被 base 既有的两个 011 重号阻断。 |
| PR metadata | PASS | PR #1352 为 OPEN，head/base 与本任务一致；标题及 Problem / Solution / Validation / Compatibility and risk / Spec 段落已核验。 |
| 合并条件 | BLOCKED | GitHub 判定 mergeable=MERGEABLE，mergeStateStatus=BLOCKED；3 个 BCS 相关 CI check 失败，需要最小修复后重跑。 |

## 自动意见

第 1 轮查询未发现 review、普通 comment 或 inline review thread。

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| BCS e2e (coverage gated) | FAIL | GitHub Actions run 32565870679 | SQLite control-plane 查询 `user_visibility` 时，旧本地库尚未补列，报 `no such column`，Provider E2E story 提前停止 | 增加 SQLite schema repair 与 MySQL/OceanBase 增量迁移 | 待修复 push 后重跑 |
| BCS unit tests | FAIL | GitHub Actions run 32565870639 | 3,915 项测试均通过，但变更行覆盖率 31/41（75.61%），低于 80% 门槛 | 增加损坏物理列的 fail-closed 回归；移除不可能命中的 JSON 序列化错误转换 | 待修复 push 后重跑 |
| Singlebox coverage | FAIL | GitHub Actions run 32565870666 | 同一 SQLite 缺列使 E2E endpoint 覆盖仅 147/152 | 复用上述 SQLite schema repair | 待修复 push 后重跑 |
| Backend / Engine / BaaS / Gateway unit tests | PASS | 当前 PR head 的对应 GitHub Actions jobs | 与本次 BCS 属性列改动无失败 | 无 | 已通过 |

## 人工意见

第 1 轮查询未发现人工 review、普通 comment 或 inline review thread。

## 当前结论

- PR: OPEN
- 自动意见: CLEAR
- ACI/CI: FAIL（根因已定位并进入最小修复）
- 人工意见: CLEAR
- 下一步: 推送 SQLite/MySQL schema 补齐及回归测试，观察新一轮实际 CI。
