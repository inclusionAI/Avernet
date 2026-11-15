---
agent: tc-review
status: implementation-authorized
created: 2026-11-15
iteration: 1
---

# Spec: select JIT targets using the authenticated Caller

## Scope and baseline

Public token-plugin context and Caller-first orchestrator. Baseline dev `5a126bd84f`. This public specification uses synthetic identifiers only. Private incident records, internal source URLs and production data stay outside the public repository. The existing flow reuses a resource/allowlist identity as the runtime actor; Caller and resource identities can differ, selecting an unavailable or incorrect Caller instance. This specification fixes only that identity propagation.

## Required identity contract

Separate three existing identities: resource `entity_id` addresses the Bot, `owner_user_id` is the Bot's actual owner, and `actor_user_id` is the verified current requester. Keep `user_list_entity_id` as the existing allowlist policy subject; this task does not change rollout policy. Never use entity/owner/allowlist identifiers as an implicit actor fallback.

`TokenExchangeContext.actor_user_id` is an additive context field carrying the exact verified `AuthPlugin.resolve_user_from_request(auth_request).staffId`, without case folding or numeric normalization. It is server-built and cannot be populated from request `friend_user_id`, client headers directly, or caller-supplied resource identifiers. If missing or empty (including legacy directly constructed contexts), runtime target resolution fails closed before selecting a Caller instance; it must not fall back to `user_list_entity_id`. Use an additive optional field with default `None` to preserve dataclass constructor compatibility; the enterprise consumer explicitly rejects absent/empty actors.

The orchestrator obtains this identity through injected `AuthPlugin` while building plugin context. Preserve Caller-first order and the exact original Caller success/error outcome even if auth/context/pipeline fails. Authentication exceptions or absent identity prevent plugin execution and produce safe diagnostics, never a fabricated actor. A mismatched `friend_user_id` remains subject to the Caller service's existing rejection; do not trust the friend value or mint JIT for the friend. If plugin execution proceeds under the existing Caller-first independent-plugin contract, its actor can only be the authenticated requester; do not change that contract incidentally.

Enterprise `CorpTokenRuntimeTargetResolver._resolve_binding` passes only the trusted actor to `RuntimeBindingRequest.actor_user_id`. Existing authorized Bot/owner/stage/environment resolution, instance status, ACTIVE binding and applied_by validation remain. Both match eligibility and prepare resolve the current Caller's target; preserve target-change/ARCA validation and Teclaw provider/engine skip. Do not change Caller token issuance, install/expiry coordination, timers, whitelist rules, frontend, DingTalk exchange, Owner policy, authorization or public runtime-binding semantics.

### Existing token_type wrapper compatibility

The current Caller service/protocol already accept `token_type="iam"` and the existing router passes non-IAM types. Keep the orchestrator compatible by accepting the same optional `token_type` argument and forwarding a non-default value to the existing Caller service (preserve default invocation compatibility). This is wrapper passthrough only: non-IAM requests skip the optional JIT pipeline for this task; a normal AuthPlugin identity does not establish the subject of a DeviceToken. Do not add DingTalk-to-JIT identity/exchange support, route changes or frontend/BCS behavior. Add a real-wrapper regression proving DingTalk type reaches Caller and does not create an unverified JIT actor.

## Methods and models

| Surface | Responsibility | Input/output and effects |
| --- | --- | --- |
| `TokenExchangeContext.actor_user_id` | Explicit verified actor, separate from resource/Owner/allowlist identity | Optional string with default `None` for additive constructor compatibility, populated with exact authenticated representation; no database/HTTP schema change. |
| `TokenExchangeOrchestrator` constructor/context builder | Inject AuthPlugin and resolve trusted identity from existing auth request before creating plugin context | Existing Bot/owner/type lookup plus authenticated staffId; resolution may be async; no direct plugin implementation selection. |
| Enterprise `_resolve_binding` | Reject absent actor and forward verified actor to existing authorized runtime resolution | Bot/owner/actor/stage/env request; read-only selection before JIT side effects. |
| Existing pipeline | Preserve independent-plugin failure isolation and original Caller outcome | No best-effort redesign or false success conversion. |

