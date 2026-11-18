---
agent: tc-review
status: completed
created: 2026-11-18
iteration: 1
---

# BaaS Caller status specification

## Requirement and delivery scope

A verified BaaS service must be able to read another user's independent Caller allocation and live health without a user-level application grant, owner identity or chat membership. It must still prove its service identity. An unauthenticated request cannot query private Caller state. Querying never creates, upgrades, restarts, publishes, exchanges credentials or writes an instance/binding.

The original read-only status implementation is still in open PR #3914 and is absent from GitHub `dev`. This worktree carries those seven prerequisite commits rebased onto `github/dev` at `d24f0e1ed2d240a54fe475d7acb03465948dbada`. The new complete PR must link #3914; do not close it or claim it is merged. Source push/PR uses `inclusionAI/Avernet`, base `dev`. OCB and its gitlink, deployment, production requests and real Caller creation are excluded.

## Existing chain and architectural choice

- Ordinary HTTP already has `POST /api/v1/expert-chats/app-caller-connection`, authenticated by `require_baas_caller`.
- That service credential is an HS256 token in `X-Avernet-Principal`, requiring `iss=baas`, `iat`, `exp`, validating optional `nbf` with the existing verifier leeway. It uses the existing configured principal signing key. It deliberately ignores `principals`, `app_id`, `tenant` and `aud`; issuer distinguishes protocols, not parties holding the same signing key.
- The public GET uses `PublicAPIRoute`, `UserIdDep`, addressed application grants and ExpertChat chat authorization. Public identity admission expects a verified principal identity set. A BaaS service token is deliberately not that identity set.
- Add a flat GET beside the existing ordinary-HTTP BaaS operation, rather than expanding the global public identity/admission/grant machinery. This exposes the same read-only capability through a BaaS-specific path; it does **not** make BaaS credentials a new exception on the public GET itself. This path distinction must be explicit in documentation and delivery.

**Selected BaaS endpoint:** `GET /api/v1/expert-chats/app-caller-instance/status`.

**Public endpoint retained:** `GET /openapi/v1/bots/{bot_id}/caller-instance/status`, unchanged authentication, grant and chat policy.

Allowed changes: existing ExpertChat ordinary router and its dedicated BaaS auth dependency, owning README, focused signed-HTTP tests, real endpoint framework cases, acceptance and Singlebox route denominator. Reuse `ExpertChatInstanceServiceProtocol.get_caller_instance_status`; no new lifecycle method, DI provider, generic identity protocol, JWT implementation, persistence schema or child router.

## Coding specification

### HTTP contract

Required nonempty query strings are `bot_id`, `owner_id`, `user_id`. They name the exact source Bot and the target Caller user; they are not derived from an end-user principal. The BaaS endpoint uses the existing ordinary `ApiResponse` envelope. Successful `data` contains exactly two required boolean values:

| Field | Meaning | Invariant |
| --- | --- | --- |
| `exists` | Exact scoped Caller ledger has a nonempty string `ext.bot_uuid`; released instances remain registered | Missing record/invalid UUID returns false without provider access |
| `alive` | Fresh read-only provider result has an ACTIVE healthy device and is not RELEASED | `alive` implies `exists` |

Call the existing instance service directly after BaaS verification, without the public ExpertChat service's `_check_chat_access`. Instance service must still resolve the Bot and its real owner in trusted server tenant/environment, then read only `(bot_id, actual owner, user_id, env)` ledger. Never substitute a shared draft/online runtime. Existing engine/provider semantics remain, including Teclaw shared-runtime distinctions; this query does not allocate an independent instance for any engine.

Authentication absence, malformed/expired/not-yet-valid token, bad signature/key/algorithm, wrong issuer or unconfigured verifier yields existing uniform HTTP 401 before service/DI work. Missing/empty required query values yield 422 after valid service authentication. Domain Bot-not-found/permission failure and provider failure must follow the ordinary ExpertChat `ApiResponse` error style consistently (existing permission code 403; sanitized generic operation failure code 5999 unless the router already provides a more specific established mapping). No upstream exception detail or token is exposed. Failure must not become a successful false/false observation.

### Authentication and scope

Reuse the existing configured `gateway_principal_signing_key` and shared JWT decoder/config, pinned to `issuer=baas` and `verify_audience=False`, with no new secret/config field. Do not accept `Authorization: Bearer`, a cookie, a user query flag, an unsigned issuer claim or a header claiming a BaaS name as proof. A correctly signed ordinary `gateway` or `bcs` token must not enter this service path.

Use trusted ordinary HTTP tenant/environment middleware only. JWT/query values `tenant`, `principals`, `app_id`, `aud` cannot change repository routing or establish a different user/owner context. A verified BaaS service may address another user's private Bot Caller without any BotAppGrant or collaborator lookup; this is the intentionally authorized service capability. Ordinary public requests retain user-id equality, addressed-Bot application grant and chat access checks. BaaS tokens must not acquire access to unrelated public endpoints.

