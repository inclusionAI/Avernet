# OpenClaw subagent completion state

## Problem

`chat_stream` currently increments `pending_announces` whenever a
`sessions_spawn` tool result has `isError=false`. OpenClaw can still return a
business rejection such as `details.status="forbidden"`, so no child exists and
no completion event can arrive. For accepted children, OpenClaw can also queue
the child result directly back into the active parent session instead of
emitting an `announce:*` run. In both cases the foreground final is suppressed
forever and the caller eventually receives a timeout.

## Required behavior

- Count a pending child only when the tool result is semantically accepted:
  `result.details.status == "accepted"` and both `childSessionKey` and `runId`
  are non-empty strings.
- Preserve the existing `announce:*` completion path and its parent-session
  isolation.
- After a foreground final is deferred for an accepted child, treat the next
  non-terminal continuation from the same trusted parent session as delivery
  of one child result. Trust its run ID for the remainder of this stream and
  decrement exactly one pending child.
- Continue waiting when more children remain. Terminate normally on the final
  of the resumed parent run once the pending count reaches zero.
- Preserve hard-stop behavior for `error`/`aborted`, inject events, compaction
  retry handling, early-final handling, and timeout diagnostics.

## Security and observability

- An unrelated session or untrusted run must not consume another parent's
  pending child count or become an allowed run.
- Logs must distinguish accepted spawn, rejected/non-accepted spawn, deferred
  parent final, parent resumption, and timeout state without recording message
  content, credentials, or tool-result bodies.

## Acceptance tests

1. A `forbidden` spawn result followed by the parent final completes normally.
2. An accepted spawn with missing child identity is not counted.
3. An accepted spawn, deferred parent final, same-session continuation on a new
   run, and new-run final completes without an `announce:*` event.
4. Existing direct announce, foreign-session isolation, compaction, inject,
   hard-stop, and timeout tests remain green.
