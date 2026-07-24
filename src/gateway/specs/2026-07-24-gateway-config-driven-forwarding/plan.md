# Plan: Config-Driven Gateway Forwarding

## Approach

Make declarative configuration the single source of truth for the gateway's
external surface. A **forwarding table** (one entry per exposed operation: path,
auth requirement, public flag, and an *optional* upstream-path override) plus a
**domain→server map** (domain segment → upstream base URL) replace the
hand-written stub routers from #389. Paths forward to the upstream **verbatim by
default** — the backend serves the same paths it is exposed under, so there is no
per-operation rewrite in the common case; the override exists only for the rare
op whose backend path genuinely differs. At request time a single catch-all
entrypoint resolves the operation from the table, authenticates via the existing
`Authenticator`, and forwards the (verbatim, or overridden) path to the resolved
server. The served OpenAPI is **generated** by joining the forwarding table with
each upstream's own committed OpenAPI artifact — since paths are identity by
default, generation is mostly filter-to-public + attach-security, with path
re-keying only for overridden ops. Two CI gates keep config and the pinned
artifact honest: reference-resolution (drift) and backward-compatibility
(breaking-change).

Because backend and gateway share this monorepo, the "pinned upstream artifact"
is a committed file regenerated from the backend's FastAPI app; independent
*deploy* cadence is preserved because the gateway never calls the live backend to
build its surface.

## Resolved open questions (defaults — override in review)

- **Single-box artifact transport** → commit each upstream's generated OpenAPI at
  `src/gateway/configs/upstreams/<domain>.openapi.json`, read at gateway
  build/startup. A registry-backed loader is a later enterprise overlay (same SPI).
- **Domain IA / `bots`** → public paths are `/openapi/v1/<domain>/…`; `<domain>`
  (first segment after the `/openapi/v1` version base) selects the server. `bots`
  is the sole domain now. Agent-CRUD lives at the domain root (`/openapi/v1/bots`,
  `/openapi/v1/bots/{id}`); former groups become sub-paths
  (`/openapi/v1/bots/{id}/identity`, `/openapi/v1/bots/{id}/resources`, …). No
  `bots/bots`.
- **Verbatim forwarding scope** → the *entire* path forwards unchanged, version
  base included: the backend serves `/openapi/v1/bots/…` itself and owns its
  versioned public contract. Domain resolution reads the segment after
  `/openapi/v1`. (If we later prefer the gateway to own the version base and strip
  it before forwarding, that is a one-line change in the matcher — flagged, not
  chosen.)
- **Pre-GA breaking-change policy** → CI blocks breaking changes to referenced
  ops by default; an explicit per-PR marker (`ALLOW_BREAKING_CHANGE=<reason>` in
  a `.gateway-breaking-change` file or PR label) permits a coordinated break and
  is recorded in the diff. Removed once GA.

## Affected Components

- `src/gateway/src/gateway/community/adapters/web/` — replace per-group routers
  with the catch-all forwarding entrypoint + generated-OpenAPI serving.
- `src/gateway/src/gateway/community/core/` — new `forwarding/` core: the
  forwarding table model, matcher (reuse the §8.3 specificity rules already in
  `core/authn/_route_security.py`), and the OpenAPI generator.
- `src/gateway/src/gateway/community/spi/` + `plugins/` — new `Forwarder` SPI
  (bare = httpx) and `UpstreamCatalog` SPI (bare = committed-file loader).
- `src/gateway/src/gateway/community/bootstrap/` — compose the forwarder, table,
  and catalog; extend `build_authenticator` wiring to share the one table.
- `src/gateway/configs/` — new `forwarding.yaml` (table) + `upstreams.yaml`
  (domain→server) + `upstreams/bots.openapi.json` (pinned artifact). Fold the
  standalone `route_security.yaml` into the table (auth becomes a per-op field).
- `src/backend/…/adapters/http/` — add a CI-invokable dump of `app.openapi()`,
  **and move the externally-exposed routers to serve the client-facing paths
  directly** (`/openapi/v1/bots/…`) so verbatim forwarding needs no rewrite. This
  is the one non-trivial backend code change; internal/non-exposed routes are
  untouched. (Existing `/api/…` routes may stay during transition; see Rollout.)
