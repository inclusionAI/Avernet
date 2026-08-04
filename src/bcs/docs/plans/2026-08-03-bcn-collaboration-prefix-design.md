# BCN Unified Collaboration Prefix Design

**Date:** 2026-08-03
**Status:** Approved

## Problem

BCN's V1 resources currently occupy several top-level Gateway namespaces,
including `bots`, `groups`, `group-sessions`, `friend-requests`, and
`invitations`. Some of those namespaces are also used by Backend or BaaS. A
Gateway OpenAPI aggregator therefore cannot identify BCN ownership from a
single prefix, and path-by-path conflict handling would couple Gateway routing
to BCN's individual resources.

The Gateway and BCN should expose identical endpoints. Introducing one public
path and another upstream path would require rewrite rules, make generated
clients describe a different API than BCN serves, and add another contract to
keep synchronized.

## Decision

Every BCN V1 OpenAPI operation is exposed below one ownership prefix:

```text
/openapi/v1/collaboration/**
```

The resource name follows that prefix directly:

```text
/openapi/v1/collaboration/bots/**
/openapi/v1/collaboration/groups/**
/openapi/v1/collaboration/sessions/**
/openapi/v1/collaboration/friend-requests/**
/openapi/v1/collaboration/invitations/**
```

Representative mappings are:

| Previous path | Final path |
| --- | --- |
| `/openapi/v1/bots/collaboration/mine` | `/openapi/v1/collaboration/bots/mine` |
| `/openapi/v1/bots/collaboration/{bot_id}` | `/openapi/v1/collaboration/bots/{bot_id}` |
| `/openapi/v1/groups/{group_id}/sessions` | `/openapi/v1/collaboration/groups/{group_id}/sessions` |
| `/openapi/v1/group-sessions/{session_id}/messages` | `/openapi/v1/collaboration/sessions/{session_id}/messages` |
| `/openapi/v1/friend-requests/{request_id}/accept` | `/openapi/v1/collaboration/friend-requests/{request_id}/accept` |
| `/openapi/v1/invitations/{token}/accept` | `/openapi/v1/collaboration/invitations/{token}/accept` |

The redundant `collaboration` segment after `bots` is removed. Session
resources return to the natural `sessions` name because the ownership prefix
already prevents collision with BaaS Session APIs.

## Contract and Runtime Shape

`src/bcs/api-contracts/v1/openapi.yaml` remains the authoritative BCN public
contract. Its path keys are the exact paths served by the Axum adapter and the
exact paths that a future Gateway aggregation step should publish. No Gateway
contract or path rewrite is introduced in this change.

The Axum composition root nests the five resource routers once at
`/openapi/v1/collaboration`. Individual route modules declare only their
resource-relative paths. This makes the ownership boundary structural and
prevents one resource family from accidentally omitting the prefix.

## Compatibility

The previous V1 paths are not retained as aliases. The V1 adapter is still a
preparatory surface and is not mounted by the production bootstrap, so
duplicate public routes would preserve ambiguity without protecting an active
production client.

This is a path-only contract change. Operation IDs, request and response
schemas, status codes, authorization rules, application services, and storage
behavior remain unchanged.

## Gateway Follow-up

Gateway should later register BCN as one `collaboration` API domain, import the
BCN OpenAPI document, and proxy `/openapi/v1/collaboration/**` without a path
rewrite. That work is intentionally outside `src/bcs` and outside this change.

## Acceptance Criteria

- The OpenAPI contract still contains exactly 32 approved operations.
- Every OpenAPI operation path starts with `/openapi/v1/collaboration/`.
- Axum serves the same resource paths declared by the contract.
- Representative unprefixed and former two-prefix paths are not mounted.
- No file outside `src/bcs` changes.

