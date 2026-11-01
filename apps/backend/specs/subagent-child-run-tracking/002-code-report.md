# Child run tracking: implementation report

## Scope

- Existing chain and allowed/forbidden boundaries are in `001-spec-output.md`.
- `child_runs.py` owns request-scoped accepted run/session identities, bounded
  early terminal matching, terminal deduplication and exact v1 announce mapping.
- `gateway_client.py` observes registered child terminals before parent-frame
  filtering. Parent continuation authorizes a subsequent parent run, but never
  settles children. Child completion alone never releases an old parent final.
- Parent finals cover already completed direct children so mixed direct/announce
  delivery does not wait for the same child a second time.
- Existing tests use real unique child identities and actual v1 announce IDs;
  new tests exercise the public stream with asynchronous event injection.
- Per-child compaction retry state includes provisional pre-spawn/ACK frames,
  so intermediate finals cannot settle a child. A subagent session used as the
  parent remains a parent. Duplicate stale parent finals do not cover children,
  and old-run finals cannot terminate an already authorized newer parent run.
- Empty early-final fallback and run-ID promotion are disabled once children
  are registered; the child-aware continuation gate controls those runs.
- Superseded allowed run IDs cannot reclaim the active parent resume cursor,
  including through duplicate tool results. Both old-final rejection and valid
  current-final completion have explicit regression assertions.

## Diagnostics and security

Existing transport RPC send/response/error logs remain. State-transition logs:
`sessions_spawn_accepted`, `child_spawn_duplicate`, `child_spawn_rejected`,
`child_terminal`, `child_terminal_rejected`, `child_announce_final`,
`child_announce_ignored`, `child_compaction_final_suppressed`,
`stale_parent_final`, `subagent_parent_resumed`,
`subagent_parent_final_deferred`. Fields: parent/child run and session identities,
terminal state, duplicate/match result, pending children/announces. Message text,
child output and credential fields are not logged by the new state tracker.
New tracker logs include system, direction, operation, elapsedMs and the parent
business session key through the existing logger; no new outbound RPC is added.

Foreign terminal frames need an exact accepted child pair; foreign announces
need both trusted parent session and exact registered v1 announce identity.
The provisional terminal cache is bounded to 128 entries and cannot authorize
a child without its accepted spawn response. It is local to the stream.

## Limitations

Child terminal means execution completed, not model consumption. A fresh parent
final is still required. Unknown same-parent continuation after completed child
remains a temporal association; current upstream protocol cannot prove its
causal origin. Missing child frames fail closed at the existing timeout; there
is no speculative completion or new polling/replay RPC. No deployments run.

## Validation

Baseline OpenClaw client suite before modification: 130 passed. Initial new
36-test suite on the old implementation: 21 failed / 15 passed. Independent
review's mixed/early-final and superseded-run regressions: four RED cases, then
fixed. Final client regression: 178 passed, including 48 new cases. Independent
review/regression and post-rebase evidence are recorded in 003/003b/009 reports.
