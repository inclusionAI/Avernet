# BCN API Contracts

`v1/openapi.yaml` is the source of truth for the versioned BCN OpenAPI. Domain
models and resource path items live in separate YAML fragments so a domain can
evolve without creating one monolithic file.

The current contract contains 32 approved operations across Bot, Group,
GroupParticipant, Session, SessionParticipant, Invitation, Friendship,
FriendRequest, and session-bound WebSocket resources. Every operation is published below the single BCN
ownership prefix `/openapi/v1/collaboration/**`. These are the exact endpoints
served by BCN and intended for future Gateway aggregation; no path rewrite is
required.

The Human control-plane Bot batch contains exactly five operations:

- `GET /openapi/v1/collaboration/bots/{bot_id}/candidates`
- `POST /openapi/v1/collaboration/bots/query`
- `GET /openapi/v1/collaboration/bots/{bot_id}`
- `PATCH /openapi/v1/collaboration/bots/{bot_id}`
- `GET /openapi/v1/collaboration/bots/mine`

These operations deliberately do not add generic `GET /bots`, legacy
`/actors/**` aliases, runtime discovery, or a separate descriptor patch route.
All five require a Human Principal. The Bot domain object is discriminated by
`kind=bot|human`; omission of a `kind` query filter means both kinds rather
than a synthetic `all` enum value.

Global collaboration Session resources use
`/openapi/v1/collaboration/sessions/{session_id}/**`. Creating and listing a
Group's Sessions remains nested at
`/openapi/v1/collaboration/groups/{group_id}/sessions`. The shared ownership
prefix separates both resources from Backend and BaaS paths while preserving
their natural names.

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

Export the same validated contract as deterministic JSON for Gateway
consumption:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  /tmp/bcn.openapi.json
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
The Gateway-owned schema snapshot `src/gateway/configs/schemas/bcn.openapi.json`
must be regenerated from this contract when Gateway consumers need the updated
BCN OpenAPI JSON. The candidate YAML is reviewed before implementation;
compatibility checks compare later revisions against an approved baseline.
