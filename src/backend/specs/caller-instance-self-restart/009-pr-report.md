# PR 收敛报告：caller-instance-self-restart

## 范围
- Worktree / repo: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-instance-self-restart` / `inclusionAI/Avernet`
- Head / base: `rebase/caller-instance-self-restart-on-REL20260904` / `REL20260904`
- Base SHA: `3c3aeaf109638ea5e1d917a31dd0fd38d971e5ac`
- Current head SHA: `abb5fa5608e10d3e648005d938f3eef35bb3ce2a`
- PR: NOT_CREATED
- PR title: `feat(backend): allow callers to restart existing instances`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定
| 结果 | 证据 | 说明 |
|---|---|---|
| Rebase PASS | `github/REL20260904..HEAD` 仅 1 个提交 | REL 在下，本任务 topic 在上 |
| Local CI PASS | case 100%，line 88.55%，change line 100% | 使用 Python 3.12 `uv run` 完整执行 |

## 自动意见
| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 尚未创建 PR | - | PENDING | 等待 PR 创建 | - | - |

## ACI/CI
| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| 本地 casePassRate | PASS | 17127/17127 | - | abb5fa560 | 100% |
| 本地 lineCoverage | PASS | 88.55% | - | abb5fa560 | >=75% |
| 本地 changeLineCoverage | PASS | 44/44 | - | abb5fa560 | 100% |
| 远端 GitHub checks | PENDING | PR 未创建 | - | - | - |

## 人工意见
| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 0 | 尚无 | - | CLEAR | PR 未创建 | - | - |

## 当前结论
- PR: NOT_CREATED
- 自动意见: PENDING
- ACI/CI: PENDING（本地门禁 PASS）
- 人工意见: CLEAR
- 下一步: 提交报告、推送 GitHub head、创建以 `REL20260904` 为 base 的 PR。
