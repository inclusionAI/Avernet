---
agent: tc-engine-regression
status: completed
created: 2026-10-25
iteration: 1
---

# Engine regression report

## Result

**PASS for the changed OpenClaw gateway scope.** No service was started and no
deployment was performed.

## Independent focused run

The regression agent ran gateway `chat_stream`, chat ack/approval, and plugin
stream semantics together: **102 passed, 0 failed**. INFO-log assertions also
confirmed the accepted spawn, non-accepted spawn, deferred parent final,
parent-resumed, and foreign-session drop paths without message/tool-result or
credential bodies.

## Full-suite evidence

- First run: 4076 passed, 7 failed. Six failures exposed an attributable
  approval/ack call-site omission and were repaired.
- Final post-rebase OpenClaw client suite after repair and review fixes:
  130 passed.
- Final full Engine rerun excluding one proven baseline-only macOS ACLI
  process-group test: **4089 passed, 6 deselected**, total line coverage
  **95.55%**.
- The commit-based report gate passed with **100% case pass rate
  (4089/4089)** and **100% changed-line coverage (154/154)**.
- The ACLI test fails identically on an untouched detached `github/dev`
  worktree. It depends on Linux `/proc/<pid>/cmdline`, which is absent on the
  Darwin test host; no ACLI source is changed by this branch.

## Residual risk

The new frames are covered with simulated gateway events. A live OpenClaw
gateway/ARCA deployment was not part of this PR-only request, so production
frame-shape validation remains a deployment follow-up rather than local
evidence.
