---
agent: tc-review
status: implementation-authorized
created: 2026-11-12
iteration: 1
---

# Shared Teclaw Caller IAM refresh

## Requirement and baseline

A Caller-mode service may create chat sessions on its successful online Teclaw publication without allocating a separate Caller instance. Interactive IAM refresh must select that same existing shared runtime and install the authenticated Caller credential only for the Caller-owned session. An absent independent Caller instance must not turn this supported topology into a zero-target refresh failure.

Issue: https://github.com/inclusionAI/Avernet/issues/3951.

Public baseline: `inclusionAI/Avernet`, `REL20261112`, `cbfe8225d22da39696b7f01a47ed2d55f6b639d7`. Private host refs, incident identities and runtime evidence remain outside the public repository. This specification authorizes implementation and testing, not deployment.

Owning boundaries: [Caller identity](../../src/agentclaw/community/core/caller_identity/README.md), [runtime binding](../../src/agentclaw/community/core/runtime_binding/README.md), [Expert Chat](../../src/agentclaw/community/core/expert_chat/README.md), [repository architecture](../../../../docs/arch/arch.rules.md).

## Coding specification

### Existing chain

IAM HTTP adapter → Caller-first token orchestrator → `CallerIamTokenService` → trusted Caller context, authenticated identity, target selection → `CallerSessionValidator` → token exchange → existing runtime updater. Expert Chat already records sessions through `ExpertChatRepository.add_owned_session`; `get_owned_session` resolves the exact authenticated user/Bot/Owner/session tuple and filters ACTIVE/current environment. `RuntimeBindingResolutionService.AUTO` already resolves Teclaw service stage bindings rather than an independent Caller instance.

The existing `CallerSessionValidator.validate` intentionally only checks the nonempty session requirement for Teclaw. Keep that contract for existing callers. A newly admitted shared-runtime refresh needs an additional scoped ownership guard; do not claim the old validator already performed it.

### Rules and minimal implementation

1. Identify the shared Teclaw capability from server-resolved service/Caller Bot context, never a request-supplied boolean or session-key suffix. A small internal `CallerIamTokenContext` flag, defaulting false for existing direct consumers, is the preferred means of keeping non-Teclaw resolution behavior unchanged. The flag's producer must follow the existing canonical engine metadata/strategy and never infer a runtime provider solely from an arbitrary string supplied by the caller.
2. Admit the new shared branch for `online` chat refresh. Resolve its existing successful publication via `RuntimeBindingRequest(AUTO)` using the exact Bot, current Owner, authenticated actor, stage and environment. Confirm the selected active binding is actually `teclaw`. A stale/mismatched provider or missing publication is a real target failure, not permission to fall back to draft or another user's instance.
3. A shared request requires nonblank `session_key` and an ACTIVE Backend-owned session indexed by `get_owned_session(authenticated_user, bot_id, owner_id, session_key)` in the current environment. Missing/blank context returns `CALLER_CREDENTIAL_REQUEST_INVALID`; a missing, deleted or differently scoped row returns `CALLER_IDENTITY_FORBIDDEN` without disclosing which scope exists. No runtime session HTTP lookup or new persistence schema is required.
4. Perform this new ownership guard before requesting a credential. For shared targets, resolve again after signing and before installation; require unchanged binding ID/source and repeat provider/ownership checks. A publication/provider/engine switch rejects with an existing target error (`CALLER_TARGET_NOT_FOUND`) and sends no outbound installation. Do not silently redirect a signed credential to a replacement target.
5. Install exactly once through the existing `_exchange_target` and runtime updater with the selected binding and unmodified session key. Preserve the original IAM token response, Caller token subject check, APPEND semantics and per-session Teclaw overlay. Do not create a Caller instance or change base outbound rules.
6. Keep authentication and friend-subject checks before shared selection. An explicit friend request still requires the authenticated identity to equal the server-verified friend subject. Shared Teclaw online requests use the same exact session guard; other engines retain their existing explicit independent-Caller selection. Owner inspection does not authorize installation into another Human's session.
7. Draft/verify requests retain the existing Owner/current-lock-holder policy; ordinary Callers do not gain write access to those stages through this online shared-session fix. Temporary test exchange remains Owner-only/non-production. Non-Teclaw instance refresh, multiple-target aggregation, token exchange errors, provider errors and no-target failures retain their existing semantics.

