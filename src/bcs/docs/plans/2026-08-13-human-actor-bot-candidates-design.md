# Human Actor Bot Candidates Design

- **Date:** 2026-08-13
- **Status:** Approved
- **Scope:** BCN OpenAPI V1 Bot candidates
- **Contract:** `api-contracts/v1/openapi/bots.yaml`

## Problem

`GET /openapi/v1/collaboration/bots/{bot_id}/candidates` currently accepts only
a physical Bot managed by the authenticated Human. Legacy `/actors/list`
allows the same directory rules to be evaluated from a Human Actor such as
`human_197262`, so the OpenAPI surface cannot yet replace that Human-facing
usage.

Adding a second Human-only endpoint would duplicate an operation whose filters,
result type, ordering, and purpose semantics are otherwise identical. Renaming
the Bot API to a generic Actor API would also create unnecessary contract and
client churn.

## Decision

Extend the existing Bot candidates operation so `bot_id` may identify either:

1. a physical Bot managed by the authenticated Human; or
2. that authenticated Human's own Human Actor, named `human_{subject.id}`.

Keep all public Bot terminology and existing Rust names. Contract prose will
describe the additional perspective as “including Human Actor”; it will not
rename Bot resources, commands, response types, or path parameters to Actor.

The operation continues to return physical Bot candidates only.

## Authorization

The operation remains Human control-plane only and continues to require the
configured App identity.

- For a physical Bot, the existing rule remains unchanged: `created_by` must
  equal the authenticated Human subject ID.
- For a Human Actor, its ID must exactly equal
  `human_{authenticated subject ID}`.
- A different Human Actor is rejected with `403 forbidden`.
- A missing, deleted, or unmaterialized requested record remains
  `404 bot_not_found`; this read operation does not create a Human Actor.

Authorization remains application policy in `bcs-app-bot`, before Provider
hydration and before candidate selection.

## Candidate Semantics

Human Actor and physical Bot perspectives use the same rules, matching Legacy
`cooperatable_only` behavior:

| OpenAPI purpose | Legacy flag | Visible candidates |
| --- | --- | --- |
| `discovery` | `cooperatable_only=false` | `public` and `protected` physical Bots |
| `collaboration` | `cooperatable_only=true` | `public` physical Bots plus accepted friends of the selected perspective |

Both perspectives:

- use the selected record's persisted environment;
- exclude the selected ID, Human rows, deleted rows, and unonboarded rows;
- apply the same trimmed, case-insensitive name filter;
- do not filter by raw status or effective reachability;
- order by `created_at DESC, bot_id ASC` before pagination; and
- return the existing `BotCandidatePageEnvelope`.

## Architecture and Data Flow

No new route, command, Core method, repository method, or SQL query is added.

```text
GET /bots/{bot_id}/candidates
        -> bcs-api-http::list_candidates
        -> BotServiceImpl::list_candidates
             -> authenticate Human
             -> load selected Bot/Human record
             -> authorize physical Bot ownership or current Human Actor
             -> FriendCoreService::list_friends(selected id)
             -> BotControlPlaneCoreService::list_candidates(existing query)
        -> existing BotCandidate page projection
```

`BotCandidateReadQuery.acting_bot_id` already accepts an identifier and the
store already receives the environment and friend set from the application
layer. Its filtering logic is perspective-kind agnostic, so the existing Core
and store implementation is reused without branching or duplication.

## Contract and Documentation Changes

Update the authoritative OpenAPI description and behavior metadata to state
that the path Bot perspective includes the current Human Actor. Preserve
`bot_id`, `list_bot_candidates`, `ListBotCandidates`, `BotCandidate`, and all
existing Bot-oriented terminology.

Update BCN API documentation and the earlier Bot control-plane implementation
design so they no longer claim that candidates require a physical acting Bot.
Contract tests will lock the two allowed perspective kinds and the unchanged
physical-Bot-only result kind.

## Compatibility

This is an additive contract change:

- requests using managed physical Bots keep the same success and error behavior;
- the authenticated Human's own Human Actor changes from
  `400 invalid_bot_kind` to success;
- other Human Actors remain forbidden; and
- request parameters and response schemas do not change.

No database migration, configuration change, Legacy route change, or new API
path is required.

## Testing

Follow contract-first TDD:

1. Change OpenAPI tests to require both managed physical Bot and current Human
   Actor perspectives while keeping `result_kind=bot`.
2. Change application tests so the current Human Actor succeeds and a different
   Human Actor is forbidden.
3. Prove `collaboration` includes a private Human friend and excludes a private
   non-friend.
4. Preserve existing physical Bot candidate tests and route forwarding tests.
5. Run the OpenAPI validator and tests, targeted Rust suites, and the relevant
   BCS architecture checks.
