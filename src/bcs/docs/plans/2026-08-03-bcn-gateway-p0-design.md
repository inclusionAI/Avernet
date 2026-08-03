# BCN-to-Gateway P0 Integration Design

- **Date:** 2026-08-03
- **Status:** Approved through the preceding design discussion
- **Tracking for deferred work:** inclusionAI/Avernet#700

## Goal

Expose the existing contract-first BCN V1 HTTP API through Gateway at the
identical `/openapi/v1/collaboration/**` paths, publish its OpenAPI description
through Gateway's existing compatibility-gated schema catalog, and verify the
Gateway Principal at the BCS boundary.

## Scope boundary

P0 contains only the work required for a usable, fail-closed integration:

1. Export the authoritative BCS YAML contract as deterministic, self-contained
   `bcn.openapi.json`.
2. Publish that artifact through Gateway's existing dump, compatibility-gate,
   and file-schema-catalog flow.
3. Compose the existing V1 Application facades and mount the existing
   `bcs-api-http` Router in the BCS production bootstrap.
4. Resolve the shared Gateway Principal signing key once in the BCS composition
   root and inject `GatewayPrincipalTokenVerifier` with `iss=gateway`,
   `aud=bcs`, and `kid=bare`.
5. Configure Gateway domain `collaboration` to server `bcs`, with no rewrite,
   and require a User Principal for the prefix.
6. Add focused contract, mount, forwarding, authentication, and served-OpenAPI
   tests.

The current contract has 32 operations. PR #697's standalone session token
Router is not part of this P0 branch and is not added to the contract or mount.
If that PR merges first, its public operation must be added to the contract and
explicitly composed in a follow-up/rebase rather than becoming an undocumented
33rd route.

## Approaches considered

### Selected: contract-first artifact plus explicit runtime composition

Keep `src/bcs/api-contracts/v1/openapi.yaml` and its fragments authoritative.
The exporter resolves references and emits deterministic JSON. Bootstrap
constructs the existing V1 Application implementations and mounts the existing
Axum Router. This is the smallest change that preserves current architecture.

### Deferred: generate Axum routes and route inventory from one Rust manifest

This gives stronger runtime/contract drift prevention, but changes the API
development model and is not required to forward today's API. It remains in
issue #700.

### Rejected: derive the public document from runtime introspection

BCS is contract-first, unlike the FastAPI services. Runtime introspection would
create a second authority and would still require schema and error-envelope
conformance work.

## Architecture and data flow

```text
BCS YAML contract
  -> deterministic JSON exporter
  -> Gateway compatibility gate
  -> configs/schemas/bcn.openapi.json
  -> FileSchemaCatalog
  -> Gateway /openapi.json

Client
  -> Gateway /openapi/v1/collaboration/**
  -> authenticate User
  -> sign X-Avernet-Principal (aud=bcs)
  -> forward path verbatim to BCS
  -> BCS verifies JWT and creates AuthenticatedCaller
  -> existing bcs-api-http Router
  -> existing V1 Application facade
  -> existing core/store services
```

Gateway selects the upstream from the first segment after `/openapi/v1`.
Therefore one `collaboration -> bcs` domain entry covers bots, groups, sessions,
friend requests, and invitations. Gateway and BCS paths remain identical; no
proxy rewrite or handwritten Gateway operation is added.

## OpenAPI publication

The BCS exporter:

- loads the existing multi-file YAML contract;
- resolves all file and local references;
- rewrites discriminator mappings to self-contained JSON pointers;
- rejects any exported operation outside
  `/openapi/v1/collaboration/**`;
- emits UTF-8 JSON with sorted keys, stable separators, and one trailing
  newline;
- never includes unresolved `$ref` values to external files.

Gateway's existing compatibility gate remains the publication authority. The
initial committed `bcn.openapi.json` establishes the single-box published
artifact. Collision detection in the multi-domain OpenAPI merger is deferred
to #700; the dedicated collaboration path prefix prevents path collisions in
this P0.

## BCS runtime composition and authentication

`bcs-api-http` remains a delivery adapter and continues to depend only on V1
Application contracts. Concrete `bcs-app-*` implementations are selected in
`bootstrap/bcs`, which already owns service and store construction.

BCS resolves the same community HMAC key as Gateway:

```text
AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE
```

Local/dev single-box execution may use Gateway's documented fixed development
fallback key. Pre, gray, and prod fail startup when the configured secret is
missing or empty. Request-time missing, duplicate, malformed, expired,
wrong-issuer, wrong-audience, wrong-kid, or incorrectly signed Principal values
produce the existing uniform 401 envelope.

The V1 Router is merged directly into the existing Axum application. Legacy
routes remain mounted unchanged. The mount must not add another
`/openapi/v1/collaboration` prefix.

## Gateway configuration

Add:

```yaml
upstream_vars:
  bcs_server_url: https://bcs.sample.com

route_security:
  /openapi/v1/collaboration/**:
    user: required

upstreams:
  domains:
    collaboration:
      server: bcs
      schema:
        source: file
        path: schemas/bcn.openapi.json
  servers:
    bcs:
      base_url: ${bcs_server_url}
```

The server name is deliberately `bcs`: Gateway uses it as the signed Principal
audience, matching the BCS verifier contract.

## Error handling

- Export validation errors fail before publication and leave the current
  artifact untouched.
- Backward-incompatible schema changes are rejected by the existing Gateway
  compatibility gate unless an explicit coordinated override is supplied.
- Invalid BCS trust configuration fails startup; it never installs a permissive
  verifier.
- Invalid request Principals fail with 401 before any V1 Application service is
  invoked.
- Unknown Gateway domains remain 404 and are never forwarded.

## Verification

Focused automated evidence must cover:

- deterministic JSON bytes, 32 operations, collaboration-only paths, and no
  external references;
- dump/gate/publish of the BCN artifact;
- `collaboration` domain resolution to server `bcs` without rewrite;
- Gateway signing with audience `bcs` and stripping forged inbound Principal
  headers;
- BCS production Router reachability plus missing/invalid Principal 401;
- one representative GET and one body-carrying POST/PATCH through Gateway;
- Gateway `/openapi.json` contains BCN paths while Backend/BaaS paths remain;
- old `/openapi/v1/bots/collaboration/**` and
  `/openapi/v1/group-sessions/**` paths remain absent.

Full route inventory, representative schema/error serialization conformance,
immutable compatibility baselines, component collision detection, and expanded
live E2E remain tracked by #700.
