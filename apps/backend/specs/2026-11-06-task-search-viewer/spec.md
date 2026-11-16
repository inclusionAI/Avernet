# Task semantic search with optional viewer scope

Status: approved by Shangjian on 2026-11-06; implemented and locally verified; not deployed.

## Summary
Allow callers to search task Bot candidates from an explicit Bot or Human
perspective without changing existing callers. Keep the current route, required
baton fields, candidate response and task execution protocol.

## Motivation
The existing task search returns a public-only catalog subset, while product Bot
perspective discovery includes public and protected catalog entries. Callers need
to select the perspective deliberately rather than silently changing all searches.

## User stories
- Backend continues accepting existing four-field requests from old clients.
- Updated task-loop searches only as the current executor Bot, never as Human.
- A caller can explicitly select a Bot or Human viewer for candidate discovery.
- Invalid or incomplete viewer scope is rejected rather than falling back.

## Acceptance criteria
- [x] Omitted viewer scope preserves current filtering and candidate cap.
- [x] Explicit Bot scope uses the existing product Bot visibility rules.
- [x] Explicit Human scope uses the existing product Human visibility rules.
- [x] Viewer type and nonblank identity must be provided together; only bot and
      human are accepted. Both omitted/null mean the unchanged default path.
- [x] Current baton validation and candidate DTOs remain unchanged.
- [x] Viewer selection is not an authentication credential or a request to change
      the node's assignee, owner, state, or execution permissions.
- [x] Runtime-readiness screening remains in place for task candidates in both
      explicit perspectives; searchability is not a guarantee of dispatch access.
- [x] The source task-loop transport requires an explicit Bot viewer with the
      current Bot Runtime identity; Human, absent/null viewers and blank IDs are
      rejected locally. Never infer the viewer from owner or candidate results.

## Scope
Optional viewer scoping, input validation, compatible propagation, tests and docs.

## Out of scope
Changing default task search behavior, increasing the candidate cap, changing the
regression target host, adding a search fallback or alternative endpoint, changing
Relay reports/state transitions, replacing the pending dispatch-expiry regression
change, deploying, or synchronizing installed Skill copies.

## Decisions for review
Explicit viewer filtering reuses existing product behavior, while task results
retain runtime-readiness checks and the current maximum of five. Consequently,
the API need not return the same count/order as a product top_k=20 request.
