# Collab friend-request inbox

The public Bot discovery header exposes 好友申请 immediately after its identity
picker only when `getCollabFriendRequestsEnabled().value` is true. The default
capability is false; `src/extensions/collab.ts` enables it. This entry does not
restore the Backend message center and is not visible on the public group page.

## Scope and data flow

The modal follows the discovery identity (not workspace active identity). Human
queries use the authenticated user id, and Bot queries preserve the full Bot id.
The modal's mount key includes login user id and actor type/id. Closing it or
changing that scope destroys the old view, aborts reads, and prevents late
responses from updating the next view or displaying success notifications.
Aborting a client mutation does not guarantee rollback of a server-side action;
reopen/refresh queries the authoritative state.

`FriendRequestsButton` → `FriendRequestsDialog` / `FriendRequestRow` →
`useFriendConnectionRequests` → `friendConnectionRequestService` → existing BCS
controllers. Pure view contracts are in
`src/domain/collaborationSquare/friendRequests.ts`.

On open/refresh or pagination:

1. Query both directions of pending requests concurrently using the current
   actor, each with its own 1-based page and page size 20.
2. Deduplicate the complete Bot ids in both pages' sender/recipient actors and
   call `/openapi/v1/collaboration/bots/query` once. Human actors are not sent
   as Bot ids. The current Human's presentation comes from authenticated
   identity; unknown Humans use their id, without an organization lookup.
3. Render the peer's Bot name/avatar, actor id/type, provided online state,
   request message and status. Missing Bot records or ordinary detail-query
   failures retain the request and use id/default avatar plus a refresh warning.
   Authentication/authorization failures and aborts are not treated as success.

The request contract provides no creation timestamp; the UI does not invent one.
Pagination preserves each direction's page. Confirmed rows stay in the current
snapshot with 已通过/已拒绝 until the next pending-only refresh. There is no
background inbox polling, unread badge, cancel action or private-chat navigation.

## Decisions and failures

Only a received pending request addressed to the selected actor has 通过/拒绝.
The Hook blocks duplicate submission synchronously and disables that row's two
buttons while pending. Refresh and paging are blocked during mutations.

Accept/reject use the existing POST transition endpoints with encoded request
ids. A success requires business code 20000, matching request id/actors and the
expected confirmed status. Failed writes remain errors and leave the pending
row actionable for retry; the modal stays open. On success, retain enriched
presentation, update confirmed status, and refresh the public Bot catalog's
friendship state. Never create a Human private session or navigate to single chat.

## Contract authority and validation

- [BCS OpenAPI friendships](../../bcs/api-contracts/v1/openapi/friendships.yaml)
- [BCS domain schemas](../../bcs/api-contracts/v1/domain-models.yaml)
- [BCS application service contract](../../bcs/crates/service-api/bcs-service-api/src/application/v1/friend_connection.rs)
- [Controller](../src/services/backendApi/collaboration/collaborationFriendConnectionController.ts)
- [Service tests](../test/services/collaborationSquare/friendConnectionRequestService.test.ts)
- [Identity/race tests](../test/hooks/useFriendConnectionRequests.test.tsx)
- [Modal tests](../test/components/CollaborationSquare/FriendRequestsDialog.test.tsx)
- [HTTP-boundary page tests](../test/shell/collabFriendRequestsBoot.test.tsx)

Open message-center behavior remains unchanged. Private collab overlays must
inherit this capability from the public collab assembly; rebuilding a private
export and validating live auth/BCS interactions are separate deployment gates.
