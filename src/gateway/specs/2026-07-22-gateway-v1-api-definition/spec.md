# Gateway v1 External API — Definition

## Summary

A new external API, exposed through the API Gateway, that lets a **third-party
integrator provision and configure agents** on our platform to build their own agent
product. This deliverable defines the **API contract only** — the resource groups,
operations, request/response shapes, and per-operation auth requirements — as a served,
auto-generated OpenAPI document. It contains **no business logic** and **no backend
wiring**; those are later passes.

## Motivation

We are opening the platform to third-party developers who will integrate our APIs to
build their own agent platform. Today's APIs are first-party (they assume a logged-in
person inside our identity system) and carry implementation-shaped concepts (cron,
service-bot "stages", engine-passthrough payloads, source-specific handling of things
like Yuque). A previous redesign attempt stayed bound to the current implementation.

We need a clean, stable, external-facing contract a new client can integrate against —
delivered as a definition first, so the gateway team, the backend team, and the client
can all work against one agreed surface in parallel. The contract is the unblocker.

## User Stories

- As a **developer integrating our API**, I want to call the gateway as an authenticated
  user, so that my calls carry that user's identity and tenant and are owned by that user.
- As a **third-party developer**, I want to create, list, inspect, update, restart, and
  delete agents, so that I can manage the agents behind my product.
- As a **third-party developer**, I want to edit an agent's identity/behavior files, attach
  knowledge (files and links), connect tools (MCP), install skills, and schedule recurring
  routines, so that I can fully configure what an agent is and can do.
- As a **third-party developer**, I want to bind an agent to an external channel, so that my
  agent can operate on that channel.
- As a **third-party developer**, I want to read a single, self-describing OpenAPI document
  and know, per endpoint, exactly what permission (scope) it requires, so that I can
  generate a client and request only the access I need.
- As the **gateway/backend teams**, we want one agreed contract with per-route auth metadata,
  so that we can implement gateway routing and backend endpoints against a fixed target.

## Acceptance Criteria

- [ ] The gateway application serves a complete OpenAPI document covering all **7 groups**
      under the base path **`/openapi/v1`**: `bots`, `identity`, `resources`, `mcp`,
      `routines`, `skills`, `channels`.
- [ ] Every operation declares its **auth requirement** (a user principal) using the agreed
      per-route security metadata; no exposed operation is missing a declaration. (The specific
      scope vocabulary is out of scope this session.)
- [ ] Every operation's success and error responses use the standard envelope
      `{code, message, data, request_id}`, where `code` is a 6-digit number (HTTP status +
      3-digit business subcode) and `message` is English.
- [ ] Identity is modeled per the gateway auth design: the caller is an **authenticated user**
      (a user principal); resource ownership resolves to that **user (their entity id) within
      their tenant**.
- [ ] The contract carries **no `stage` / service-bot** concept and no chat/connection
      handshake, and is shaped so a future immutable-version/snapshot model can be added
      **without breaking changes** (an agent is always addressed as its live definition).
- [ ] Scheduling is exposed as **`routines`** (not "cron"); a routine's trigger is a nested
      object so non-schedule triggers can be added later without a breaking change.
- [ ] Resources are a **single unified abstraction** over files and links (a Yuque document
      is a link-type resource), addressed uniformly rather than per-source.
- [ ] The 7 groups expose (at product level) these operations:
  - **bots** — create (may return "created" or "needs user authorization"), poll
    authorization status, list (filter + paginate), get, update, delete, restart, get
    runtime status, check name availability, get creation quota ceiling, get agent passport,
    read engine config, write engine config.
  - **identity** — list an agent's identity files (with existence), read one file, write one
    file (file type restricted to a fixed whitelist).
  - **resources** — list, create (type = file / link / folder), get, update, delete,
    download, preview, check name availability, upload a file.
  - **mcp** — list marketplace servers, get a server's detail, check the caller's permission
    for a server, list tenants, read the caller's unified server config, write the caller's
    unified server config.
  - **routines** — list, create, get, update, delete, run now, list run history.
  - **skills** — list the skill catalog, get a skill's detail, list an agent's installed
    skills, add a skill to an agent, remove a skill from an agent.
  - **channels** — list, create (DingTalk), get, full update, toggle enabled/disabled state,
    delete.
- [ ] Handler bodies are stubs (they do not call backend/engine and are not expected to
      return real data); the deliverable is the contract, verified by the served OpenAPI.

## In Scope

- The API **definition** for the 7 groups above, served as auto-generated OpenAPI.
- The **global contract**: base path, the response envelope, the error-code scheme, the
  auth model (authenticated user identity + tenant), and the pagination convention.
- A **minimal skeleton** sufficient to serve the document (routes registered, request/
  response shapes and per-route security metadata attached, stubbed handlers).

## Out of Scope

- **Any business logic / implementation** behind the endpoints, and **any backend
  endpoints** (a later pass).
- Gateway plumbing owned by others: auth-strategy resolvers, Principal signing/verification,
  and the route-security compiler/CI gate (we conform to the metadata format; we don't build
  the compiler).
- Deferred groups: **conversations/chat** (depends on engine, not backend), collaborators &
  edit-lock, render-screens, bot-public/friends, and the versions/snapshot APIs.
- The **scope vocabulary / taxonomy** (which scope strings each route requires) — deferred;
  routes declare only that they require a user principal.
- Third-party **app-principal** access (an app credential acting for opaque end-users) — the
  v1 backend surface operates on the **user principal**; app-level access is a separate
  gateway concern for a later pass.

## Open Questions

- **Skills model** — v1 exposes a simple "catalog + an agent's installed skills". The backend's
  real model is richer (skill-sets grouping skills + MCPs, categories). Is the simplified
  agent-centric surface acceptable for v1, or must skill-sets be first-class now?
- **Pending source docs** — `resources.md`, `skills.md`, and the real `auth-access.md` were
  not received; those group shapes are derived from the backend code and may need small
  adjustments when the docs arrive.
