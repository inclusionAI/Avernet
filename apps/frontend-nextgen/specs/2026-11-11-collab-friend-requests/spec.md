# Collab friend-request inbox

Owner: frontend-nextgen. Status: approved for implementation, 2026-11-11.

## Scope and contracts

Add a collab-only 好友申请 button immediately after the public Bot page's
identity picker. A modal shows 收到的申请 and 发出的申请 for the selected
Human/Bot actor. The Human id is the authenticated user id, not `human_<id>`;
Bot ids retain their complete canonical value. Switching actor/login or closing
unmounts the modal and invalidates all pending reads/actions.

Use existing BCS OpenAPI contracts, not the Backend notification/work-order API:
- `apps/bcs/api-contracts/v1/openapi/friendships.yaml`: GET requests with explicit
  actor/direction, pending status and independent 1-based pagination; POST accept/reject.
- `apps/bcs/api-contracts/v1/domain-models.yaml`: FriendConnectionRequest and pages.
- Existing `queryCollaborationBots`: POST `/openapi/v1/collaboration/bots/query`
  with deduplicated complete Bot ids from both directions' current pages.

Each load fetches both current pages concurrently (20 per page), then enriches
both directions using one batch query (at most 80 distinct Bot actors). Display
Bot name/avatar with id as secondary text; unresolved/deleted Bot or failed batch
lookup falls back to type/id/default avatar, with a visible retryable warning.
Current Human name/avatar come from authenticated identity; other Humans use id.
The contract does not provide request creation time: do not fabricate a timestamp.
Optional decision time/reason and request message are preserved where present.

Only received, pending requests whose recipient matches the selected actor have
通过/拒绝 buttons. Mutations are single-flight per request, validated against the
business envelope and confirmed status before updating the row. Failed writes
remain errors; no optimistic success. Successful decisions refresh the public
Bot catalog's relationship state without private chat creation/navigation.
Initial queries are pending-only; confirmed rows remain visible with updated
status until refresh/reopen. No cancel action, notification polling or badge.

## Architecture and compatibility

Component → Hook → Service → existing controller; pure view contracts in domain.
Add `getCollabFriendRequestsEnabled`: default false, collab true. Hidden entry
must not mount the modal or issue requests. Existing Open message center is
unchanged. No BCS server or API change, no new dependencies, no new route.
No need for a system ADR: this follows existing assembly capabilities and
request/state boundaries. Keep collab closure guard strict.

## Acceptance

Tests cover capability/placement, lazy loading, both directions, full-id batch
query/dedup/empty lists/fallback/auth/abort, paging, accept/reject success and
failure, duplicate actions, close/actor changes and A→B→A races, no single chat,
and no Backend/task/admin calls. Run frontend CI, collab build and closure guard.
Private OCB export and live BCS/browser acceptance remain separate evidence.
