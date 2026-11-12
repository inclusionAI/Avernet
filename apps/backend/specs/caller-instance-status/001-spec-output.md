---
agent: tc-review
status: completed
created: 2026-11-12T10:00:00+08:00
iteration: 1
---

# Caller instance status OpenAPI specification

## Requirement and scope

Add a read-only public API that reports whether the named user's independent Caller Bot has been allocated and whether it is alive. The existing internal `POST /api/v1/expert-chats/caller-connection` can allocate, upgrade, restart and persist instances, so it MUST NOT be called to implement or validate this query. Its supplied production URL is reference material only.

The task creates an isolated Avernet worktree, tests the change, rebases the topic onto the latest GitHub `inclusionAI/Avernet` `dev` (dev below, topic commits above), and opens a GitHub PR against `dev`. OCB is excluded when it would only change its Avernet gitlink. Deployment, production mutation and real Caller creation are outside this task.

## Existing chain and permitted additions

- Public delivery: existing `/openapi/v1/bots` router, `PublicAPIRoute`, `Envelope`, `UserIdDep`, `OwnerIdDep`, application-grant admission and collaborator authorization tables.
- Domain: `ExpertChatInstanceService` owns the independent-instance ledger and consumes `ExpertChatInstanceRepository` and `BaasService`.
- Persistence: `get_instance(user_id, bot_id, owner_id)` reads the exact `(bot_id, owner_id, user_id, env)` record under current tenant/environment. Resolve legacy entity addressing to the Bot's real owner first.
- Runtime: query the ledger's `ext.bot_uuid`, never the source Bot's draft/online binding. A shared Teclaw runtime does not become an independent Caller instance merely because the source Bot has `call_type=caller`.
- Allowed additions: one flat endpoint in the existing Bots router, a small response schema, a thin chat-authorized ExpertChat Service API entry plus an independent-instance read method, explicit route-policy inventory entries, focused tests and owning-domain documentation.
- Forbidden additions: child router solely for this endpoint; provisioning/restart/upgrade or publication polling; ledger/binding writes; generic HTTP or log framework changes; Relay/Cron/BaaS implementation changes; OCB worktree/gitlink.

## Coding specification

### HTTP contract

`GET /openapi/v1/bots/{bot_id}/caller-instance/status?user_id=<subject>&entity_id=<addressed entity>`

Use the existing `OwnerIdDep` contract: `entity_id` is the current published spelling, `owner_id` remains its deprecated alias, omission means the verified acting user. The original reference's `owner_id` remains accepted through this shared dependency. `user_id` is required. No body, force-upgrade or create flag is accepted or needed.

The standard public `Envelope` remains unchanged. Its `data` contains exactly:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `exists` | boolean | yes | The scoped independent Caller ledger has a nonempty string `ext.bot_uuid`, meaning a Caller Bot was allocated. A ledger row that has not yet received a UUID is not an allocated Caller Bot. |
| `alive` | boolean | yes | A current read-only provider observation finds at least one active healthy device for that exact Caller Bot. It is always false when `exists` is false. |

`exists` denotes allocation/registration, not current running-device count. A released or recycled registered Caller remains `exists=true, alive=false`; empty device lists never change existence. No ledger row, missing/malformed `ext`, or absent/empty/whitespace UUID returns `false,false` without an upstream call. Health missing/unknown/false and pending/updating/released devices do not prove liveness. Do not use historical ledger `status=success` as proof of liveness.

Use `BaasService.get_bot(bot_uuid, health_check=True, engine_type=<resolved Bot engine>)` for the existing provider health query. This does not provision or revive resources; the provider may refresh its own health observation. Healthy ACTIVE devices use the established BaaS `status` and `health` fields (`health == "true"`); reject transitional/released devices even if stale health is true. Reuse the existing health-status mapping only if release handling remains explicit and correct. Upstream errors propagate through existing public error mapping rather than falsely reporting absence/death. BaaS's existing 404-to-RELEASED behavior yields registered-but-not-alive.

### Authentication and access

