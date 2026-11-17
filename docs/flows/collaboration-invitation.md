# Collaboration invitation links

## Scope and ownership

BCS V1 invitation application owns token validation and membership facts;
frontend-nextgen's workspace invitation page owns consent and navigation. The
public [OpenAPI invitation contract](../../apps/bcs/api-contracts/v1/openapi/invitations.yaml)
is authoritative. This page describes the source-level flow, not deployment status.

## Read before consent

1. The authenticated recipient opens `/workspace/invite/groups/{token}` or
   `/workspace/invite/sessions/{token}`.
2. Frontend calls `GET /openapi/v1/collaboration/invitations/{token}`. BCS requires
   a User identity, verifies the signature/version/type/expiration, and reads
   strict authorization snapshots from the bound environment's master store.
3. The result contains only `target_type`, `target_id`, `group_id`, and
   `already_joined`. A Group target returns its own Group ID; a Session returns
   its parent Group ID. Responses are private and non-cacheable.
4. Direct Human members navigate to the target, replacing the invitation history
   entry. Nonmembers see the existing confirmation and message-view-scope choice.
5. Only explicit confirmation calls `POST .../invitations/{token}/accept`.
   Acceptance revalidates independently and retains its idempotent result.

Membership means a Human participant whose actor ID is `human_{User.id}` in the
actual target. Public readability, current control of a member Bot, originator
status, and membership in a parent Group are not substitutes. Session membership
is valid even without direct Group membership. Modes and message scope are not
changed by following an existing-member link.

## Failures and bounded work

Invalid, unsupported, expired or missing-target invitations fail without joining;
expiration also applies to existing members. Authentication failures never fall
back to an unchecked confirmation. Transient snapshot/network failures remain
errors and may be retried; they are never interpreted as nonmembership. Older
async results cannot navigate after the frontend token changes.

Preflight never invokes legacy join, ensures a Human actor, emits an Event, or
writes membership. Group preview invokes one strict Group snapshot; the SQL implementation reads
one Group metadata row and its participant rows in two queries. Session preview
adds one Session-row query (participants stored in JSON) before the parent Group
snapshot: three SQL queries in total. Missing resources short-circuit; failures
propagate without automatic retries. Per-request work is proportional to target
and parent participant counts, not the number of Groups/Sessions or message
history. No new participant cap is introduced; very large existing resources
still incur their snapshot projection cost. No transaction spans navigation or
an external request, and no new cache is introduced. Later resource APIs still
apply their own current authorization.

## Compatibility

Deploy the BCS GET endpoint before or together with the new frontend. Existing
clients can keep using POST acceptance unchanged. A new frontend against an old
BCS receives 404 and cannot preflight; it deliberately does not turn a failed
preflight into unchecked confirmation or automatic acceptance.

## Source and verification

- [Application](../../apps/bcs/crates/application/v1/bcs-app-invitation/src/invitations.rs)
- [HTTP adapter](../../apps/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/invitation.rs)
- [Frontend state machine](../../apps/frontend-nextgen/src/pages/Workspace/hooks/useInviteAccept.ts)
- [Application tests](../../apps/bcs/crates/application/v1/bcs-app-invitation/tests/invitation_preview.rs)
- [Live HTTP journey](../../apps/bcs/scripts/e2e-test/invitation_story.py) — default runtime E2E inventory, real token issuance/consent and membership freshness.
- [Change record](../../apps/bcs/specs/2026-11-17-invitation-preflight/spec.md)

Tests exercise in-memory stores, contract export, frontend/HTTP behavior and a
live SQLite-backed BCS invitation journey. They do not by themselves establish
production routing, real SQL latency, or browser end-to-end correctness.
