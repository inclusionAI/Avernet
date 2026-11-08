# Service evaluation environment API

Owner: Backend. Additive Service API and HTTP contract; no Plugin API, database,
configuration, or existing Quality workflow change. Governed by
`docs/arch/arch.rules.md` and the existing service-publication authorization seam
(`../2026-08-21-openapi-v1-collaborator-authorization-seam/spec.md`).

## Contract

`POST /openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish`

Uses the existing public identity layer and an explicit owner_id query locator. Application
admission is explicitly `AdmissionMode.OPEN`, not addressed-Bot grant admission.
Existing identity handling remains, but both operations intentionally use NoCheck:
no Bot collaborator-level authorization is required. Resource address validation
does not prove that the actor owns or collaborates on the Bot.
The HTTP adapter uses existing Bot and publication queries to validate the addressed
publication Bot PK, source Bot ID and environment before
submitting. Publication IDs are positive integers. An edit lock is not required:
this operation reuses a built artifact and does not edit the draft or its bindings.

No request body is required. URL query parameters:
- `owner_id`: required non-empty Bot owner locator, read directly from the URL.
  No current-user default, entity_id alias or grant-derived owner resolution is used.
  This locator is for resource lookup, not proof of caller authorization.
- `operator`: required non-empty string supplied by the caller as a business operator.
  It is not derived from or proof of the authenticated identity.
- `biz_id`: optional string, default `""`, for business correlation (not a new
  HTTP idempotency guarantee).

Example: `?owner_id=owner-1&operator=user-1&biz_id=task-1`.
Existing public identity handling remains independent of the operator field.
Template, artifact, image, tag and TTL overrides are not exposed.

Returns HTTP 202 with the existing Envelope and data fields `success`,
`publish_id`, `stage` (always `eval`), `bot_uuid`, `baas_publish_id`, and nullable
`baas_bot_status`. This acknowledges submission, NOT environment readiness.
The existing service auto-approves the workflow and schedules default TTL teardown
(currently 24 hours). It does not advance the main publication lifecycle or write
its binding. This endpoint does not create or advance a Quality task.

Missing/hidden Bot or publication, including cross-Bot/environment references,
returns the existing sanitized 404 envelope. Invalid parameters return 422.
Unmapped publishing errors (including missing build artifacts and provider/write
failures) propagate to the existing application 500 handler, never a success
acknowledgement. No new error classification is inferred from exception text.

## Implementation and compatibility

`openapi_v1/service_envs/router.py` HTTP adapter → PublishFlowServiceProtocol →
existing PublishFlowService.eval_publish. No new service, service protocol module,
or DI binding is introduced. The existing protocol declares eval_publish.

The HTTP route reuses BotServiceProtocol.get_bot_by_address and
BotPublishServiceProtocol.get_publish_by_id to validate resource addressing before
calling eval_publish with publish_id/operator/biz_id and an optional resource_spec.
Existing identity handling remains; PublicAPIRoute explicitly uses NoCheck.
EvalPublishMixin merges an optional resource_spec into a copied template_config;
no bot_address parameter is introduced.
Internal Quality callers are unchanged.
Neither publication facade is changed. Rollback removes the route and its
admission/authorization registrations; no data migration is needed.

## Validation

Dedicated adapter tests cover accepted payload, required operator and optional biz_id query parameters, OpenAPI
path constraints and sanitized not-found mapping. Adapter tests also cover cross-Bot/environment rejection before submission. Run existing lifecycle,
eval-publish, admission/authorization inventory and architecture suites as regression
checks. Live BaaS provisioning requires an integration environment.

### Local validation evidence

- Changed Python files pass AST parsing and the 1,000-line source limit.
- `git diff --check` passes; both publication facade files are unchanged.
- Resolved the missing PyJWT environment dependency by syncing the Backend lockfile
  with the dev dependency group into apps/backend/.venv (Python 3.12).
- Service environment endpoint tests and existing eval-publish external-parameter
  tests pass: 20 passed. Existing deprecation warnings remain.
- Admission and authorization inventory tests pass: 50 passed.

## Environment publish progress

`GET /openapi/v1/service-envs/env-publish/{baas_publish_id}/progress`

The only business input is the positive `baas_publish_id` returned by env-publish.
No Bot ID, publication ID, owner/entity locator, or request body is required.
The endpoint retains the shared public identity handling, OPEN admission and explicit
NoCheck. It deliberately does not verify Bot membership or workflow ownership:
a caller admitted by the existing public identity layer can query a known workflow ID.