### Key methods

| Method | Layer | Responsibility | Inputs / result | Effects |
| --- | --- | --- | --- | --- |
| `CallerIdentityService.get_iam_token_context` | Caller domain | Produce trusted shared-runtime capability with existing resolved Bot identity | Existing context inputs; additive internal context field | Existing metadata reads only |
| `CallerIamTokenService.get_iam_token` and narrowly scoped shared-target helper | Application use case | Choose the published shared target, validate before signing, fence before installation and preserve legacy branches | Authenticated actor, context, stage, exact session; normal `CallerIamTokenOutcome` or existing error | Read-only resolution; existing signing/install calls only after validation |
| `CallerSessionValidator.validate_owned_shared_session` (name may follow local style) | Caller domain | Confirm actual Teclaw provider and exact active owned-session row only for the new shared branch | Bot, Owner, actor, binding, session | Fixed repository reads; safe validated/rejected event; no runtime/network lookup |

The shared guard is separate from `validate`, so existing nonempty-only call sites and tests remain valid. Inject the existing `ExpertChatRepository` read port through the existing Caller DI module. Avoid a new framework, global resolver API, generic plugin capability, new HTTP route or private enterprise workaround.

### Domain models

`CallerIamTokenContext` remains an internal immutable value describing one server-resolved IAM request. If a capability flag is used:

| Field | Type | Required/default | Source | Invariant / compatibility |
| --- | --- | --- | --- | --- |
| `shared_teclaw_runtime` (final name may follow local convention) | bool | Default false for backward-compatible direct construction | Trusted existing service/Caller Bot context and canonical engine policy | Never a client authorization flag; success still requires actual provider, stage and owned-session checks |

Reuse `RuntimeBindingRequest`, `ResolvedRuntimeBinding` and the existing owned-session index without schema changes. The selected binding/source is immutable during a shared refresh; the session belongs to the authenticated Caller in the exact Bot/Owner/environment scope. Context evidence is not proof that a selected provider remains unchanged after signing.

### Allowed file scope

| File | Change |
| --- | --- |
| `src/agentclaw/community/core/caller_identity/iam_token_service.py` | Shared online target branch/fence within existing application use case |
| `src/agentclaw/community/core/caller_identity/session_validator.py` | Add separate precise shared-session ownership check |
| `src/agentclaw/community/core/caller_identity/contracts.py` and `service.py` | Only the internal trusted shared capability if needed; no transport schema |
| `src/agentclaw/community/di/modules/caller_identity_module.py` | Inject existing ExpertChat repository into the validator |
| `src/agentclaw/community/core/caller_identity/README.md` | Document shared-online policy and narrow ownership check; preserve legacy validation description |
| Corresponding Caller unit/DI/contract tests and real SQLite ownership tests | Regression and meaningful coverage |
| Existing Backend acceptance story module/registry | Only if required to cover changed Core behavior through the actual assembled service |

Paths above are relative to `apps/backend/`. Public runtime-binding semantics, Expert Chat allocation/creation, BaaS, Relay, Cron, WebSocket, generic HTTP clients, JIT and Singlebox orchestration are out of scope. No enterprise product patch is expected; the host should consume the verified public commit via gitlink. If implementation evidence requires another file, explain its necessity before extending this list.

### Diagnostics and external boundaries

Reuse existing logger and signing/install boundary events. Add stable shared-target selected/rejected/fenced events and shared-session validated/rejected events with Bot, current Owner/entity, authenticated Caller, stage, selected binding/source, environment/reason and plaintext business session key where relevant. Include correlation IDs when available. Never log IAM/Caller token values, Authorization/Cookie headers, credentials, secrets or raw upstream bodies.

Existing inbound `caller_iam_request_received` / response success/failure and outbound Caller signing/append events remain authoritative for request/response/status/duration. No new external operation is introduced. Tests must observe shared selection, rejection before signing, genuine append invocation on success, and absence of sentinel credential values in logs. Do not introduce recursive masking infrastructure.

## Review specification

