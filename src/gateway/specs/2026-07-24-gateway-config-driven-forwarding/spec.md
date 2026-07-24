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

- [ ] The gateway no longer contains a hand-written endpoint definition per
      forwarded operation; forwarded operations are described entirely by
      configuration.
- [ ] Each forwarded operation's configuration comprehensively specifies: the
      auth requirement, the target domain, the public request path (and method),
      the upstream request path (and method) when it differs, and whether the
      operation appears in the published document.
- [ ] A separate configuration maps each **domain** (the top-level path segment)
      to a target backend server; changing a domain's server requires no change
      to any operation's client-facing path.
- [ ] An incoming request is routed to a server strictly by resolving its domain
      through that map; a request whose path is not covered by configuration is
      **denied** (the gateway is never an open proxy).
- [ ] Every forwarded operation resolves to exactly one auth requirement; a
      request that fails it is rejected before forwarding, and the caller identity
      is established at the gateway exactly as it is today (no change to the trust
      model between gateway and backend).
- [ ] The gateway serves a single OpenAPI document that covers all publicly
      exposed forwarded operations. Its per-operation request/response shapes come
      from the backend's own published API description — they are not authored on
      the gateway.
- [ ] The published document includes an operation **only if** its configuration
      marks it public; a backend operation that exists but is not configured as
      public does not appear.
- [ ] The backend's operations are exposed under a single **`bots`** domain.
- [ ] The gateway obtains a backend's API description as a **versioned, pinned
      artifact** produced when the backend is released; the gateway does not
      require the backend to be reachable at request time in order to route,
      authenticate, or forward.
- [ ] Continuous integration **fails** when a referenced backend operation changes
      incompatibly (e.g. a field or operation is removed, an optional input
      becomes required, a type or default changes) unless the change is an
      explicit new major version; backward-compatible changes (new operations, new
      optional fields) are allowed to flow into the published version.
- [ ] Continuous integration **fails** when configuration references an operation
      that does not exist in the pinned backend description, so config and backend
      cannot drift apart unnoticed.

## In Scope

- The declarative configuration format for forwarded operations (auth, routing,
  path mapping, public exposure).
- The domain → server mapping configuration and domain-based route resolution.
- Generating the served public API document from configuration + the pinned
  backend API description.
- The mechanism by which a backend's API description becomes a pinned artifact the
  gateway consumes, and the CI gates that (a) detect breaking changes to
  referenced operations and (b) detect config/backend drift.
- Collapsing the backend's exposed operations under the `bots` domain.
- Retiring the hand-written per-operation endpoint stubs that exist only to
  generate documentation.

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
- The **auto-bump automation** that opens a pull request when a new backend
  artifact is published — the pin and the CI gates are in scope; automating the
  version bump is a follow-up.

## Open Questions

- **Artifact transport for a single-box deployment.** In the open-source /
  single-box profile there may be no artifact registry. Is committing each
  backend's generated API description into a shared location (read at gateway
  build) acceptable as the baseline, with a registry as an enterprise overlay?
- **Domain naming.** `bots` is both the chosen domain name and, previously, one
  resource group among several (identity, resources, skills, routines, channels,
  mcp). Under the domain model those groups become sub-paths of `bots`. Is that
  the intended information architecture, and does the agent-CRUD group keep a
  clean path (avoiding an awkward `bots/bots`)?
- **Breaking-change policy while pre-GA.** During single-partner co-development,
  are coordinated breaking changes to a published version permitted (CI override
  by agreement), or should breaking changes always require a new major version
  even before GA?