Reuse verified public principal and tenant middleware. Human and mixed user+application credentials must name their authenticated `user_id`; mismatches remain 403 and missing user remains 422. Application-only callers require a live addressed-Bot grant for the named user and resolved owner. Register the route in `admission_routes.py` as `GRANT_CHECKED_ADDRESSED_BOT`, and declare the corresponding standard dependency. Register the domain-enforced chat-access boundary in `authorization_rules.py` using the existing `ServiceChecked` mechanism and cite the implementing service. The current checkout has no `PermissionLevel.CHAT`; its permission enum is NONE/MEMBER/ADMIN/OWNER. A generic `Check(MEMBER)` would incorrectly deny ordinary users of a public service Bot: `get_operable_permission_level` grants ownership, explicit collaborators and live Space membership, but has no public-Bot exception. The service must enforce the existing ExpertChat chat rule: actual Bot owner, public Bot (`public == "1"`), or collaborator with MEMBER access. Expose a thin `ExpertChatService.get_caller_instance_status` application entry that reuses its existing `_find_chat_bot` and `_check_chat_access`, then invokes the injected instance-service read method. Cite `core.expert_chat.services.expert_chat_service` in the `ServiceChecked` row. This preserves the existing collaborator dependency and requires no new DI or duplicated access policy. Do not alter the general Public API gate or fabricate a new CHAT enum for this endpoint. The `ServiceChecked` MEMBER metadata records the private-Bot collaborator bar; public/owner chat exceptions remain explicitly documented and tested in the service. This API does not introduce an administrator bypass or grant permission to inspect another subject's Caller instance. Missing/inaccessible Bot and ungranted app remain masked by the existing public 404 policy.

### Key method abstraction

| Method | Layer | Responsibility | Inputs / outputs | Dependencies and side effects |
| --- | --- | --- | --- | --- |
| `ExpertChatService.get_caller_instance_status` | ExpertChat Service API and core | Resolve the Bot, reuse existing owner/public/collaborator chat permission, delegate exact Caller observation using the real owner | `user_id`, `bot_id`, `owner_id`; two booleans or existing domain errors | Existing Bot/collaborator reads and injected instance service; no new DI |
| `ExpertChatInstanceService.get_caller_instance_status` | Instance Service API and core | Read exact scoped Caller allocation and observe provider health | `user_id`, `bot_id`, real `owner_id`; `{exists: bool, alive: bool}` | Instance repository read and BaaS health GET only; no local writes/lifecycle calls |

The HTTP handler performs parsing, existing principal/grant handling, service invocation and envelope conversion only. Allocation and health interpretation remain in the service. Both owning Protocols document their respective semantics independently; the adapter injects the existing flat `api/expert_chat_service.py` contract, and the ExpertChat service consumes its already injected instance-service Protocol.

### Model and invariants

`CallerInstanceStatus` is an observation DTO, not a persisted entity. Both booleans are required, no optional/default uncertainty field is introduced, and `alive => exists`. The ledger and device models/schema do not change. Internal UUID, binding ID, connection credentials and full provider payload never appear in public `data`.

### Boundary logs

Use existing logger and shared public access logging. For the new inbound operation record request, success and failure with operation, GET/route, request/trace ID where available, tenant/environment, subject, Bot and owner, duration, result/status and the two response booleans. The provider query must have an outbound request plus success/failure event including UUID, engine, health-check flag, duration and non-sensitive interpreted health fields. Never dump complete provider responses or headers: credentials may be embedded in runtime details. Existing simple credential masking is sufficient; add no generic redaction infrastructure. Tests assert events and relevant fields and verify raw sample credentials are absent.

### Expected files

| Area | Change |
| --- | --- |
| `adapters/http/openapi_v1/bots/router.py` | Flat GET endpoint; stay below 1,000 lines |
| `adapters/http/openapi_v1/bots/schemas_caller_instance.py` | Small DTO; existing `schemas.py` is already 980 lines |
| `adapters/http/openapi_v1/admission_routes.py`, `authorization_rules.py` | Mandatory route policy inventory |
| `core/expert_chat/expert_chat_instance_service_protocol.py`, `expert_chat_service_protocol.py` | Add and document read-only Service API methods |
| `core/expert_chat/services/expert_chat_service.py` | Thin application entry reuses Bot lookup and chat access, delegates exact caller observation |
| `core/expert_chat/services/expert_chat_instance_service.py` | Implement scoped read and provider-health projection, below 1,000 lines; no duplicate authorization or new dependencies |
| Owning ExpertChat and/or public API README | Publish exact two-field semantics and no-provision boundary |
| Focused public-route, service and route inventory tests | Behavioral and contract coverage |

## Review specification

