---
agent: tc-review
status: completed
created: 2026-10-25
iteration: 1
---

# Spec summary: OpenClaw subagent completion state

The implementation contract is maintained at
`engine/adapter/specs/2026-10-25-openclaw-subagent-final-state/`.

## Scope

- Count only semantically accepted `sessions_spawn` results with complete child
  identity.
- Support both direct `announce:*` completion and OpenClaw's direct parent
  session resume path.
- Preserve session isolation, compaction, inject, hard-stop, early-final, ack,
  and approval behavior.
- Add diagnostic state-transition logs without recording message/tool content.

## Acceptance gate

- Rejected or incomplete spawn results never create a pending wait.
- A deferred parent final can resume on a trusted same-session continuation and
  terminate on the resumed run's final.
- Announce deltas do not consume pending children; foreign sessions cannot
  authorize a continuation run.
- Focused tests, full Engine regression, changed-line coverage, SAST, rebase,
  and GitHub PR checks must pass or have a documented pre-existing baseline.
