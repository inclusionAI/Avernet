# Submission stage 1 — delivery foundation

This branch contains only the default-paused durable delivery foundation.
**Normal SessionResourceService deletion does not yet emit withdrawal events.**
The operator CLI and user-facing single-chat wiring belong to stage 2.

The neighboring spec, contract and runbook describe the final two-stage target,
not functionality already available in this foundation-only branch.

- Head: `codex/tc-resource-withdrawal-foundation`
- PR base: `dev`
- Follow-up head: `codex/tc-session-resource-withdrawal`
- Source/test/SQL additions: 1,477 lines (docs excluded).
- Isolated foundation verification: 135 regression + 314 architecture tests passed.
- Unified Singlebox: failed on the combined local run; repository CI is required.
- Delivery stays OFF; ECB, MySQL and UI release qualification remain open.

The user creates the merge request. No remote PR creation, merge, production
configuration change or service restart is authorized by this submission.
