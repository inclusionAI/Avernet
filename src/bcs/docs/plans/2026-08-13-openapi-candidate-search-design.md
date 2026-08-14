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
    ?q=<optional natural-language query>
    &purpose=discovery|collaboration
```

The operation is the OpenAPI projection of legacy `/actors/search`:

| OpenAPI input | Legacy input |
| --- | --- |
| path `bot_id` | `current_bot_uuid` |
| `purpose=discovery` | `cooperatable_only=false` |
| `purpose=collaboration` | `cooperatable_only=true` |
| `q` | `q` |

`q` is optional. An omitted `q`, `q=`, and a whitespace-only `q` are equivalent
and produce an empty successful result without invoking semantic or name
search. This lets clients omit the parameter when the user has not entered a
query. `ctoken` is Gateway compatibility state and is not part of the BCN
contract. The result limit remains the legacy fixed top-K of 20; no offset or
total-count pagination is advertised for relevance-ranked search.

## Authorization

The operation uses the same perspective authorization as the existing
candidate list:

- the caller must have valid Human and App Gateway Principals;
- a physical acting Bot must be managed by the current Human;
- a Human Actor perspective must be exactly `human_{subject.id}`;
- a missing or unmaterialized perspective returns `bot_not_found`; and
- an unauthorized perspective returns `forbidden`.

Authorization is completed by the V1 application before invoking the shared
candidate-search Core.

## Search and Fallback Semantics

Legacy actor search currently combines reusable search policy, BCSFuse
orchestration, legacy response projection, and status/downlink enrichment in
one application service. The reusable candidate-search behavior moves into a
dedicated Core service. Both the legacy actor-directory application and the V1
Bot application call that Core directly; neither application calls the other.

The shared Core behavior is:

1. Normalize `q`; a missing, empty, or whitespace-only query produces an empty
   successful result without downstream calls.
2. Ask BCSFuse for ranked worker recommendations using the configured minimum
   score.
3. Exclude the acting Actor and recommendations that no longer resolve to a
   Bot.
4. Apply legacy visibility rules:
   - discovery: public and protected Bots;
   - collaboration: public Bots and accepted friends.
5. Preserve BCSFuse relevance order and attach normalized `score`,
   `short_profile`, and worker-profile `tags`.
6. If BCSFuse fails, returns no recommendations, or all recommendations are
   removed, run the existing case-insensitive name-substring fallback.
7. Identify the outcome as `empty_query`, `semantic`, or `name_fallback`.

The shared Core may retain the opaque recommendation response only as an
internal legacy-compatibility value. The legacy application may project it into
the existing `/actors/search` context. It is never exposed through OpenAPI V1.

The legacy actor-directory application remains responsible for the legacy
`ActorDirectoryEntry` shape, dynamic `active`/`offline` status, downlink flag,
and fallback skill-summary text. The V1 application remains responsible for
Human perspective authorization and the V1 Bot projection.

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
  "search_mode": "semantic"
}
```

Each `bot` is the existing complete, secret-free `PhysicalBot` DTO. `score` and
`short_profile` are optional because the downstream recommendation format
allows missing enrichment. `tags` is always an object. Semantic results expose
their BCSFuse score; name-fallback results omit `score` instead of assigning
the ambiguous value zero. The response contains only physical Bots even when
the perspective is a Human Actor. No raw BCSFuse response is part of the V1
contract.

`search_mode` is one of `empty_query`, `semantic`, or `name_fallback`. An empty
query returns `items: []` and `search_mode: empty_query`.

## Architecture and Data Flow

```text
GET /bots/{bot_id}/candidates/search
    -> bcs-api-http (parse and verify Gateway Principals)
    -> BotServiceImpl::search_candidates
         -> authorize candidate perspective
         -> BotCandidateSearchCoreService
              -> WorkerProfileCoreService / BCSFuse
              -> registry and friendship Core services
              -> name fallback when required
         -> BotControlPlaneCoreService::get_by_ids
         -> secret-free PhysicalBot projection
    -> V1 success envelope

GET /actors/search
    -> bcs-http
    -> ActorDirectoryService::search_actors
         -> BotCandidateSearchCoreService
         -> legacy ActorDirectoryEntry projection
```

The delivery adapter remains thin. No request is sent to the legacy HTTP route,
and no new BCSFuse client is introduced. Composition injects one shared search
Core into the legacy and V1 applications. Worker-profile recommendation is a
Core contract rather than an application contract so the shared search Core
does not depend on a legacy use case.

The refactor is intentionally limited to candidate search. Legacy actor list
and actor-status update behavior remain in `ActorDirectoryService`.

## Compatibility and Risk

This is additive: the existing candidate-list path, parameters, ordering,
pagination, and response remain unchanged. The new operation adds one method to
the V1 `BotService` application contract and one route to the approved OpenAPI
inventory.

The primary risk is accidental drift while extracting behavior from the legacy
application. Core tests therefore lock semantic recommendation, visibility,
friendship, fallback, empty-query behavior, enrichment, and ordering. Separate
legacy and V1 application tests lock their projections and authorization.
Contract tests lock the optional `q`, normalized response shape, search mode,
complete PhysicalBot body, and absence of raw BCSFuse data, `ctoken`,
`current_bot_uuid`, and `cooperatable_only`.

## Validation

- OpenAPI validator and complete OpenAPI contract test suite.
- Candidate-search Core tests for semantic, fallback, empty-query, filtering,
  ordering, and downstream failure behavior.
- Legacy actor-directory regression tests proving `/actors/search` compatibility.
- `bcs-app-bot` application tests for authorization and projection.
- `bcs-api-http` route tests for parameter forwarding and rejection behavior.
- BCS architecture checks and targeted Cargo tests.
- Regenerated Gateway BCN OpenAPI snapshot.
