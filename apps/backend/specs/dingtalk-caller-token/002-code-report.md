---
agent: tc-code
status: completed
created: 2026-11-14
iteration: 2
---

# DeviceToken exchange with existing HTTP locator parameters

Only `token_type=iam|dingtalk` is added to the existing GET `/api/v1/token/iam` contract. The route has no new user or owner query parameters, and the service has no expected identity arguments. Existing application signature authentication is preserved.

The service resolves Bot context with the existing locator and obtains its owner there. It invokes `exchange_dingtalk(device_token, bot_id, owner_user_id, task_metadata)` without an `auth_context`. The returned verified subject is used verbatim to select the exact Caller instance. The service does not decode or normalize source credentials or query identity values. Missing, blank or padded subjects/tokens and invalid or expired results fail before target installation. Existing session-key validation, exact Caller binding scope checks and binding recheck remain in place. IAM behavior is unchanged.

Diagnostics record safe derived owner/caller metadata, exact binding, stable error codes and elapsed time. Source/issued tokens, Cookie and signed application principals are excluded.

Validation for this revision:

- TDD: five expected failures reproduced the old query/assertion contract before source changes.
- IAM compatibility plus initial DeviceToken core/API regression: **71 passed**.
- Final DeviceToken core/API tests: **36 passed**, including schema-only source selector, malicious extra query IDs, real principal signature checks, exact verified subject with leading zeros, invalid result values, target/session/upstream failures and secret-free diagnostics.
- Ruff for changed Backend Python files and `git diff --check`: passed.
- Broader architecture, Caller identity and IAM/OpenAPI regression: **689 passed**; measured source lines **759/780 (97.31%)**.
- Final focused coverage includes the real context-permission projection; no new session-ownership enforcement is claimed. The existing validator checks required session keys; the exact Caller binding enforces caller scope.
- The final permission-rejection test was added after the 689-case run and passed in the fresh 36-case run.

This revision changes only Backend source/tests/spec/docs. No BaaS/BCN edits, deployment, live credential exchange or push were performed by this implementation step. The earlier full Backend run predates this API refinement and is not represented as a rerun of this revision.