- `src/backend/tests/community/contracts/gateway/` — extend to (a) regenerate and
  compare the pinned artifact (drift) and (b) run the breaking-change gate.

## Data Model Changes

None. No database tables or migrations. All new state is configuration files and
a committed OpenAPI artifact.

## API / Interface Changes

**Forwarding table — `configs/forwarding.yaml`** (illustrative):

```yaml
operations:
  # Default: path forwards verbatim; no upstream field needed.
  - method: POST
    path:   /openapi/v1/bots
    auth:   [ first_party_user ]        # was route_security.yaml; OR-list, §8.1 shape
    public: true
  - method: GET
    path:   /openapi/v1/bots/{id}
    auth:   [ first_party_user ]
    public: true
  # Exceptional: a backend path that genuinely differs and can't be moved.
  - method: GET
    path:   /openapi/v1/bots/{id}/passport
    upstream: { method: GET, path: /legacy/agent/{id}/passport }   # optional override
    auth:   [ first_party_user ]
    public: true
```

**Domain→server map — `configs/upstreams.yaml`**:

```yaml
domains:
  bots: { server: agentclaw, artifact: upstreams/bots.openapi.json }
servers:
  agentclaw: { base_url: "${AGENTCLAW_URL}" }   # env-overridable per §1 of the forwarding doc
```

**Runtime entrypoint** — one catch-all replaces all group routers: resolve op by
(method, path) → 404 if unconfigured (fail-closed, never open-proxy) →
`Authenticator.authenticate` → forward the path verbatim (or apply the override) →
`Forwarder` issues the upstream call → stream response back through the standard
envelope.

**Generated OpenAPI** — `GET /openapi.json` is served from the generator (config ⋈
pinned artifact), not FastAPI's route introspection. Only `public: true` ops
appear, presented at their (verbatim) paths; each carries `x-avernet-security`
from its `auth` field. Path re-keying happens only for ops with an override.

**No change** to `Principal` establishment/enforcement (`core/authn/_runner.py`,
strategies) or the gateway↔backend trust model: identity is built exactly as
today and conveyed downstream via the existing bare seam; hardened signing
(auth-design §7.1) remains a separate workstream.

## Key Files & Functions

- `core/forwarding/_table.py` (new) — `ForwardingTable.from_yaml`; `resolve(method,
  path) -> Operation | None`; `Operation.upstream_path` returns the path verbatim
  unless an override is set. Reuse `_route_security.py` segment matcher/specificity.
- `core/forwarding/_openapi.py` (new) — `generate_openapi(table, catalog) -> dict`:
  for each public op, pull the upstream operation object + referenced `components`
  from the pinned artifact, key it under its path (verbatim; re-keyed only for
  overridden ops), attach security.
- `spi/forwarder/_protocols.py` + `plugins/forwarder/bare/_plugin.py` (new) —
  `Forwarder.forward(request, target) -> Response`, httpx-backed.
- `spi/upstream/_protocols.py` + `plugins/upstream/bare/_plugin.py` (new) —
  `UpstreamCatalog.openapi(domain) -> dict` from the committed file.
- `adapters/web/_forward.py` (new) — the catch-all FastAPI route + envelope mapping.
- `adapters/web/app.py:74` — drop `include_all`; mount the catch-all; override
  `app.openapi`.
