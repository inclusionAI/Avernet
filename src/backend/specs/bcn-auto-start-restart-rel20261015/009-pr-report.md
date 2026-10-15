# PR 收敛报告：bcn-auto-start-restart-rel20261015

## 范围
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-auto-start-restart-rel20261015
- Source repo / push remote: GitHub inclusionAI/Avernet / origin (git@github.com:inclusionAI/Avernet.git)
- Initial base: REL20261015 @ 1ce85adc687991db91ca610a9dfaf16bb5d82382
- Development topic: feat/bcn-auto-start-restart-rel20261015
- Planned rebase head: rebase/bcn-auto-start-restart-on-REL20261015
- PR base: REL20261015
- PR: https://github.com/inclusionAI/Avernet/pull/2742
- PR title: feat(backend): register all bots with BCN on start and restart
- PR description sections: Problem / Solution / Validation / Compatibility and risk
- Human comments: auto
- Scope: only start/restart automatic registration; OpenClaw personal uses plugin, other combinations use existing gateway default. Create retains original eligibility and DRM behavior. No runtime deployment or automatic merge.

## 自动意见
首轮已查询reviews、issue comments与inline comments，均为空。标题与完整英文说明已回读核验，包含Problem/Solution/Validation/Compatibility and risk。

## ACI/CI
- Local: independent Review PASS; 549 focused tests passed; full Backend 22,270 passed / 39 skipped / 0 failed or errors.
- Local coverage (base 1ce85adc6, source head ac821fa20): lines 113569/126726=89.62%; changed executable lines 14/14=100%. Same XML validated with project Python 3.12 after host Python2 report-check selection error.
- Rebase: completed against freshly fetched GitHub origin/REL20261015@1ce85adc6, source topic commits e80901492 and ac821fa20.
- Remote: PENDING，已创建PR #2742；首次head=973b9290de84bd7ab9f205503a106b670fedac12。
- Jobs: Unit Tests https://github.com/inclusionAI/Avernet/actions/runs/38681119779 ，Singlebox https://github.com/inclusionAI/Avernet/actions/runs/38681119857 ，E2E https://github.com/inclusionAI/Avernet/actions/runs/38681119860 。当前均尚未终态。
- Required evidence: actual executed tests, line coverage numerator/denominator, changed-line coverage numerator/denominator; no fabricated job results.

## 当前结论
- PR: OPEN
- 自动意见: CLEAR（首轮）
- ACI/CI: PENDING
- 人工意见: CLEAR（首轮）
- 下一步: 持续观察实际PR head的评论和jobs，修复可复现失败。