Existing fields `entity_id`, `owner_user_id`, `user_list_entity_id`, `bot_id`, `stage`, `env`, `publish_id`, `binding_id`, `request_id` retain their prior meaning and defaults. No table, migration, HTTP request parameter or new credential type is needed. Update the owning token-plugin contract documentation and consumer/conformance tests with the field semantics.

## Diagnostic boundary

Use existing logger. Add only needed identity-context failure/target-selection metadata: event, bot ID, owner/resource/actor distinctions where necessary, binding/stage/env, reason and correlation ID when available. No raw IAM/JIT tokens, authentication snapshots, headers, cookies or exception messages that may contain credentials. This adds an internal auth capability use and context propagation, not a new external HTTP endpoint; retain existing auth/JIT/BaaS structured boundary logs. Test success/error diagnostics, correct actor versus resource semantics, and absence of synthetic credential secrets.

## Core regression and acceptance

Construct resource entity E, actual Owner O and authenticated Caller B all distinct. Make both Caller A (the old entity-derived actor) and Caller B ready with ACTIVE isolated bindings and valid applied_by. Run the real orchestrator, public pipeline, actual runtime binding service, enterprise resolver/filter/JIT plugin and real BaaS append implementation with local repository doubles and `httpx.MockTransport` at HTTP boundaries. AuthPlugin is a controlled recording implementation returning B; do not bypass or mock the orchestrator/context/resolver under test.

Invoke twice with distinguishable synthetic JIT token responses. Assert two JIT HTTP requests and two outbound append requests reach B's exact physical ARCA device; A receives zero query-selected installation or updates. Assert x-jit-token, existing domains and append mode, and both first and renewed values in the HTTP capture. Distinguish local request submission from actual remote provider installation or rule deduplication; no live operation is authorized.

Also cover auth absent/empty/error, spoofed friend versus verified B, Caller required failure preservation, allowlist subject unchanged, unsupported/denied allowlist, Owner and personal existing behavior, Teclaw provider and legacy engine skip, non-ARCA prepare rejection, actual public and enterprise DI. Missing identity must cause no wrong-instance JIT HTTP/append. Public tests must remain corp-free; enterprise tests supply the cross-repository consumer proof.

Case pass rate must be 100%, total coverage >=70%, changed-line coverage >=90%; report exact fractions and skipped cases separately. Focused modified-file coverage target >90%; do not lower exclusions/thresholds or use assertion-free tests. Run affected contracts, architecture/DI tests, static unused import checks and `git diff --check`; run repository full required gates and inspect actual current-head PR CI.

## Review and QA red lines

Reject entity/Owner/friend fallback, direct trust in request metadata, case normalization, mocked tested methods, missing A/B competing-instance proof, changed whitelist subject, altered Caller outcomes, disabled Teclaw exclusion or loosened binding scope. Review source changes against exact dev base. Candidate cross-repository tests and locked dependency tests are independent evidence.

## Delivery boundaries

User authorized implementation, isolated worktrees, rebase latest dev and PRs; no deployment, merge or forced history rewrite. Avernet source goes only to GitHub inclusionAI/Avernet targeting dev; create source PR first. OCB is a genuine enterprise adapter code change, not a gitlink-only task. Its old fixed public SHA lacks the actor field and cannot prove integration. Record `DEPENDENCY_PENDING` until the candidate is available from the configured internal mirror and the paired integration update is made. Within the authorized paired implementation, include the corresponding gitlink only after exact mirror availability verification; disclose an unmerged source dependency. Clearly link that source PR and pending-merge status; a local object cache is not mirror-availability proof. Do not silently supply entity fallback to make locked-version tests pass. Keep source PR, candidate combination, mirror availability, gitlink integration, actual CI and deployment status distinct. PR English title/body require Problem/Solution/Validation; OCB default reviewers are verified separately. Never infer CI PASS from local tests or PENDING jobs.
