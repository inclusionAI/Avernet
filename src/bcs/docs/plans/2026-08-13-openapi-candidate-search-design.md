# OpenAPI Candidate Search Design

- **Date:** 2026-08-13
- **Status:** Approved
- **Scope:** BCN OpenAPI V1 Bot candidate search
- **Legacy reference:** `GET /actors/search`

## Problem

BCN's legacy `GET /actors/search` operation supports semantic candidate
recommendation through BCSFuse and falls back to a name search when BCSFuse is
unavailable, returns no recommendations, or produces no visible candidates.
The versioned OpenAPI currently exposes only
`GET /openapi/v1/collaboration/bots/{bot_id}/candidates`, whose contract is a
deterministically ordered, pageable candidate list with an optional name
filter. OpenAPI consumers therefore cannot use the existing semantic search
capability without calling the legacy surface.

The list and search operations have different ordering, pagination, result
metadata, and downstream-degradation semantics. Changing the existing
candidate-list operation would make one operation switch contracts based on a
query combination and would weaken its deterministic pagination guarantee.

## Decision

Add a separate versioned operation:

```http
GET /openapi/v1/collaboration/bots/{bot_id}/candidates/search
    ?q=<natural-language query>
    &purpose=discovery|collaboration
```

The operation is the OpenAPI projection of legacy `/actors/search`:

| OpenAPI input | Legacy input |
| --- | --- |
| path `bot_id` | `current_bot_uuid` |
| `purpose=discovery` | `cooperatable_only=false` |
| `purpose=collaboration` | `cooperatable_only=true` |
| `q` | `q` |

`ctoken` is Gateway compatibility state and is not part of the BCN contract.
The result limit remains the legacy fixed top-K of 20; no offset or total-count
pagination is advertised for relevance-ranked search.

## Authorization

The operation uses the same perspective authorization as the existing
candidate list:

- the caller must have valid Human and App Gateway Principals;
- a physical acting Bot must be managed by the current Human;
- a Human Actor perspective must be exactly `human_{subject.id}`;
- a missing or unmaterialized perspective returns `bot_not_found`; and
- an unauthorized perspective returns `forbidden`.

Authorization is completed before invoking the legacy search application
service.

## Search and Fallback Semantics

The versioned Bot application facade calls the existing `ActorDirectoryService`
with the mapped legacy command and fixed limit 20. This preserves the current
behavior without making an HTTP loopback call:

1. Trim `q`; an empty query produces an empty successful result, matching
   legacy behavior.
2. Ask BCSFuse for ranked worker recommendations using the configured minimum
   score.
3. Exclude the acting Actor and recommendations that no longer resolve to a
   Bot.
4. Apply legacy visibility rules:
   - discovery: public and protected Bots;
   - collaboration: public Bots and accepted friends.
5. Preserve BCSFuse relevance order and attach `score`, `short_profile`, and
   worker-profile `tags`.
6. If BCSFuse fails, returns no recommendations, or all recommendations are
   removed, run the existing case-insensitive name-substring fallback with
   `q`, score `0`, and the legacy skill summary.
7. Preserve the raw optional `context.recommend_response` for wire-level
   functional parity with the legacy result.

The OpenAPI facade hydrates returned IDs through the Bot control-plane service
so each result uses the existing secret-free `PhysicalBot` projection,
including Provider and effective reachability fields.

## Response

The standard V1 success envelope contains:

```json
{
  "items": [
    {
      "bot": { "bot_id": "...", "kind": "bot" },
      "is_friend": false,
      "tags": {},
      "score": 0.82,
      "short_profile": "..."
    }
  ],
  "context": {
    "recommend_response": {}
  }
}
```

`score` and `short_profile` are optional because the downstream recommendation
format allows missing enrichment. `tags` is always an object. The context value
is nullable when recommendation was unavailable. The response contains only
physical Bots even when the perspective is a Human Actor.

## Architecture and Data Flow

```text
GET /bots/{bot_id}/candidates/search
    -> bcs-api-http (parse and verify Gateway Principals)
    -> BotServiceImpl::search_candidates
         -> authorize candidate perspective
         -> ActorDirectoryService::search_actors
              -> WorkerProfileService / BCSFuse
              -> legacy name fallback when required
         -> BotControlPlaneCoreService::get_by_ids
         -> secret-free PhysicalBot projection
    -> V1 success envelope
```

The delivery adapter remains thin. No request is sent to the legacy HTTP route,
and no new BCSFuse client is introduced. Composition injects the same
`ActorDirectoryService` already used by the legacy adapter.

## Compatibility and Risk

This is additive: the existing candidate-list path, parameters, ordering,
pagination, and response remain unchanged. The new operation adds one method to
the V1 `BotService` application contract and one route to the approved OpenAPI
inventory.

The primary risk is accidental drift between the legacy search result and its
OpenAPI projection. Application tests therefore lock command mapping, fallback
metadata propagation, result ordering, and perspective authorization. Contract
tests lock the path, parameters, response shape, and absence of `ctoken`,
`current_bot_uuid`, and `cooperatable_only`.

## Validation

- OpenAPI validator and complete OpenAPI contract test suite.
- `bcs-app-bot` application tests for authorization and projection.
- `bcs-api-http` route tests for parameter forwarding and rejection behavior.
- BCS architecture checks and targeted Cargo tests.
- Regenerated Gateway BCN OpenAPI snapshot.