The current `require_baas_caller` logs `operation=app_caller_connection`. Reuse verification through a minimal internal helper and route-specific dependency wrapper (or an equally narrow existing mechanism) so status authentication events identify `app_caller_instance_status`. Preserve existing connection dependency signature and behavior. Do not expose an `operation` argument as a FastAPI query parameter. Add appropriate `# COSEC:` comments at the trusted service boundary and where claims are deliberately not projected.

### Key abstraction

| Method | Layer | Responsibility | Inputs / outputs | Dependencies and side effects |
| --- | --- | --- | --- | --- |
| Existing shared BaaS verification with a status-specific dependency | HTTP adapter | Verify service token and refuse before query work; select diagnostics only, not data tenant | HTTP connection to success/401 | Shared decoder/config; no domain writes |
| `get_caller_instance_status` (existing) | ExpertChat instance Service API | Resolve exact source Bot and Caller ledger; read live provider health | Required bot/owner/user strings to `{exists, alive}` or existing domain/provider error | Repository reads and safe provider GET only |

No new domain model or persisted field is necessary. The existing response projection already expresses the required observation. Do not widen required fields to optional types or alter shared lifecycle contracts.

### Boundary observability

Use existing logger. Record stable status-specific authentication request/success/denied and operation request/success/failed (permission denial distinguishable), with system=backend, direction=inbound, operation, GET/full route, request ID where present, trusted tenant/env, bot/owner/user, response status/error code, both non-sensitive output booleans and duration. Auth failure logs only stable exception type/reason, never raw decoder/JOSE input.

The reused instance service already records provider request/success/failure with exact UUID/engine/health-check flag, duration/status and safe health projection, using `log_response=False`. Preserve that behavior. Never dump headers, service JWT, signing key, Cookie, full upstream response or exception text that may contain credentials. Do not add a generic logging/redaction framework.

## Review specification

Reject authentication based on unverified claims, any application grant/user-id/chat bypass accessible to ordinary public callers, cross-tenant claims projection, container lifecycle calls/writes, shared-runtime substitution, swallowed upstream errors or any credential logging. Confirm private Bot/other Caller success after real BaaS verification; verify ordinary endpoint behavior remains unchanged. Domain policy stays in the instance service, transport stays in the router. Keep modified source files under the existing 1,000-line gate (split only the owned responsibility when necessary); check unused imports/variables and `git diff --check`.

Required local evidence: signed HTTP boundary tests; relevant existing BaaS connection/public status/domain suites; full OpenAPI adapter tests and entire `tests/community/framework` gate; real `ENDPOINT_CASES` happy/error registry; complete Backend behavior regression before first final push. Record coverage accurately, targeting changed-line >=90% without exclusions or threshold changes. Remote Backend and Singlebox are separate final-head gates; PENDING is not PASS.

## QA specification

| Case | Expected result |
| --- | --- |
| Valid `iss=baas` token, private Bot, unrelated caller, no app grant/membership | Query accepted; exact two booleans; no public chat/grant check |
| No ledger / ledger with invalid UUID | false/false, no provider or writes |
| Allocated live / inactive / released Caller | true/true or true/false according to existing real health semantics |
| Missing/empty/malformed credential, wrong key/algorithm, tampered JWT | Uniform 401; service not invoked |
| Wrong/missing issuer, missing iat/exp, expired/future iat/nbf | Uniform 401; no service work |
| Unconfigured signing key | Fail closed |
| Token has foreign/malformed principals, app/tenant/aud and request has tenant query | Valid service token cannot change trusted tenant/environment; no leaked claim values |
| Required parameter missing/blank | 422; no query |
| Bot absent / upstream failure | Established non-success envelope, no false health success; sanitized failure log |
| Ordinary public human/mixed user names another user | Existing 403 |
| Ordinary public app lacks addressed grant / caller lacks chat access | Existing masked 404 |
| Logs on success/auth denial/provider error | Correct status-specific operation and scope, all credentials absent |
| Exact instance snapshots before/after successful/error observations | Identical; no upsert/update/create/upgrade/restart/publish/identity exchange |

Framework cases must exercise real DI/SQLite routes and signed headers, not mock the handler/core or add baseline exemptions. Maintain real Singlebox acceptance for the new BaaS path and declare it in `singlebox_coverage_modules.yaml` router denominator. Include authentication failure and no-instance success without container creation; add pending/registered released fixtures as needed to preserve original ExpertChat Core coverage floor. Verify existing connection behavior and existing public behavior together.

## Ship specification

Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/baas-caller-status-20261118`. Initial topic `feat/baas-caller-status-20261118`; create final rebase result topic before first publish, with latest GitHub dev underneath all task commits. Resolve only task conflicts, preserving latest dev routing counts and unrelated documentation. No force push, merge, PR closing or review replies. PR title/body English with Problem/Solution/Validation and prerequisite #3914 relationship. Run secret scan and task-appropriate checks, push nonprotected branch with `--no-verify` per user tc-pr rule, observe final-head reviews and all actual CI jobs, fix real failures. Record logs in root TeamClaw `log.md`; reusable lesson in `OPTIMIZE.md`. Deployment and production QA are outside this request. Rollback reverts this additive service endpoint/auth wrapper, retaining ordinary public protection.
