# BCN OpenAPI Tag Grouping Design

- **Date:** 2026-08-03
- **Status:** Approved in the design discussion

## Goal

Group BCN operations in Gateway's Swagger UI by collaboration resource instead
of placing all operations under `default`.

## Design

The BCS OpenAPI contract remains authoritative. Each operation declares
exactly one collaboration-scoped tag:

- `Collaboration / Bots`
- `Collaboration / Friendships`
- `Collaboration / Groups`
- `Collaboration / Sessions`
- `Collaboration / Invitations`

Session connection-token issuance and the session-bound WebSocket handshake
use `Collaboration / Sessions`; they are access paths for a Session rather than
a separate public resource family.

The root BCS contract declares the five tags and their descriptions. Gateway
merges those top-level declarations in upstream-domain order and removes
duplicate declarations by tag name. Operation-level tags remain unchanged
during filtering, security annotation, and aggregation.

## Verification

- The deterministic BCN exporter test requires every operation to have exactly
  one of the five approved tags.
- Gateway merger tests require top-level tag declarations to be preserved and
  de-duplicated.
- The served-OpenAPI integration test verifies representative BCN operations,
  including the token and WebSocket endpoints, appear under the expected tag.
