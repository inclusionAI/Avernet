# BCN OpenAPI V1 — Implementation Strategy

- **Date:** 2026-07-30
- **Status:** Approved (brainstorm)
- **Branch:** `bcn-openapi-batch-2` (contract PR #621)
- **Related:** design `docs/plans/2026-07-28-bcn-openapi-v1-design.md`; implementation plan `docs/plans/2026-07-28-bcn-openapi-v1-implementation.md`; PR #514, #621

## Context

Contract PR #621 defines the 27-operation BCN OpenAPI V1 phase-one surface (5 Group ops from PR #514 + 22 new across GroupParticipant / Session / SessionParticipant / Invitation / Friendship). Four rounds of Codex review on #621 are resolved:

1. `DeleteResult` unified to `{deleted}` — group Rust synced (`87997ffc`).
2. Session participant mode restricted to `BotParticipantMode{auto,muted}`, `actor_kind=const bot`.
3. Session message sender modeled as `MessageSenderKind{bot,human,system}`; friend-request `message` removed (`9aca2977`).
4. Session `context` replaced by `SessionInput{query?}` (group-context fallback); list operations declare stable ordering (`ddb84bd3`).

This document specifies the **Rust implementation strategy** for the 22 new operations. The 17-task implementation plan already details per-task TDD steps; this strategy adds batching, sequencing, scope boundaries, Principal-transport handling, comment follow-up integration, and test strategy. It references the plan, does not rewrite it.

## Strategy Overview

Four PRs, serial order — test-only vertical slices + one production-rollout PR:

| PR | Scope | Plan tasks |
| --- | --- | --- |
| #P1 | GroupParticipant (3 ops) | 3 (GroupService participant methods) + 5 (use case) + 10 (routes) |
| #P2 | Session (10 ops: 7 Session + 3 SessionParticipant) | 3 (SessionService + SessionMessageService traits) + 6 + 11 |
| #P3 | Invitation + Friendship (9 ops) | 3 (InvitationService + FriendshipService traits) + 7 + 12 |
| #P4 | Rollout: mount + gateway + principal + compat + E2E | 13, 14, 15, 16, 17 |

Each slice: `application::v1` trait (if absent) → domain use-case facade over legacy core + V1 authorizer → `bcs-api-http` thin route → route unit tests with an injected test `PrincipalVerifier`. No production bootstrap mount in slices (Task 13 reserved for #P4); slices are end-to-end testable via the test verifier but not production-reachable (matches PR #514 state).

PR base: slices build on contract PR #621's contract commits (`bcn-openapi-batch-2`); after #621 merges, each slice rebases to `dev`.

## Sequencing Approaches

- **A (chosen) serial:** #P1 → #P2 → #P3 → #P4, each slice based on the previous slice's merged `dev`, independent PR, TDD. Session depends on group domain, Invitation on group+session; serial avoids parallel merge conflicts.
- B parallel: 3 slices in separate worktrees — domain-dependency cross-cuts cause merge conflicts.
- C single PR: contradicts the agreed batching.

## Slice #P1 — GroupParticipant

Operations: `POST /groups/{group_id}/participants`, `PATCH /groups/{group_id}/participants/{actor_id}`, `DELETE /groups/{group_id}/participants/{actor_id}`.

- **Trait:** extend `GroupService` with `add_participant` / `update_participant` / `delete_participant` + command types. Reuse legacy `Participant` / `ParticipantMode` / `ParticipantRole`.
- **Use case (`bcs-group-v1`):** V1 authorizer → delegate legacy `GroupCoreService` `add_participant` / `update_participant_mode` / `remove_participant`; role invariants (originator/driver non-removable before transfer), no actor-kind branching.
- **Routes (`bcs-api-http/routes/group.rs`):** 3 thin handlers; `CreateParticipant` / `UpdateGroupParticipantRequest` DTO mapping.
- **Tests:** use-case authorization matrix (originator/driver/manager manage; plain participant read-only; Human/Bot same rules; required-role non-removable) + route tests.
- **Comment follow-ups:** group `DeleteResult{deleted}` already synced (`87997ffc`); `UpdateGroupParticipantRequest.mode` keeps full 4-value `ParticipantMode` (group participant can be Human or Bot). No new follow-ups.
- **Dependency:** PR #514 group V1 foundation (authorizer + GroupService + routes + test verifier) ready.

## Slice #P2 — Session (most comment follow-ups)

Operations: 7 Session (create/list/get/update/delete/completion/messages) + 3 SessionParticipant (add/update/remove).

- **Trait:** new `application::v1::session::SessionService` + `application::v1::message::SessionMessageService`.
- **Use case (`bcs-session`):** per Task 6, move legacy `application.rs` to `application/legacy.rs`, add `v1/{session,participant,completion}.rs`. V1 facade delegates legacy `SessionManagementService` + V1 authorizer; `SessionMessageService` delegates `GroupMessageHistoryService`.
- **Routes (`bcs-api-http/routes/session.rs` + `dto/session.rs`, new).**

Comment follow-ups (Rust mapping):

1. `BotParticipantMode{auto,muted}` — V1 `SessionParticipant.mode` Bot-only; legacy `ParticipantMode` full 4 values, session participants are Bot-only (project auto/muted).
2. `MessageSenderKind{bot,human,system}` — `SessionMessage.sender_type` maps legacy `SenderType{Bot,Human,System}`; `sender_id` from legacy (system messages `"system"`).
3. `SessionInput{query?}` + group-context fallback — `create_session` input narrowed (extract `query` from JSON); `SessionDetail.input` projects `Session.input.query`; fallback: no `input` → V1 facade uses parent group `context` as `input.query`.
4. Stable ordering — `SessionRepoPort::list_by_group` enforces `created_at DESC, session_id ASC`; message store enforces `session_seq ASC` (memory sort + mysql `ORDER BY`).
5. `SessionDetail.input` projection + `SessionMessage.content` from legacy `serde_json::Value` to string.

Other alignments: `SessionStatus[running,completed]`; completion idempotent (`complete_if_running` CAS, Completed→`Ok(None)`→V1 200); `delete_session` idempotent (`delete`→bool→`{deleted}`); `SessionParticipant.actor_kind=const bot`; `update_session` only `title`.

Tests: use case (completion idempotency, Completed rejects mutable, session belongs to group, Human not auto-enrolled, driver invariant, input fallback) + route (10 ops; assert `POST /sessions/{id}/messages` 404).
Dependency: based on #P1 merged `dev`; legacy `SessionManagementService` / `GroupMessageHistoryService` ready.

## Slice #P3 — Invitation + Friendship

Operations: Invitation 3 + Friendship/FriendRequest 6.

- **Trait:** new `application::v1::invitation::InvitationService` + `application::v1::friendship::FriendshipService`.
- **Use case:**
  - Invitation (`bcs-group/application/v1/invitation.rs`): reuse legacy `InviteService` token gen/verify; **V1 accept new join branch** (Bot→self; Human→verify `created_by` then join owned bot), does **not** reuse legacy `join_*_by_invite` (staff_no-based Human join).
  - Friendship (`bcs-friend/application/v1/{friendship,friend_request}.rs`): reuse legacy `FriendCoreService` / `FriendRequestCoreService`; Human ownership auth in application layer.
- **Routes:** `routes/invitation.rs` + `dto/invitation.rs` + `routes/friendship.rs` + `dto/friendship.rs`.

Comment follow-ups:

1. `list_friendships` / `list_friend_requests` stable ordering — `MemoryFriendRepo::list_friends` iterating `HashSet` (Codex-flagged) → collect then `sort_by` (created_at DESC + friend_bot_uuid/request_id ASC); DB store `ORDER BY`.
2. Friend-request `message` removed (`9aca2977`); Rust DTO carries no message.
3. `remove_friendship` single-pair (V1 new capability) — legacy `FriendCoreService` only has `remove_all_friendships`; per design §8.7 add single-pair remove to `FriendCoreService` + `FriendRepo` + store in this slice.
4. `accept_invitation` `bot_uuid` semantics — Bot Principal omits `bot_uuid` (self); Human carries `bot_uuid` (must verify `created_by`); Bot Principal carrying `bot_uuid` rejected (no impersonation).

Tests: use case (token target_type/target_id, Bot self-accept, Human owned-bot accept `created_by`, expired→410, repeat-accept idempotent, completed session→409, friend-request receiver-only, accept/reject idempotent, friendship symmetric idempotent) + route (9 ops; `bot_uuid` path cannot override Principal, `from_bot_uuid` cannot override Bot Principal, no Legacy response leak).
Dependency: based on #P2 merged `dev`; legacy `InviteService` / `FriendCoreService` / `FriendRequestCoreService` ready; **single-pair friendship remove needs new legacy Core API** (done in-slice).

## Slice #P4 — Rollout (production-blocked)

Plan tasks 13 (mount) + 14 (gateway) + 15 (principal transport) + 16 (compat gate) + 17 (E2E).

- Task 15 (trusted Principal transport) is the blocker: needs Gateway/identity owner decision on BotPrincipal schema + signed token format. Task 13 production mount depends on a real principal verifier (no test verifier in prod).
- #P4 start prerequisite: Principal transport owner decision in place. **#P1-#P3 test-only are not blocked** by principal and can proceed/merge independently; #P4 defers until the decision lands.
- Task 16 (compat gate) does not depend on principal and can be done first within #P4.

## Cross-slice Dependencies & PR Base

- Serial `#P1 → #P2 → #P3 → #P4`; each slice base = previous slice's merged `dev`; after #621 merges, slices rebase to `dev`.
- Technically #P2 delegates legacy `SessionManagementService` (no call to #P1 new code); #P3 invitation accept reuses legacy `add_participant` (no call to #P1 V1 `add_group_participant`). Serial mainly avoids authorizer/contract conflicts, not a hard dependency.

## Test Strategy

- TDD: each task writes a failing test first (plan steps).
- test `PrincipalVerifier` (PR #514 trait, test impl in slices); no production mount.
- Use-case tests: repository/registry test doubles, no HTTP.
- Route tests: fake V1 services; assert path/method/Principal forwarding/DTO unknown-field rejection/envelope/error-code mapping.
- Boundary AST: `application::v1` must not import `axum` / `http` / `bcs_protocol`.
- mount / E2E / compat gate reserved for #P4.

## Comment Follow-up Integration Summary

| Comment | Contract fix | Rust slice |
| --- | --- | --- |
| `DeleteResult{deleted}` | `87997ffc` | #P1 (group Rust synced in `87997ffc`) |
| Session participant `BotParticipantMode` + `actor_kind=bot` | `87997ffc` | #P2 |
| Session message `MessageSenderKind` | `9aca2977` | #P2 |
| Friend-request `message` removed | `9aca2977` | #P3 (DTO no message) |
| Session `context`→`SessionInput{query?}` + fallback | `ddb84bd3` | #P2 |
| List stable ordering | `ddb84bd3` | #P2 (session) + #P3 (friendship) |
| `accept_invitation` `bot_uuid` semantics | (contract already correct) | #P3 |
| `remove_friendship` single-pair (new capability) | (contract already defines) | #P3 (new legacy Core API) |

## References

- Design: `docs/plans/2026-07-28-bcn-openapi-v1-design.md` (§8 scope + Legacy mapping).
- Implementation plan: `docs/plans/2026-07-28-bcn-openapi-v1-implementation.md` (17 tasks, per-task TDD steps).
- Contract: `src/bcs/api-contracts/v1/` (27 operations, validated).
- PR #514 (first 5 Group ops + V1 foundation), PR #621 (contract expansion to 27 ops).
