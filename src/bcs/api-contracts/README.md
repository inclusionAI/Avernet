# BCN API Contracts

`v1/openapi.yaml` is the source of truth for the versioned public BCN OpenAPI.
`v1/internal.yaml` is the source of truth for the versioned internal BCN
collaboration API. Domain models and resource path items live in separate YAML
fragments so a domain can evolve without creating one monolithic file.

The current public OpenAPI contract contains 34 approved operations across Bot,
Group, GroupParticipant, Session, SessionParticipant, Invitation, Friendship,
FriendRequest, and session-bound WebSocket resources. Every public operation is
published below the single BCN ownership prefix `/openapi/v1/collaboration/**`.
These are the exact endpoints served externally by BCN.

The Human control-plane Bot batch contains exactly five public operations:

- `POST /openapi/v1/collaboration/bots/query`
- `GET /openapi/v1/collaboration/bots/{bot_id}`
- `PATCH /openapi/v1/collaboration/bots/{bot_id}`
- `GET /openapi/v1/collaboration/bots/mine`
- `GET /openapi/v1/collaboration/bots/{bot_id}/candidates`

Bot candidate search moved to the internal contract:

- `GET /api/v1/collaboration/bots/{bot_id}/candidates/search`

The candidates operation accepts either a physical Bot managed by the current
Human or that Human's own `human_{subject.id}` record (including Human Actor).
Both perspectives use the same discovery and collaboration filters, and the
response still contains physical Bot candidates only. The candidate-search
operation is the versioned projection of legacy `/actors/search`: it uses
semantic worker recommendation first, preserves its score/profile/tag
enrichment, then falls back to a Bot-name substring search when no usable
recommendation is available. An omitted `q`, `q=`, and a whitespace-only `q`
are equivalent: each returns `items: []` with `search_mode: empty_query` without
invoking downstream search. Semantic and fallback results use
`search_mode: semantic` and `search_mode: name_fallback`; fallback items omit
`score`. Raw BCSFuse recommendation context is never part of the V1 response.
The path `bot_id` replaces legacy `current_bot_uuid`; `purpose` replaces
`cooperatable_only`; Gateway `ctoken` is not part of the BCN contract.

These public bot operations deliberately do not add generic `GET /bots`,
legacy `/actors/**` aliases, runtime discovery, or a separate descriptor patch
route. All public bot operations require a Human Principal. The Bot domain
object is discriminated by `kind=bot|human`; omission of a `kind` query filter
means both kinds rather than a synthetic `all` enum value.

Global collaboration Session resources remain public at
`/openapi/v1/collaboration/sessions/{session_id}/**`. Creating and listing a
Group's Sessions remains nested at
`/openapi/v1/collaboration/groups/{group_id}/sessions`. The shared ownership
prefix separates both resources from Backend and BaaS paths while preserving
their natural names.

Session collection adds two idempotent Human control-plane operations at
`/openapi/v1/collaboration/sessions/{session_id}/collect`. `POST` collects and
`DELETE` uncollects on behalf of the required `participant` Bot. BCN verifies
that the authenticated Human owns that Bot and that the Bot participates in
the Session; collection state remains attributed to the Bot rather than the
Human caller.

Session-bound WebSocket access adds two operations to that HTTP surface:

- `POST /openapi/v1/collaboration/sessions/{session_id}/token` issues the
  short-lived connection credential after normal user authentication and
  session authorization.
- `GET /openapi/v1/collaboration/messages/ws?token=...` describes the WebSocket
  HTTP Upgrade handshake. The OpenAPI contract intentionally covers only the
  connection credential, authentication failures, and `101` upgrade response;
  WebSocket message envelopes remain governed by the existing protocol tests.

The WebSocket operation uses `x-avernet-protocol: websocket` so publication
and Gateway integration can distinguish an Upgrade endpoint from an ordinary
HTTP GET without inventing a JSON response body for status `101`.

Session files moved to the internal contract as nine operations under
`/api/v1/collaboration/sessions/{session_id}/**`: list, prepare, metadata,
delete, proxy upload, complete, protected download, share, and public shared
download. Protected operations declare User, App, and Bot as optional Gateway
identities and use `x-bcn-identity-policy: human_or_owned_bot`; BCN still
requires a valid Human or Bot actor and checks a co-present Bot's signed owner
claim against the User. Shared download declares an empty Gateway requirement
because its share token is the credential. The two download operations use
`x-avernet-raw-response: true` to document `200` byte streams and `302`
redirects instead of JSON success envelopes.

Validate the contract:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
```

Build a deterministic, self-contained OpenAPI document for Swagger UI, Redoc,
Gateway aggregation, or client generation:

```bash
uv run --with pyyaml python src/bcs/scripts/bundle_openapi_contract.py \
  --root src/bcs/api-contracts/v1 \
  --output-dir /tmp/bcn-openapi
```

Export the same validated public contract as deterministic JSON for Gateway
`/docs` consumption:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  /tmp/bcn.openapi.json
```

Export the internal contract for Gateway `/internal-docs` consumption:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  /tmp/bcn.internal.openapi.json \
  --entrypoint internal.yaml \
  --path-prefix /api/v1/collaboration/ \
  --forbid-prefix /openapi/v1/
```

Pass `--root src/bcs/api-contracts/v1` to export a different checked-out
contract root. The generated JSON is self-contained: source-fragment `$ref`
entries are resolved and discriminator mappings point inside the JSON document.

Run the contract tests without changing the repository-wide Python
dependencies:

```bash
uv run --with pytest --with pyyaml \
  pytest src/bcs/tests/openapi -q
```

Generated bundle outputs are build artifacts and are not committed from BCS.
The Gateway-owned schema snapshots `src/gateway/configs/schemas/bcn.openapi.json`
and `src/gateway/configs/schemas/bcn.internal.openapi.json` must be regenerated
from these contracts when Gateway consumers need updated BCN API JSON. The
candidate YAML is reviewed before implementation;
compatibility checks compare later revisions against an approved baseline.