- Verify the new branch fixes the supported shared online topology instead of suppressing `CALLER_OUTBOUND_UPDATE_FAILED` globally.
- Verify a correct public/enterprise composition can construct the service without corp dependencies or service locator use.
- Require exact authenticated session ownership before signing and again before installation; do not parse a user suffix as authorization.
- Require actual selected provider check, published-stage resolution, environment isolation and target fence. Preserve existing lock policy and other-engine target behavior.
- Require friend-subject mismatch, wrong Owner/entity, missing/deleted row, publication change and signing/install failures to remain errors without secret leakage.
- Keep modified source files below the repository size limit and clean unused imports/variables. No CI threshold relaxation, test skips, `no cover` workaround or no-assertion coverage padding.
- Run affected tests and architecture checks. Strict preflight: case pass100%, total lines>=70%, changed executable lines>=90%; changed modules should have meaningful >90% focused coverage. Report actual numerators/denominators and remote metrics separately.

## QA specification

| Case | Setup / operation | Expected |
| --- | --- | --- |
| Shared normal Caller | Caller-mode Teclaw service, online publication, Owner-held lock, no independent instance, active owned session | IAM200; one signing and one append for exact published binding/session; no instance allocation/query required by this branch |
| Shared Owner | Owner uses an owned online session | Same shared target and session-scoped install; no other user's session |
| Friend subject | Server-verified owned online session; request identity equals subject | Same shared validation; identity mismatch rejected before target/signing |
| Missing key | None/empty/whitespace session | Existing invalid-request error; no signing/install |
| Wrong scope | Key exists for another user/Bot/Owner/environment or row deleted/absent | Forbidden; no signing/install; indistinguishable rejection |
| Stale binding | Publication/provider/engine changes during signing | Existing target error; no append to either old or replacement binding |
| Missing publication | No successful online binding or inactive selected binding | Real failure; no draft/Caller fallback |
| Draft/verify ordinary Caller | Not Owner/current lock holder | No newly granted shared service write |
| Draft/verify authorized actor | Existing Owner/lock policy | Existing behavior preserved |
| Other engines | Independent Caller present, absent, or service/instance partial success | Existing target/aggregation semantics preserved; no new ownership lookup |
| Credential/install failure | Signing raises, invalid subject, append fails | Existing error response remains; no success conversion |
| Read-only ownership | Real SQLite rows across user/Bot/Owner/environment | Exact row only; reads do not create/index/migrate/provision a session |
| Logs | Success and each key rejection with sentinel credentials | Diagnostic fields visible, business session key retained, raw credentials absent |

Use the actual application service and runtime resolver with repository Protocol doubles for focused cases; add real SQLite repository isolation tests. A controlled signing/install boundary is permitted; do not mock the target-selection method being tested. Exercise the real IAM adapter/DI at least for shared success and owned-session rejection. Full local Backend tests and remote CI are independent from live provider acceptance.

## Delivery specification

1. Public implementation worktree and topic branch are based on freshly fetched authoritative GitHub `REL20261112`. Rebase onto that branch and open an English PR on `inclusionAI/Avernet` against `REL20261112`; never push public source to the internal fetch mirror.
2. Run focused tests/review first, then local full Backend checks and current-head remote CI, fixing attributable failures without weakening gates. Preserve test, security and coverage evidence per exact head.
3. After the public commit is independently available from the configured mirror, prepare the OCB release-base gitlink update and PR against OCB `REL20261112`. Record public PR/head, mirror SHA and host PR separately. Mirror unavailability is PENDING, not permission to substitute an unverified GitHub SHA.
4. No merge, deployment, instance creation or runtime rule edits are authorized by this implementation task. A PR being open or green does not prove the historical target was upgraded.
5. Rollback is a reviewed revert of the corresponding public behavior commit and/or host gitlink consumption; preserve all existing Caller policy while investigating.

## Implementation plan and acceptance evidence

1. Write the failing regression with owned shared online session, no independent instance and an unrelated lock holder; verify the current code returns a zero-target failure.
2. Implement the narrow shared branch, separate ownership guard, DI and target fence; make positive/negative tests pass.
3. Verify same old independent-instance, Owner/lock, friend and test-exchange behavior; run real SQLite isolation and no-write assertions.
4. Update owning Caller documentation, run architecture/focused/static checks and strict coverage, then independent review and full Backend CI.
5. Record exact PR refs, mirror/host integration and any missing live-provider verification in delivery reports. This specification itself is not execution evidence.
