# BCN Collaboration Path Alignment Design

**Date:** 2026-08-03  
**Status:** Approved

## Problem

BCN currently publishes Bot control-plane operations directly under
`/openapi/v1/bots/**` and global Session operations under
`/openapi/v1/sessions/**`. Those paths collide with the Gateway's existing
Backend-owned Bot surface and BaaS-owned Session surface. Directly aggregating
the BCN contract into the Gateway OpenAPI document would overwrite existing
paths and make runtime ownership ambiguous.

The public API should retain one top-level Bot vocabulary. Collaboration is a
Bot capability, not a second kind of Bot or a separate top-level bounded
context.

## Public Path Contract

BCN owns two unambiguous public prefixes:

```text
/openapi/v1/bots/collaboration/**
/openapi/v1/group-sessions/**
```

The Bot control-plane operations move as follows:

| Existing path | New path |
| --- | --- |
| `GET /openapi/v1/bots/mine` | `GET /openapi/v1/bots/collaboration/mine` |
| `POST /openapi/v1/bots/query` | `POST /openapi/v1/bots/collaboration/query` |
| `GET /openapi/v1/bots/{bot_id}` | `GET /openapi/v1/bots/collaboration/{bot_id}` |
| `PATCH /openapi/v1/bots/{bot_id}` | `PATCH /openapi/v1/bots/collaboration/{bot_id}` |
| `GET /openapi/v1/bots/{bot_id}/candidates` | `GET /openapi/v1/bots/collaboration/{bot_id}/candidates` |

Existing friendship and friend-request operations already live under
`/openapi/v1/bots/collaboration/{bot_uuid}/**` and remain unchanged.

The global Session resource moves as follows:

| Existing prefix | New prefix |
| --- | --- |
| `/openapi/v1/sessions/{session_id}` | `/openapi/v1/group-sessions/{session_id}` |

This includes Session detail, completion, messages, participants, and
Session invitation operations. The nested collection
`/openapi/v1/groups/{group_id}/sessions` remains unchanged because its parent
Group already establishes BCN ownership.

## Contract and Runtime Synchronization

`src/bcs/api-contracts/v1/openapi.yaml` remains the public contract authority.
Its path keys change in the same commit as the Axum route literals under
`bcs-api-http`. The operation set, request and response schemas, authorization
requirements, and application-service calls do not change.

No compatibility aliases are mounted at the old paths. The V1 adapter is not
production-mounted yet, so keeping duplicate legacy public paths would add
ambiguity without preserving an active client contract. Tests explicitly prove
that representative old Bot and Session paths no longer resolve.

## Error Handling

Path migration does not introduce new error semantics. Requests to new paths
retain the existing V1 envelope and status mappings. Requests to old paths are
absent from the versioned router and therefore return the router's normal
not-found or method-not-allowed response.

## Validation

- OpenAPI validation continues to report exactly 32 approved operations.
- Bot contract tests require every Bot control-plane operation under
  `/openapi/v1/bots/collaboration/**`.
- Session contract tests require global Session operations under
  `/openapi/v1/group-sessions/**` while preserving the nested Group collection.
- Axum route tests call the new paths and prove representative old paths are
  absent.
- The deterministic bundled OpenAPI document contains no direct BCN-owned
  `/openapi/v1/bots/{bot_id}` or `/openapi/v1/sessions/{session_id}` paths.

## Out of Scope

- Gateway longest-prefix routing and schema aggregation changes.
- Production mounting of `bcs-api-http`.
- Changes to application services, persistence, authorization, DTOs, or
  Gateway Principal verification.