- `adapters/web/routers/**` — **deleted** (the seven stub groups from #389).
- `bootstrap/_authn.py:56` — `_load_route_security` reads the forwarding table's
  `auth` fields instead of a separate `route_security.yaml`.
- `src/backend/.../adapters/http/app.py` — add `dump_openapi()` used by CI.
- `src/backend/tests/community/contracts/gateway/test_upstream_artifact.py` (new)
  — regenerate + assert pin match; run compatibility gate.

## Dependencies

- Promote **`httpx`** from dev to runtime (`pyproject.toml:7`) for the bare forwarder.
- Breaking-change detection: prefer a focused in-repo Python checker over the two
  OpenAPI JSONs (removed op/field, optional→required, type/`default`/enum change),
  restricted to referenced ops. `oasdiff` (Go) is the richer alternative but adds
  a non-Python toolchain — deferred.

## Risks & Mitigations

- **Risk:** Runtime proxy edge cases (streaming/SSE bodies, timeouts, header
  hop-by-hop rules, large uploads). The forwarding doc includes SSE routes.
  **Mitigation:** bare forwarder streams request/response; SSE/upload ops are
  flagged in the table and covered by explicit tests; timeout/retry policy is a
  per-server config field with sane defaults.
- **Risk:** With verbatim forwarding the join key is (method, path); a backend
  route rename silently orphans a config entry and changes the public URL.
  **Mitigation:** the drift gate fails when a referenced (method, path) is absent
  from the pinned artifact; the compatibility gate treats a public-path rename as
  a breaking change. Overridden ops match on the override target.
- **Risk:** Moving backend routers to `/openapi/v1/bots/…` changes the paths
  existing internal callers use (`/api/…`).
  **Mitigation:** keep the old `/api/…` routes mounted during transition (dual-mount
  or a compatibility router); the migration is a backend-owned step sequenced
  before the gateway cutover (see Rollout).
- **Risk:** Losing the gateway's standalone contract test (it no longer authors
  the surface). **Mitigation:** the generated-OpenAPI is snapshot-tested, and the
  compatibility gate now covers the *real* backend ops — strictly stronger.
- **Risk:** Downstream identity conveyance is still the bare (unsigned) seam.
  **Mitigation:** unchanged from today and explicitly out of scope; the seam is an
  SPI so §7.1 signing drops in without touching forwarding.
- **Risk:** Scope creep into a full production reverse proxy.
  **Mitigation:** deliver a correct MVP forwarder; call out productionization
  (connection pooling tuning, circuit breaking) as follow-ups.

## Alternatives Considered

- **Component-owned OpenAPI merged by reference at build (my earlier proposal).**
  Same single-source outcome, but requires each component to publish + the gateway
  to ingest N specs. The config-table model is a thinner step and matches the
  team's decision; the pinned-artifact join is retained from it.
- **Keep hand-written routers, dedupe via codegen from backend.** Still leaves the
  gateway authoring shapes; codegen churn on every backend change. Rejected.
- **Runtime fetch of upstream OpenAPI.** Live docs, but adds a boot dependency and
  turns merge failures into runtime errors. Rejected for the pinned artifact
  (build-time), per the spec.
- **`oasdiff` for compatibility.** Richer, but a Go binary in a Python CI path;
  deferred behind a focused in-repo checker.

## Rollout

- Sequence: **(1)** backend adds routers at `/openapi/v1/bots/…` (dual-mounted
  with the existing `/api/…` during transition) and emits the pinned artifact;
  **(2)** gateway lands config + generator + forwarder; **(3)** cut over — the
  catch-all serves the generated OpenAPI, then the #389 stub routers are deleted
  in the same PR. Verbatim forwarding depends on (1), so it leads.
- Single domain (`bots`) wired; the map admits more domains without code change.
- Backward-compat: the generated `/openapi/v1` surface must snapshot-match #389's
  served document for already-published ops (guarded by the compatibility gate).
- No DB migration; config-only deploy for the gateway.

## Test Strategy

- **Unit:** table resolution + specificity (incl. unconfigured→404); verbatim
  path forward + the override path; OpenAPI generator (public filter, component
  collection, security attach, override re-keying); compatibility checker
  (additive passes, each breaking class fails).
- **Integration:** catch-all with a stub upstream (httpx transport mock) — auth
  reject-before-forward, 404 on unconfigured, envelope on success/error, one SSE
  and one upload op.
- **Contract/CI (backend):** regenerate `app.openapi()` and assert it matches the
  committed `bots.openapi.json` (drift); run the breaking-change gate over
  referenced ops; assert every `forwarding.yaml` op resolves in the artifact.
- **Parity:** generated `/openapi/v1` document ⊇ the #389 operation set for the
  ops carried over.
- Must stay green: `ruff`, `mypy --strict`, `pytest -m "not e2e"`.
