# Plan: Config-Driven Gateway Forwarding

## Approach

Replace #389's hand-written stub routers with **domain-transparent forwarding**
plus a **generated, auto-refreshing OpenAPI doc**. There is **no per-operation
forwarding table and no whitelist**. The gateway needs only:

1. a **domain → server** map (leading path segment → upstream base URL),
2. the existing **prefix auth rules** (`route_security.yaml`, fail-closed default),
3. a **schema source** per domain (where the backend's published OpenAPI lives).

At request time a single catch-all resolves the domain from the leading path
segment (unknown domain → deny), authenticates via the existing `Authenticator`,
signs the established principal, and forwards the path **verbatim** to the
resolved server. The served `/openapi.json` is generated from the backend's own
published description (shapes from the backend, presented at verbatim paths) and
kept fresh by a **background refresh** that auto-adopts the latest published
version with a **last-known-good** fallback. The schema is a **doc-only** input —
routing, auth, and forwarding never read it — so its store being down degrades
only the doc page. The **backward-compat gate runs at publish time** (backend
release CI), so an incompatible description never reaches the store.

## Resolved decisions

- **Doc transport = auto-adopt latest.** Backend release CI publishes the
  generated description to a shared store (OSS); the gateway background-refreshes
  and adopts the latest — no promotion pointer, no gateway redeploy. Single-box /
  open-source reads a local committed file through the same pluggable seam.
- **Verbatim forwarding, version base included.** The whole path forwards
  unchanged; the backend serves `/openapi/v1/bots/…` itself and owns its versioned
  public contract. Domain resolution reads the leading segment after `/openapi/v1`.
- **Domain IA.** `bots` is the sole domain; agent-CRUD sits at the domain root,
  former groups become per-agent sub-paths (no `bots/bots`).
- **Exposure model.** No gateway whitelist. Two gates remain: (a) the domain must
  be configured or the request is denied; (b) every `/openapi/v1` endpoint verifies
  the gateway-signed JWT, so direct/unsigned access is rejected. The invariant
  "`/openapi/v1/**` is external-only" is enforced by a backend-side test.
- **Auth = existing prefix rules, reused as-is.** New endpoints inherit the
  domain default automatically; only non-default auth adds a rule. (Note the
  per-op-auth "fold" considered earlier is **not** done — `route_security.yaml`
  stays the auth config.)

## Affected Components

- `src/gateway/…/adapters/web/` — replace the seven group routers with one
  catch-all forwarding entrypoint + a generated, background-refreshed
  `/openapi.json`.
- `src/gateway/…/core/forwarding/` (new) — domain resolver (reuse the segment
  matcher/specificity in `core/authn/_route_security.py`) and the OpenAPI
  generator.
- `src/gateway/…/spi/` + `plugins/` (new) — `Forwarder` SPI (bare = httpx);
  `SchemaCatalog` SPI (bare = local file, enterprise flavor = OSS + refresh);
  `PrincipalSigner` seam used on the forward path (bare = HMAC per auth-design
  §7.1).
- `src/gateway/…/bootstrap/` — compose forwarder, domain map, schema catalog,
  signer; keep the existing `Authenticator` wiring.
- `src/gateway/configs/` — new `upstreams.yaml` (domain→server + schema source).
  `route_security.yaml` stays (auth prefix rules). No per-op forwarding file.
- `src/backend/…/adapters/http/` — **move externally-exposed routers to serve
  `/openapi/v1/bots/…` directly**; add a CI `dump_openapi()`; add the
  `/openapi/v1` = external-only invariant test; **verify the gateway JWT** on
  those routes (backend side of §7.1).
- `src/backend` release CI — publish `app.openapi()` to the store after the
  publish-time backward-compat gate passes.

## Data Model Changes

None. No tables, no migrations. New state is config files + the published OpenAPI
artifact (in the store / a local file).

## API / Interface Changes

**`configs/upstreams.yaml`** (the only new config):

```yaml
domains:
  bots:
    server: agentclaw
    schema: { source: oss, url: "oss://…/bots/openapi-latest.json", refresh_seconds: 300 }
    # single-box flavor instead:  schema: { source: file, path: schemas/bots.openapi.json }
servers:
  agentclaw: { base_url: "${AGENTCLAW_URL}" }   # env-overridable per the forwarding doc §1
```

**`configs/route_security.yaml`** — unchanged prefix-rule shape; `/**` default +
non-default overrides only.

**Runtime request path** — one catch-all: leading segment → domain (no match →
`404`, not an open proxy) → `Authenticator.authenticate` (fail-closed) → sign
principal → forward path **verbatim** to `server.base_url` with the JWT → stream
response through the standard envelope.

**Doc path** — `GET /openapi.json` served from the in-memory description (filtered
to `/openapi/v1/<domain>` paths, `x-avernet-security` attached from the auth
rules). A background task re-reads the source every `refresh_seconds`; on failure
or malformed content it keeps the last-known-good copy.

**No change** to `Principal` establishment/enforcement (`core/authn/_runner.py`,
strategies). The forward path newly *uses* the signing seam; key
management/rotation (§7.1) stays a separate workstream.

## Key Files & Functions

- `core/forwarding/_domains.py` (new) — `DomainMap.from_yaml`;
  `resolve(path) -> Server | None` off the leading segment.
- `core/forwarding/_openapi.py` (new) — `generate_openapi(description, rules) ->
  dict`: keep `/openapi/v1/<domain>` paths, attach security, return the doc.
- `spi/forwarder/…` + `plugins/forwarder/bare/…` (new) — `Forwarder.forward(request,
  target, principal_token) -> Response`, httpx streaming.
- `spi/schema_catalog/…` + `plugins/schema_catalog/bare/…` (new) —
  `SchemaCatalog.current(domain) -> dict`; bare = local file; background refresher
  holds last-known-good.
- `spi/signer/…` + `plugins/signer/bare/…` (new) — `PrincipalSigner.sign(principal)
  -> str` (bare HMAC).
- `adapters/web/_forward.py` (new) — catch-all route (depends on `require_principal`)
  + envelope mapping.
- `adapters/web/app.py:74` — drop `include_all`; mount the catch-all; override
  `app.openapi` to serve the generated doc; start the refresher on lifespan.
- `adapters/web/routers/**` — **deleted** (the seven #389 stub groups).
- `src/backend/.../adapters/http/app.py` — `dump_openapi()`; router prefix move;
  gateway-JWT verification dependency.
- `src/backend/tests/community/contracts/gateway/test_public_namespace.py` (new) —
  assert no `/openapi/v1` route lacks the auth/verification marker.
- backend release CI step — compat-gate then publish `app.openapi()`.

## Dependencies

- Promote **`httpx`** to a runtime dep (`pyproject.toml:7`) for the bare forwarder.
- The **OSS reader** lives in the enterprise flavor only; community stays
  file-based (no cloud SDK in the open-source deps).
- Breaking-change detection: a focused in-repo Python checker over two OpenAPI
  JSONs (removed op/field, optional→required, type/`default`/enum change).
  `oasdiff` (Go) is the richer alternative but adds a non-Python toolchain —
  deferred.

## Risks & Mitigations

- **Risk:** Exposure control moves from the gateway to the backend (anything under
  `/openapi/v1` is reachable). **Mitigation:** the namespace-invariant test (no
  internal route under `/openapi/v1`), the domain-must-be-configured gate, and the
  mandatory gateway-JWT verification on every such route (unsigned/direct access
  rejected).
- **Risk:** Signing is load-bearing now — if the backend doesn't verify the JWT,
  the exposure argument collapses. **Mitigation:** the backend-side verifier is a
  hard **sequencing dependency** (Rollout); forwarding does not go live until it is
  in place.
- **Risk:** Runtime proxy edge cases (SSE/streaming, timeouts, hop-by-hop headers,
  large uploads). **Mitigation:** bare forwarder streams both directions;
  timeout/retry is a per-server config field; SSE + upload each get an explicit test.
- **Risk:** Schema store unreachable/malformed. **Mitigation:** doc-only input +
  last-known-good; never blocks routing/auth; the doc endpoint never hard-fails.
- **Risk:** Backend route move breaks existing `/api/…` callers. **Mitigation:**
  dual-mount `/api/…` and `/openapi/v1/…` during transition; backend-owned step
  sequenced before gateway cutover.
- **Risk:** New endpoint silently inherits the default auth (fine now; wrong for a
  future sensitive route once scopes exist). **Mitigation:** noted as a follow-up
  when the scope vocabulary lands; not a blocker while scopes are deferred.
- **Risk:** Scope creep into a full production proxy. **Mitigation:** MVP forwarder;
  connection-pool tuning / circuit breaking are follow-ups.

## Alternatives Considered

- **Per-operation whitelist (the earlier version of this plan).** Safer default
  exposure, but every new API needs a gateway config change + release — the exact
  cost we set out to remove. Rejected in favor of domain-transparent forwarding +
  the namespace invariant + JWT.
- **Committed pin read at boot.** Simple, but the doc only updates on a gateway
  redeploy. Rejected for the auto-refresh store.
- **Gateway fetches the *live* backend `/openapi.json`.** Adds a boot/runtime
  dependency on the live app and mid-rollout version ambiguity. Rejected: the store
  holds an immutable per-release snapshot instead.
- **`oasdiff` for compatibility.** Richer, but a Go binary in a Python CI path;
  deferred behind the in-repo checker.

## Rollout

- Sequence: **(1)** backend serves `/openapi/v1/bots/…` (dual-mounted with `/api/…`),
  verifies the gateway JWT, and publishes its description; **(2)** gateway lands the
  domain map, forwarder, signer, and background-refreshed doc; **(3)** cut over —
  catch-all + generated doc go live, then the #389 stub routers are deleted. (2)/(3)
  must not precede the backend JWT verification in (1).
- Single domain (`bots`) wired; the map admits more domains without code change.
- Parity: the generated `/openapi/v1` doc ⊇ the #389 operation set for carried-over
  ops (snapshot check).
- No DB migration; config-only gateway deploy.

## Test Strategy

- **Unit:** domain resolution (unknown domain → deny); verbatim forward target;
  OpenAPI generator (namespace filter, security attach); background refresh adopts
  a new version and falls back to last-known-good on failure; compatibility checker
  (additive passes, each breaking class fails); signer round-trip.
- **Integration:** catch-all against a stub upstream (httpx transport mock) — auth
  reject-before-forward, unknown-domain 404, JWT attached on forward, envelope on
  success/error, one SSE and one upload path.
- **Contract/CI (backend):** `app.openapi()` dumps deterministically; the
  namespace-invariant test; the publish-time compat gate (additive vs breaking).
- Must stay green: `ruff`, `mypy --strict`, `pytest -m "not e2e"`.
