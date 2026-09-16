# Publish-ignore management implementation

## Scope and existing call chain

The existing publish HTTP router authenticates a caller and calls a service. The service resolves the source Bot and its exact publication/stage binding. A runtime plugin routes to a pinned BaaS replica or resolves the ARCA binding and calls the Engine Bot HTTP router. The Engine writes only its fixed local ignore file.

Allowed additions: request/contract types, a thin endpoint in each existing router, a task-specific application service and runtime plugin, DI wiring, runtime identity projection, tests and documentation. No changes to tar/cp, publication state transitions, generic HTTP clients, Relay, Cron, or remote deployments.

## Implementation

- Backend `/api/service-bot/publish/ops/publish-ignore` accepts Bot ID, entity ID, draft/verify/online stage, add/remove operation and one literal path. Version selection has been removed; see the current 001 spec for the revised contract.
- Existing collaborator authorization remains the single source for Bot management permissions; platform administrators bypass that per-Bot restriction, but anonymous requests never do. Entity ID is only a lookup constraint. Authorization precedes device enumeration.
- Binding selection reuses RuntimeBindingResolutionService.resolve with CALLER_SERVICE; draft uses the Bot binding and published stages use the shared current-stage rules. Connections reuse DeviceContextResolver.resolve_for_binding_invoke with pinned BaaS replica UUIDs.
- BaaS calls pin `device_uuid`; ARCA calls use the selected binding's trusted connection URL/headers and never call BaaS enumeration. Per-target results retain partial failures and unknown delivery outcomes. A second snapshot detects observed target changes; it cannot promise membership never changed between snapshots.
- Backend signs each per-target payload with Ed25519. Only Backend receives the private PEM key; Engine receives the public PEM key. The signature covers target identity, operation, path, request ID and timestamp.
- Engine checks the signature and time window, reloads managed runtime identity, takes a bounded fixed sibling lock, and atomically updates the fixed ignore file. A persistent, bounded request journal prevents repeated signed requests from restoring a subsequently removed rule. Failed operations are retried through Backend with a fresh request ID.
- File service preserves unrelated rules/comments and CRLF, uses literal paths, rejects symlinks/nonregular/oversized files, and leaves the old file intact if replacement fails. Add-existing and remove-absent are successful no-ops.
- Docker startup changes were withdrawn at the user's request. Engine consumes the identity already supplied by the deployed startup script; the existing daas bootstrap saves entity/version metadata. Missing identity still fails closed.

## Changed areas

| Area | Purpose |
| --- | --- |
| Backend existing publish router/schema | HTTP input and response mapping only |
| Backend publish-ignore contracts/service/plugin/DI | Authorization, exact target selection, provider routing and signed delivery |
| Backend Context Boundary README files | Declare the added service/plugin seams |
| Engine core publish-ignore models/protocol | Typed mutation contract |
| Engine plugin and DI module | Signature/identity checks and fixed-file mutation |
| Engine existing Bot router | Structured inbound boundary logging and response mapping |
| Engine credentials | Read runtime identity supplied by the existing bootstrap; no Docker dispatcher changes |
| Backend/Engine tests | Permissions, providers, edge cases, signatures, file safety, protocol and DI behavior |

## Boundary observability

Backend events: `backend.publish_ignore.request`, `response`, `failure`, `engine_request`, `engine_response`, `engine_failure`, `http_failure`. Engine events: `engine.publish_ignore.request`, `success`, `failure`.

Events carry request correlation, the real operator where available, exact target, literal path/operation, provider/binding/replica, status, results and elapsed time. Authentication objects are excluded; upstream arbitrary exception text/bodies are not blindly logged. File contents are never logged; responses provide rule count and SHA-256 revision. Tests inspect success/failure logs and ensure raw reusable authentication material is absent.

## Validation status

Historical initial checks: Backend 35 cases and Engine 44 cases passed; the nine Docker-dispatcher tests were subsequently removed together with the withdrawn script change. Historical full Engine suite: 2,692 passed, 5 pre-existing corp-only deselections, 93.50% total line coverage. The local report command initially selected an old system Python; re-running with the project Python 3.12 passed the case and total-coverage gates.

The first complete Backend run found four actionable gate failures (new endpoint registry coverage and contract import direction), with 18,755 passed and 43 existing skips. All four were corrected without new exemptions or lower thresholds: shared command/error/target values now live in `kernel.publish_ignore`, the new endpoint has registered happy/error scenarios, and consumer-to-runtime contracts exercise actual service/DI resolution. Review also added restart-in-progress rejection and a post-delivery publication-binding snapshot check.

Final local complete suites at feature commit `f21ea371b` against base `e12a495a2`:

| Gate | Backend | Engine |
| --- | --- | --- |
| Test execution | 18,781 passed; 43 existing skips | 2,692 passed; 5 existing corp-only deselections |
| Repository case-rate gate | 18,824/18,824 (includes skipped cases by existing checker semantics) | 2,692/2,692 |
| Total line coverage | 89.16% | 93.50% |
| Changed executable lines | 210/211 = 99.53% | 395/396 = 99.75% |

Both changed-line results were explicitly checked against 90%, not merely Backend's default 80%. Legacy Skill compatibility, Python SAST block-rule scans for both components, shell syntax and diff whitespace checks passed. Remote PR checks are a separate gate and remain pending until recorded in `008-aci-report.md`.

## Rollout and limits

Set `SERVICE_BOT_PUBLISH_IGNORE_SIGNING_KEY` only in Backend and `SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY` in Engine using trusted secret/config management. Missing keys or runtime identity fail closed. No secrets are supplied or committed by this change.

The cp consumer is a separate `agentclaw-daas-scripts` delivery. This Avernet PR does not ship that repository. Updating the ignore file only affects later installations after that consumer is deployed; it neither restarts the Bot nor deletes files. Rules live in the instance home, not a per-version namespace, and are not automatically propagated to new replicas. Actual NAS semantics and deployed-device black-box verification remain rollout work, not claimed local results.