The handler directly reuses PublishFlowService.get_baas_publish_progress with
include_devices=False, matching Quality TaskProcessor.to_env_ready. No new service,
repository dependency, or DI binding is introduced. HTTP 200 returns the existing
Envelope with the BaaS progress dictionary as data. Querying does not transition
Quality tasks or the main publication. Provider errors propagate through existing
error handling. The publishing endpoint and its resource validation are unchanged.

Adapter tests cover status pass-through, provider failure propagation, the single
positive path parameter, OPEN admission and NoCheck registration.


## Environment teardown

`POST /openapi/v1/service-envs/{bot_uuid}/env-teardown?operator=user-1&publish_id=7`

Reuses PublishFlowService.eval_teardown as called by Quality TaskProcessor.to_env_released.
The non-empty path parameter bot_uuid is the environment ID returned by env-publish,
not the source Bot ID. Query operator defaults to "system" and must be non-empty.
Query publish_id is a non-negative integer defaulting to 0; callers should pass the
originating Backend publication ID so manual release and the TTL safety net converge
on the same operation. No request body, owner ID, or new service is introduced.

HTTP 202 returns Envelope data containing success, bot_uuid, default_tag and message.
This confirms durable task submission only; it does not mean the environment is gone
and does not return a destroy workflow ID. No Quality task transition is performed.
Enqueue errors propagate rather than being swallowed as in the Quality cleanup caller.

Like the other service environment endpoints, admission is OPEN and authorization is
explicit NoCheck. Existing public identity handling remains, but the endpoint does
not verify caller ownership of the environment. operator is a business field only.
Tests cover forwarding, defaults, enqueue failure propagation, and API registration.

## Public contract and CI integration

All three routes explicitly depend on `require_principal`, including when the
service-env router is mounted independently. OPEN admission and NoCheck remain
unchanged: business operator/owner values are not verified caller identities.
These operations have no acting-user dimension and do not add a `user_id`
parameter. Publication keeps its required `owner_id` locator, distinct from the
optional caller-derived owner on engine-runtime operations. The public contract
inventories explicitly assert these differences rather than disabling their gates.

Gateway configuration declares `service-envs` as a Backend domain using the
existing Backend schema, with optional user/app/bot identity shapes matching
OPEN admission. A verified principal is still required by Backend. Existing URL
paths and service method contracts are unchanged; no new core service is added.
Deployment must ship the matching gateway configuration to route this domain.

Response fields carry descriptions, and curl examples use indented Markdown
rather than fenced code to comply with the published-document contract.
The endpoint framework executes real HTTP success/error cases for every route,
including unsigned-request rejection. Only long-running orchestration is replaced
through per-test DI; publication resource validation uses real repositories.

Validation of the CI integration fix (2026-10-29):
- Focused Backend contracts, adapter tests, admission/authorization inventories,
  endpoint coverage gate, no-mock gates, and the reported agentswarm regression:
  188 passed. The agentswarm failure did not reproduce; its code is unchanged.
- Registered environment HTTP cases: 9 passed (success, business/validation
  errors, and missing-principal rejection).
- Executable Backend source lines changed by this fix against its parent:
  15/15 covered. This is not the full PR merge-base coverage result.
- Focused Ruff check and `git diff --check` passed; changed Python files remain
  below 1,000 lines.
- Gateway YAML routing/security assertions passed in the Backend contract suite.
  Gateway's own config/architecture suites could not run successfully with the
  Backend interpreter: `pydantic_settings` and `pytestarch` are not installed.
- Full Backend suite and Singlebox E2E were not rerun locally.


## Per-deployment resource override

The publish endpoint accepts optional positive integer Query parameters `cpu`
(cores), `memory` (GiB), and `disk` (provider-defined capacity units/support).
CPU and memory must be provided together; disk is optional but cannot be passed
without both. Missing pairs, non-integers, and non-positive values return 422
before provisioning. Example suffix: `&cpu=4&memory=8&disk=200`.
This does not guarantee a 200 MB/GB volume: the storage provider must support the
existing disk field and determines its interpretation.

The adapter constructs the existing ResourceSpecification and forwards it via the
Service API's additive optional `resource_spec=None` argument. EvalPublishMixin
replaces the resource_spec entry in a copied template_config, preserving other
settings and passing it through existing release_async/BaaS plumbing. Omitting
disk in an override omits disk from the replacement resource specification; it
does not inherit the old template's disk. No source Bot or publication config is
persisted. With all three absent, existing configuration behavior is unchanged.
Existing Quality callers need no changes. No Plugin API or database change.
Tests cover real HTTP validation/forwarding and execution of the release callback,
including no template, existing template, no override, and source immutability.
