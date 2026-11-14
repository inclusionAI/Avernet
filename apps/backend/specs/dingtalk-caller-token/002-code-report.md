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

## PR CI fixture repair

Two independent PR CI attempts failed an existing event-loop responsiveness test because its worker stopped waiting after 0.5 seconds before the probe was scheduled. A new control with an explicit 0.6-second probe delay reproduces that false failure against the old fixture.

The test fixture now rejects synchronous lookup execution on the event-loop thread before blocking. An offloaded worker waits for the probe handshake without a worker timeout; the test waits for lookup startup or early HTTP completion. Cleanup releases the worker, cancels unfinished tasks and gathers both tasks. A single outer 30-second watchdog protects against a hung test; no worker or startup latency threshold determines correctness. A separate probe marker prevents cleanup release from being counted as probe success. The original probe-before-lookup assertion remains.

Controls verify delayed-probe offload success, immediate inline-sync rejection, propagation of early request exceptions, and rejection of a response that never enters the lookup handshake. The old fixture fails the first two controls as expected.

Validation: the 26-case file passed three normal runs and three `n4` runs with coverage; each coverage run measured 183/184 test-file lines. The 36 DeviceToken core/API tests passed again. Ruff and Git whitespace checks passed. Only the test fixture and this validation report changed; production behavior and CI thresholds are unchanged. Remote CI remains pending for this focused repair.
