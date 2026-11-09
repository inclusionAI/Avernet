# Track OpenClaw child runs at the adapter boundary

## Scope and contract

Replace PR #3097's scalar pending-announces/resume-count heuristic with accepted
child identities `(childSessionKey, runId)`. Change only the Python adapter,
tests, and task documents; keep OpenClaw source and external frame formats intact.

A child execution completes on its matching chat final/error/aborted, with
run/session validation and idempotent transitions. A matched v1 announce final
is a separate result-delivery signal and may also settle its child. Neither a
parent continuation nor an arbitrary announce prefix completes a child.

Do not forward child frames as parent results. A child completion never releases
a previously deferred parent final. Require a subsequent parent continuation
and final, or correlated announce finals. A foreground final after all known
children completed can end the stream, including the same-original-run path.
For explicit announce fan-in, wait for every accepted child's announce final;
one announce must not finish the stream merely because all child runs ended.

The existing protocol does not prove model consumption. This change establishes
child execution completion plus a subsequent parent result; it does not claim
that a parent final proves all queued results were consumed. Unknown same-session
continuations require a deferred parent final and observed child completion;
before that they cannot steal the stream. The residual inability to distinguish
an unrelated same-session run after child completion is documented, not hidden.

## Model and transitions

- Child registry: exact run -> child session; completed run identities and
  announced identities remain until the request exits for duplicate rejection.
- Canonical session matching preserves current default-agent alias behavior.
- Exact announce identity: `announce:v1:{childSessionKey}:{childRunId}`.
- Bounded early child state handles frames arriving before the accepted spawn
  tool result, including compaction retry lifecycle/intermediate finals, without
  authorizing unrelated runs. The parent itself may use a subagent session key.
- Parent state: deferred final and actual post-final continuation. Child
  terminal decrements once; parent progress never decrements.
- Diagnostics: accepted/duplicate/rejected spawn, matched/duplicate child
  terminal, matched/unmatched announce, parent deferred/resumed/final, pending
  counts; record business IDs but never message/tool output or credentials.

## Acceptance and regression plan

1. Three children finish before the original parent final: original final passes.
2. Three children finish after a deferred final and resume once: resumed final passes.
3. Duplicate spawn/terminal/announce, wrong session/run and foreign nested child
   do not change another child's state or authorize parent completion.
4. A child final never reaches downstream; last child completion alone never
   flushes an old parent final. Same-run continuation after a defer works.
5. No completed child: unrelated same-session assistant/approval/final is ignored.
6. Announce fan-in uses real registered identities and deduplicates each child.
7. Child error/abort settles only that child; parent hard stops, ACK ordering,
   approvals, inject, compaction and empty early-final behavior remain tested.
   Duplicate old parent finals cannot update child coverage or end a newer
   resumed run. Registered children disable generic early-final run promotion.
8. Validate RED before implementation, focused OpenClaw/client and full Engine
   regression, lint/SAST, production file limits, and changed-line coverage.

## Delivery

Use existing task worktree, create a new topic/rebase branch, rebase onto freshly
fetched GitHub `dev`, push without force, create a source PR to GitHub dev, and
observe actual checks/review comments. No deployment, merge, or old-PR closure.