Reject any lifecycle invocation, ledger/binding mutation, returning connection information, shared-online fallback, subject/owner substitution, missing public policy inventory, raw credential logging, swallowed upstream failure, or HTTP dependency in core. Require required/non-optional types end to end and minimum changes only. Inspect all modified source file sizes (maximum 1,000 lines), unused imports/variables, `git diff --check`, Python slice spacing and `# ` comment style.

Local checks must include real HTTP router/dependency behavior, domain-unit tests, public admission/authorization inventory suites and related ExpertChat regression. Measure changed-line coverage with genuine behavior assertions; the task's local target is >90% changed lines, with total module coverage recorded separately. Remote checks are distinct: case pass 100%, total line >=70%, changed line >=90%, or exact repository gates when stricter. PENDING is never PASS. Do not weaken thresholds, add blanket exclusions or use assertion-free tests.

## QA specification

| Case | Setup / operation | Expected behavior |
| --- | --- | --- |
| No ledger | Exact scoped key absent | `false,false`; no BaaS or any writes |
| Unallocated ledger | `ext` missing/malformed or UUID invalid/blank | `false,false`; no provider call |
| Healthy Caller | Registered UUID, current active healthy device | `true,true`; provider receives exact UUID and engine |
| Recycled Caller | Registered UUID, RELEASED/404 provider result or empty devices | `true,false`; no revive or re-registration |
| Unhealthy or unknown | false/missing health; pending/updating/released device with stale true | `true,false` |
| Multiple devices | Unhealthy plus one active healthy device | `true,true` |
| Stale ledger state | success ledger with unhealthy provider | `true,false`, proving live observation is used |
| Scope isolation | Other user/owner/env/tenant or only source shared binding exists | Never report that other/shared runtime as this Caller |
| Principal mismatch | Human or mixed user+app names another subject | Existing 403 before service query |
| Missing principal/user | No verified auth / no user_id | Existing 401 / 422 |
| Public chat caller | Authenticated subject has chat access via public Bot, but no editor or Space membership | Caller status succeeds; no generic MEMBER gate rejects this user |
| Private chat denial | Unrelated user targets a private Bot | Masked refusal before provider access |
| Collaborator failure | Private-Bot collaborator lookup raises | Fail closed, no health query |
| App grant | Valid and missing/wrong-owner grant | Authorized result / existing masked 404 |
| Missing/inaccessible Bot | No Bot or insufficient existing chat relation | Existing masked 404; no provider call |
| Upstream failure | BaaS read raises | Existing public error, failure log, no false success |
| Contract | Successful HTTP response/OpenAPI schema | Standard Envelope; data exactly two required booleans |
| Logging | Success and failure with sample credentials in test doubles | Request/result/failure evidence exists; credential text absent |

All cases use local fixtures/test doubles; do not invoke the supplied production lifecycle URL. Explicitly assert `upsert_instance`, `update_instance`, `create_bot`, upgrade, restart and publish polling are not called by the read method.

## Ship specification

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-instance-status-20261112`.
- Initial topic: `feat/caller-instance-status-20261112`; use a fresh rebase result branch if required by the invoked rebase skill.
- Source remote and PR: GitHub `inclusionAI/Avernet`, base `dev`; internal mirrors are not push/PR targets.
- Fetch latest `github/dev`, commit only task files, rebase topic atop that verified SHA, rerun affected checks, push non-protected topic with `--no-verify`, create English PR title/body and observe actual head-specific checks/comments.
- No OCB gitlink update, deployment or production writes. Backout is reverting the additive endpoint and its policy/contract entries.
- Record task changes in TeamClaw root `log.md`; retain focused implementation/review/regression/PR reports under this task directory. Report unresolved remote gates accurately.


### Narrow provider logging adjustment

The existing `BaasService.get_bot` unconditionally logs complete provider data,
which may carry credentials. Permit a backward-compatible keyword-only
`log_response=True` option that forwards to the existing `_get_bots_api`
response-log switch. Only the new status query supplies `False`; in this mode
provider HTTP/transport failures also omit raw bodies/exception text. Existing
callers retain their default behavior. This is required to make the new read
boundary safe, not a general BaaS lifecycle or logging refactor. Verify the real
BaaS read with MockTransport success, release/404, 503, API error, malformed JSON
and transport-error credential sentinels.

## Public schema inventory follow-up
- The additive Caller GET is one explicit-user-scoped and Bot-path operation. Keep exact schema-inventory assertions synchronized: user-scoped 299, Bot-path 227. This maintains the existing strict count gate.
