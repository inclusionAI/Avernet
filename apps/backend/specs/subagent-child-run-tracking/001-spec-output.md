# Child run identity completion: review specification

Date: 2026-11-01. Implements the user's request to track accepted subagent run
IDs in the Adapter, rebase onto GitHub dev, and deliver a source PR.

Detailed design and executable acceptance criteria:
`engine/adapter/specs/2026-11-01-openclaw-child-run-completion/spec.md`.

Existing chain: Gateway WebSocket -> OpenClawGatewayClient.chat_stream ->
OpenClaw plugin port -> OpenClawChatAdapter -> existing downstream transport.
Allowed changes: request-local child tracking and frame filtering/termination,
closest regression tests and task reports. No OpenClaw source, frontend, BCS,
runtime configuration, deployment, or external frame contract changes.

Review must verify child run/session identity, duplicate handling, bounded
pre-ACK/early-terminal buffering, no child-final forwarding, no parent-progress
decrement, parent final temporal boundary, mixed direct/announce fan-in,
compaction/inject/approval/ACK regressions and precise limitations.

QA is local event-driven simulation at the actual Adapter chat_stream boundary;
full Engine tests and remote CI provide independent regression evidence. Real
container behavior and model consumption are not claimed by these checks.

Delivery: preserve PR #3097 and original branch. Rebase a new topic branch onto
the freshly fetched GitHub dev and submit its own PR; do not force-push, merge,
close the old PR, or deploy. User explicitly authorized implementation/rebase/PR.
