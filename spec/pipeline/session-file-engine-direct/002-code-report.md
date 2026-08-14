# Code Report

## Implementation

- Added Engine-owned `GET /api/session-files`, verified content streaming, and
  local-file/manifest deletion. The routes require `x-iam-token`, are checked
  against the requested session, and never accept a filesystem path.
- Added manifest stat observations, locked atomic manifest list/remove writes,
  and `missing`/`changed` availability. Content and Chat references rehash the
  file before exposing bytes or an absolute path.
- Added Backend `GET /api/session-resources/pending`; ready resources are
  intentionally excluded. Legacy Backend ready-file routes are marked
  deprecated, and a missing Engine file no longer queues re-materialization.

## Local Verification

- Engine: `23 passed` for direct data-plane, materialization, and Chat
  reference tests.
- Backend: `13 passed` for session resource service and HTTP router tests.
- Engine and Backend targeted Ruff checks plus `git diff --check` passed.
- An ASGI request against the assembled Engine app reached
  `/api/session-files` and returned its expected unauthenticated response,
  proving router registration without starting the local Engine lifecycle.

## Pending Gate

Deployment and real Bot interface evidence will be recorded in
`004-deploy-report.md` and `005-qa-report.md`. No target-Bot acceptance is
claimed until upload, ready transition, direct Engine operations, and Chat
reference tests all pass.
