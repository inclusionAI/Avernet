# Invitation Membership Preflight Implementation Plan

**Goal:** Existing Human members follow a shared Group/Session invitation without an extra confirmation click.
**Architecture:** Add read-only V1 invitation preview using strict authorization snapshots, expose it over authenticated HTTP GET, and branch the frontend invitation state machine before consent. Preserve POST accept and expiration semantics.
**Tech Stack:** Rust/async-trait/Axum, OpenAPI YAML, TypeScript/React/Jest.

## Task 1 — BCS contract and application

- [x] Add failing tests in `crates/application/v1/bcs-app-invitation/tests/invitation_preview.rs` for Group/Session membership, nonmembers, Bot-only callers, malformed/expired tokens, missing resources, fresh snapshot reads and no mutations.
- [x] Run `cargo test -p bcs-app-invitation --test invitation_preview` and confirm missing preview contract fails compilation.
- [x] Add `PreviewInvitation`, `InvitationPreview`, and required `InvitationService::preview_invitation` in `crates/service-api/bcs-service-api/src/application/v1/invitation.rs`; update every trait implementation/test double.
- [x] Extract invitation-owned helpers and implementation from oversized `bcs-app-invitation/src/lib.rs` into `invitations.rs`; extract existing unit tests into `tests.rs` so modified source files stay below 1,000 lines.
- [x] Inject `SessionAuthorizationCoreService` through the existing composition root; preview checks `human_{user.id}` against strict snapshots, returning minimal target metadata with no legacy join call.
- [x] Rerun application and service API contract tests.

## Task 2 — HTTP and public contract

- [x] Add authenticated GET tests to `crates/adapters/http/bcs-api-http/tests/invitation_routes.rs`, verifying caller/token forwarding, envelope, no accept call and missing credentials.
- [x] Add GET route translating `PreviewInvitation` to the service API, with private/no-store response caching.
- [x] Update `api-contracts/v1/openapi.yaml`, `openapi/invitations.yaml`, and schemas; add preview-specific contract assertions and update approved operation inventories/export count.
- [x] Run `cargo test -p bcs-api-http --test invitation_routes` and Python OpenAPI tests. Compile all affected test targets.

## Task 3 — Frontend (delegated implementation)

- [x] Add failing service/hook/page tests for preflight GET, auto-navigation for members, confirmation for nonmembers, invalid/auth/transient failures, and obsolete async responses.
- [x] Add controller GET and typed preview result. Replace local-only `getAcceptPageState` with read-only preflight; branch `useInviteAccept` into accepted/confirm without accepting automatically.
- [x] Reuse panel navigation with `replace: true` and explicit read-only authenticated Human identity; maintain explicit consent/message scope for nonmembers and avoid Session detail hydration for preview navigation.
- [x] Run focused frontend tests, `npm run check:open-core`, `npm run typecheck`, and affected-file ESLint.

## Task 4 — Evidence and review

- [x] Update invitation flow documentation/navigation with read-only and exact-membership contracts and bounded DB-read costs (Group: one strict snapshot; Session: Session + parent Group snapshots).
- [x] Review the complete diff against the approved spec; check source line counts and `git diff --check`.
- [x] Record final HTTP runtime result and actual passing/failing/unrun checks. Leave changes uncommitted unless requested; no push or deployment.
