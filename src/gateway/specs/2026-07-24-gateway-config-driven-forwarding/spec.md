# Config-Driven Gateway Forwarding

## Summary

Replace the gateway's current approach — where every externally-exposed API is
re-declared by hand on the gateway (routes, request/response shapes, auth) purely
to generate documentation and drive forwarding — with a **declarative
configuration** that is the single source of truth for how a request is
authenticated, which backend server it is routed to, and how the public API
document is produced. Request/response **shapes are no longer rewritten on the
gateway**; they are taken from each backend's own published API description. This
removes the "every API is defined twice" duplication while keeping one stable,
self-describing external contract.

## Motivation

Today the gateway defines a stub endpoint for every backend operation so that
(a) it can serve an OpenAPI document to third-party integrators and (b) its
per-route auth requirements are known. The real operation is then defined again
on the backend. Every API is written twice, and the two copies drift. The team
has agreed to invert this: the backend owns the operation and its shapes; the
gateway owns only the *gateway concerns* — auth, routing target, path exposure —
expressed as configuration. The published document becomes a generated artifact,
not hand-maintained code. This is the unblocker for opening the platform to
third-party developers without paying the double-write cost on every change.

## User Stories

- As a **gateway maintainer**, I want to expose or change a forwarded API by
  editing configuration instead of writing endpoint code, so that I don't
  re-declare an operation the backend already owns.
- As a **backend developer**, I want my operation's request/response shapes to
  flow into the public document from my own service's API description, so that I
  define each shape once and the published contract can't silently drift from it.
- As a **third-party integrator**, I want to read one self-describing API
  document where every operation states the permission it requires, so that I can
  generate a client and request only the access I need.
- As a **gateway operator**, I want the top-level segment of a request path to
  determine which backend server handles it, so that routing topology is
  configurable and a server can be split out later without changing client-facing
  URLs.
- As a **platform owner**, I want a published API version to keep evolving with
  backward-compatible changes while breaking changes are blocked automatically,
  so that we can co-develop with a partner and later serve external clients
  safely on the same version.

## Acceptance Criteria

- [ ] The gateway contains **no hand-written endpoint definition** per forwarded
      operation and **no per-operation forwarding whitelist**. Onboarding a new
      backend API (served under the public namespace) requires **no gateway config
      change and no gateway release**.
- [ ] Forwarding is **domain-transparent**: a request whose leading path segment
      matches a configured **domain** is forwarded to that domain's server; the
      path is sent **verbatim** (identity, version base and domain segment
      included). A request whose leading segment matches **no** configured domain
      is **denied** — the gateway forwards only into known domains, never acts as
      an open proxy.
- [ ] A single configuration maps each **domain** to a target server; changing a
      domain's server requires no change to any client-facing path.
- [ ] Auth is resolved from **prefix rules with a fail-closed default**: every
      request resolves to an auth requirement via the most-specific matching rule
      (ultimately the `/**` default) and is authenticated before forwarding. A new
      endpoint therefore inherits its domain's auth requirement automatically; only
      endpoints needing **non-default** auth add a rule. (Conveying the established
      identity downstream — the signed-principal / JWT seam — is owned by the auth
      workstream, not delivered here.)
- [ ] The public path namespace (`/openapi/v1/**`) is, by invariant, the
      **external contract only**: the backend never mounts internal-only routes
      there, and this is enforced by a backend-side check. This invariant — together
      with the auth workstream's JWT verification on those routes — is the exposure
      gate, not a gateway whitelist.
- [ ] The gateway serves a single OpenAPI document whose request/response shapes
      come from the backend's own published API description (not authored on the
      gateway) and are presented at their verbatim paths. An operation with an
      explicit upstream override is re-keyed to its client-facing path.
- [ ] The backend's operations are exposed under a single **`bots`** domain, and
      the backend **serves those same paths** (no per-operation path rewrite in the
      default case).
