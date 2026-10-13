# Restore service Bot routing during group creation

PR: [#2662](https://github.com/inclusionAI/Avernet/pull/2662).

Status: implemented and locally verified.

Owner: Frontend next-gen. Scope: migrate the existing open-claw group creation
behavior requested by the user on 2026-10-13. Adds a backward-compatible public classification path; the legacy API remains.
No core Service API or ADR change.

## Existing contracts

- `GET /openapi/v1/bots/{bot_id}/classification` is authenticated but does not require
  owner/collaborator access. Returns the public `{code, message, data, request_id}` envelope with
  `data.bot_id` and `data.bot_type`. Errors use HTTP 401/404/409/422/500 and
  corresponding six-digit public codes. Composite actor identities use the Bot ID
  in the encoded path and the entity suffix in `entity_id`, supported by the
  current backend classification router.
- Group creation accepts optional `participants[].tags: string[]`. Service Bot
  stages are `online`, `verify`, and `draft`.
- The optional task execute API accepts participant IDs/bindings but does not
  declare participant tags. Explicit routing with task execution is rejected
  before sending a request; users can uncheck task execution to create the group
  through the standard endpoint. Dynamic routing retains existing behavior.

## Acceptance criteria

1. Selecting a Bot queries its minimal classification. Default Bots and humans
   do not require classification; unmanaged Bots (404) are treated as non-service.
   Other failures block creation and expose a retry action.
2. A service Bot defaults to dynamic routing without a stage tag. Specified
   routing requires an explicit choice from production, verification, and draft.
   Returning to dynamic routing clears the selected stage.
3. Free chat, manager/worker, and custom collaboration preserve the selected
   stage in the corresponding participant's `tags`, including the driver/manager.
   Human message visibility scope is preserved; ordinary Bots carry no tags.
4. Removing/reselecting a Bot, closing the modal, or unmounting invalidates old
   asynchronous results. Reopening starts with fresh routing selections.
5. The group form scrolls when routing controls require additional height.

The frontend uses its existing `/openapi` Gateway proxy. The Gateway forwards
the Bots domain to Backend and requires a user for this classification operation.
Its pinned OpenAPI artifact includes the new operation.

## Validation

Completed locally: 9 focused suites / 115 tests passed; TypeScript, frontend lint,
production build, and Open Core artifact checks passed. Modified/added source
files remain below 1,000 lines (the creation modal remains below 300 lines).


- Classification service tests exercise real controller request construction,
  composite identity encoding, default/human skips, missing Bots, and failures.
- Routing hook tests cover default routing, explicit stages, retry, selection
  races, and close/reopen reset.
- Modal tests cover the three group strategies, stage selection, failure/retry,
  and dynamic routing tag removal.
- Request tests cover tags and visibility scope for all strategies, and reject
  unsupported task execute routing before losing tags.
- Runtime classification and provider routing against a deployed backend remain
  deployment verification; no live group is created by automated tests.
