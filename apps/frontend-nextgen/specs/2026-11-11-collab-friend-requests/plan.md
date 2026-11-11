# Collab Friend Requests Implementation Plan

**Goal:** Collab-only request inbox/outbox with BCS Bot enrichment and recipient decisions.
**Architecture:** Pure domain views; service validates existing controller results;
modal-local hook isolates identity/request generations; capability-gated entry.
**Tech Stack:** React, TypeScript, Umi, Jest/Testing Library, existing shadcn primitives.

- [x] Add service red tests in `test/services/collaborationSquare/friendConnectionRequestService.test.ts`:
  `load(actor, {sent:1, received:1}, signal)` must request both pending pages then
  `queryCollaborationBots({bot_ids: uniqueFullBotIds}, signal)` once; failures and
  invalid envelopes must not return success; approve/reject verify id/status.
- [x] Implement domain contracts in `src/domain/collaborationSquare/friendRequests.ts`,
  validated service/mapper in `src/services/collaborationSquare/`, and enrich the
  existing controller DTO with contract fields. Run closest service tests/typecheck.
- [x] Add hook/UI red tests for lazy entry, placement, pagination, decisions,
  duplicate submission and close/identity stale results. Implement
  `useFriendConnectionRequests`, `FriendRequestsButton`, `FriendRequestsDialog`
  and row rendering using existing Button/Modal/Segmented/Pagination/Avatar.
- [x] Add default-false capability and collab true override; mount the entry after
  SquareIdentityPicker only for Bot resource. Scope component key by login+actor.
- [x] Exercise real collab shell/pages with HTTP fixtures for sent/received/query/
  accept/reject; assert no private-session/Backend/admin/space/task calls.
- [x] Update README and this spec's validation report. Run `npm run check:open-core`,
  `npm run ci`, `npm run build:collab`, line-count and `git diff --check` checks.
  Do not commit or push without a new user request.