- [ ] On each backend release, CI **publishes** the backend's generated API
      description as an artifact to an **object store** (vendor-neutral —
      S3 / MinIO / GCS / OSS / …). The gateway
      **auto-adopts the latest** published description by refreshing it in the
      background and serving the doc from an in-memory copy; a new release's doc
      appears **without a gateway redeploy**.
- [ ] The published description is a **doc-only input**: routing, auth, and
      forwarding never read it, so the shared store being unreachable or stale
      degrades **only** the doc endpoint, never live traffic. On fetch failure or a
      malformed artifact the gateway keeps serving the **last known-good** copy.
- [ ] The **backward-compatibility gate runs at publish time** (backend release
      CI), comparing the new description against the currently-published one, and
      **fails the release** on a breaking change (field/operation removed, optional
      input made required, type/default changed) unless it is an explicit new major
      version; backward-compatible changes publish freely.
- [ ] The schema source is **pluggable**: the single-box profile reads a local
      committed description file; **any deployed edition — corp or community —**
      reads from an **object store** (vendor-neutral) through the same seam. The
      object-store reader is a flavor, not an enterprise-only capability.

## In Scope

- The domain → server mapping configuration and domain-transparent route
  resolution (deny requests to unknown domains).
- The prefix-based, fail-closed auth resolution (default rule + non-default
  overrides). The forwarder exposes the seam where the auth workstream's signer
  attaches the downstream principal token.
- Generating the served public API document from the backend's published API
  description, presented at verbatim paths (with an optional upstream override).
- The publish-and-refresh mechanism: backend release CI publishes the generated
  description to a shared store; the gateway auto-adopts the latest via background
  refresh with last-known-good fallback; and the publish-time backward-compat gate.
- Making the schema source pluggable (local committed file for single-box;
  vendor-neutral object-store reader for any deployed edition — corp or community).
- Collapsing the backend's exposed operations under the `bots` domain, including
  moving the backend's routes so it serves those client-facing paths directly, and
  a backend-side check enforcing the `/openapi/v1` = external-only invariant.
- Retiring the hand-written per-operation endpoint stubs (and the per-operation
  forwarding whitelist) that exist only to generate documentation / gate exposure.

## Out of Scope

- **Business logic / backend endpoint implementations** — owned by the backend
  team; this work only forwards to them.
- The **scope vocabulary** (which specific permission string each operation
  requires) — operations declare that they require a user principal; the taxonomy
  is a separate effort.
- Third-party **app-principal** access (an app credential acting for opaque
  end-users) — the forwarded surface operates on the user principal.
- Deferred API groups that do not forward to the backend (e.g. conversation/chat,
  which targets a different server) — the model must not preclude them, but they
  are not delivered here.
- Onboarding a **second** backend server / domain beyond `bots`; the design must
  allow it, but only `bots` is wired up now.
- **Principal signing/verification (the gateway↔backend JWT), incl. key
  management/rotation** (auth-design §7.1) — owned by the auth workstream. This
  feature exposes the forwarder seam it plugs into and depends on it for go-live,
  but does not implement it.

## Resolved Decisions

- **Doc transport / freshness** → backend release CI publishes the generated
  description to a shared store; the gateway **auto-adopts the latest** via
  background refresh (no promotion pointer, no gateway redeploy). Single-box reads
  a local committed file via the same pluggable seam.
- **Domain IA** → `bots` is the sole domain; agent-CRUD sits at the domain root
  and former groups become per-agent sub-paths (no `bots/bots`). The `/openapi/v1`
  namespace is external-only, enforced backend-side.
- **Exposure model** → no per-operation whitelist; forwarding is domain-transparent
  with fail-closed prefix auth, so onboarding a new API needs no gateway release.
- **Breaking-change policy** → gate runs at publish time and blocks breaking
  changes to the published description; an explicit new major version is the way to
  make one.

## Open Questions

- **Scope inheritance (future).** Once the scope vocabulary lands, a new endpoint
  silently inheriting the domain's default auth may be under-protected if it is
  sensitive. A mechanism to force explicit scopes on such routes will be needed —
  out of scope now (scopes are deferred), noted so it is not lost.
