# Engine Extension Relay

- **Status:** Draft (for review)
- **Date:** 2026-11-05
- **Modules:** backend (owner), gateway, engine adapter (unchanged in phase 1), docs
- **Plan:** [plan.md](plan.md) · **Tasks:** [tasks.md](tasks.md)

## Summary

Every engine (OpenClaw, Claude Code, Hermes, DeepSeek Harness, …) implements the
same **standard Engine API**: sessions, chat, MCP, skills, cron, approvals,
files, nodes, and so on. Beyond that, each engine has APIs of its own that only
make sense for that engine. Today the only way to expose one is to write it
into our codebase. This feature adds **one generic relay**. Avernet
authenticates the caller, checks the caller's access to the Bot, resolves the
Bot's active Engine, and forwards the request to an **Extension Server** that
the engine's owner runs and operates. The engine owner then adds
engine-specific APIs on their own server and ships them on their own schedule.

## Motivation

- Engine-specific APIs that are written into the backend, engine adapter, or
  gateway (`/api/openclaw/*`, the corp-only `aicoding` `/data/*` proxy) make
  every engine's roadmap depend on Avernet releases and reviews. The OSS
  codebase also accumulates code that most deployments never run.
- The Gateway already moved away from declaring every API twice
  (`apps/gateway/specs/2026-07-24-gateway-config-driven-forwarding`). This
  feature applies the same idea to engines: Avernet owns the cross-cutting
  concerns (identity, authorization, routing, limits, audit), and the engine
  owner owns the operations.
- Third-party integrators and our own workbench need a single, stable way to
  reach these APIs. Neither should learn where an engine is deployed or hold an
  engine's credentials. The standard engine-runtime Open API was built for the
  same reason (backend spec `2026-07-30-openapi-v1-engine-runtime-track-c`).

## Glossary

| Term | Meaning |
| --- | --- |
| **Standard Engine API** | The engine-neutral API every engine implements via the engine adapter's plugin Protocols and that Avernet exposes under `/openapi/v1/bots/{bot_id}/engine/*`, `/sessions`, … |
| **Engine Extension API** | An operation that exists only for one engine and is served by that engine's Extension Server. |
| **Extension Server** | An HTTP server owned and operated by an engine owner. It is registered with Avernet for exactly one engine type. |
| **Extension Manifest** | The Extension Server's published API description: its operations, and for each one the scope it requires and which callers may use it. It is the authority on what the relay forwards. |
| **Extension Assertion** | A short-lived token that Avernet signs and attaches to every relayed request. It is the only identity the Extension Server receives. |
| **Relay** | The Avernet component that authenticates, authorizes, and forwards requests to an Extension Server. It serves both surfaces below. |
| **Internal surface** | Avernet's first-party API (`/api/...`), used by our workbench through the first-party login session. |
| **Open surface** | The public API (`/openapi/v1/...`) behind the Gateway, used by users, third-party Apps, and Bots through the signed `X-Avernet-Principal`. |

## User Stories

- As an **engine owner**, I want to ship an engine-specific API by deploying it
  on my own server and publishing its manifest, so that I do not need an
  Avernet code change or release.
- As an **engine owner**, I want every relayed request to carry a verifiable
  statement of who is calling, for which Bot and tenant, and with which
  permissions, so that I never handle Avernet passwords, cookies, or API keys
  and can enforce my own access rules.
- As a **third-party App developer**, I want to call an engine's extension
  operations for a Bot I am authorized for, using the same credentials and base
  URL as the rest of the Open API, so that I don't integrate with each engine
  separately.
- As a **workbench user**, I want engine-specific panels in the UI to work for
  my Bot through my normal login, so that engine features feel native.
- As a **client developer**, I want to discover which extension operations a
  Bot's current engine offers, so that I can show or hide engine-specific
  features.
- As a **platform operator**, I want to register, disable, and rotate
  credentials for Extension Servers through configuration, and to cap their
  traffic, so that a misbehaving server cannot hurt the platform or other
  tenants.
- As a **security reviewer**, I want every relayed call to be audited and the
  relay to fail closed, so that it cannot be used as an open proxy or to escalate
  privileges.

## Acceptance Criteria

**Routing and scope**
- [ ] A request to `…/bots/{bot_id}/engine/ext/{path}` on either surface is
      forwarded to the Extension Server registered for that Bot's **active
      engine**. The forwarded path is `{path}` beneath the server's configured
      base, and the query string is forwarded unchanged.
- [ ] Each Extension Server is registered for one engine type and is chosen by
      the Bot's active engine, never by anything the caller supplies. A caller
      cannot pick a different engine's server or name an arbitrary host.
- [ ] The relay forwards only operations (method plus path template) that the
      Extension Manifest declares. Any other operation returns 404 without
      contacting the Extension Server. If the Bot's engine has no registered or
      enabled server, the relay returns 404 `engine_extension_not_available`.
- [ ] Path traversal (`..`, encoded `/` or `\`, empty segments) is rejected with
      400 before any matching is done.

**Authentication and authorization**
- [ ] On the Open surface, the caller is authenticated exactly as for any
      `/openapi/v1/bots/**` route (Gateway principal, verified by the backend).
      On the Internal surface, the caller is authenticated through the
      first-party session. Unauthenticated requests get 401.
- [ ] The caller must be authorized for the Bot under the same rules as the
      standard engine-runtime API (Owner or collaborator; an App needs a valid
      grant for the Bot). If the caller is not authorized, the relay returns a
      masked 404, the same response as for a missing Bot.
- [ ] Each manifest operation declares a scope (`read` or `write`) and an
      audience (`user`, `app`, `bot`, or a combination). A caller type that is
      not listed gets 403 without contacting the Extension Server. An App
      caller also needs a valid grant for the Bot. Today grants carry no
      scopes, so a granted App may call any operation whose audience includes
      `app`. The operation's scope still goes into the assertion, so the
      Extension Server can check it (see Open Question 2).
- [ ] The Extension Server receives **no** caller credentials. Cookies,
      `Authorization`, Avernet API keys, app tokens, and `X-Avernet-*` headers
      are removed before forwarding.
- [ ] Every relayed request carries one Extension Assertion. It is signed with an
      **asymmetric** key, verifiable against a published public key set, valid
      for at most 60 seconds, bound to one Extension Server (audience), and bound
      to the request's method and path. It names the caller, the caller type,
      the Bot, its tenant, its engine, the granted scope, and a unique request
      id. The symmetric key Avernet components share internally is never given
      to an Extension Server.
- [ ] Avernet publishes the public key set so that Extension Servers can verify
      assertions without a shared secret. Key rotation works with no failed
      requests: during the overlap window both the old and the new key are
      published.

**Forwarding behavior**
- [ ] Request bodies up to the configured limit (default 10 MiB) are forwarded.
      Larger bodies get 413 before forwarding.
- [ ] Upstream status codes and bodies pass through unchanged. `Set-Cookie`,
      hop-by-hop headers, and headers outside the allow-list are removed. Every
      response carries `X-Avernet-Relay-Source: upstream` or
      `X-Avernet-Relay-Source: relay`, so clients can tell a vendor error from
      an Avernet error.
- [ ] Server-Sent Events responses are streamed to the client without
      buffering.
- [ ] The relay does not follow redirects. A 3xx response is passed back as is,
      with a `Location` only if it points into the same Extension Server.
- [ ] If the server cannot be reached, the relay returns 502
      `engine_extension_unreachable`. If the upstream does not respond in time
      (default 30 s, or 10 min for streams), the relay returns 504
      `engine_extension_timeout`. Both use the standard Avernet error envelope.
- [ ] In deployed environments, the server URL must be `https`. URLs resolving to
      loopback, link-local, or private addresses are refused unless they are
      explicitly allow-listed. Single-box mode may use `http://127.0.0.1`.

**Limits and resilience**
- [ ] There are per-Extension-Server limits on concurrent requests and request
      rate, and a per-(tenant, Bot) rate limit. Requests over a limit get 429
      with `Retry-After` and are not forwarded. In phase 1 the limits apply per
      backend replica.
- [ ] After repeated upstream failures, a circuit breaker stops forwarding to
      that Extension Server for a cool-down period. Other engines and the
      standard Engine API are not affected.

**Discovery and documentation**
- [ ] `GET …/bots/{bot_id}/engine/extensions` returns the active engine's
      extension operations that this caller can use (method, path, summary,
      scope). If the engine has none, it returns an empty list.
- [ ] The Open API document lists the relay route and points to the discovery
      endpoint. Per-engine operations are documented by the manifest and are
      not copied into Avernet's committed schemas.

**Audit and observability**
- [ ] Each relayed call writes one audit record: request id, caller and caller
      type, tenant, Bot, engine, Extension Server, operation template, status,
      latency, bytes in and out, and the relay outcome. Bodies and secrets are
      never logged.
- [ ] The relay forwards trace context (`traceparent`) and the request id, so a
      single call can be followed end to end.

**Engine owner contract**
- [ ] A public, versioned document describes the Extension Server contract: the
      manifest format, how to verify assertions, its claims, required headers,
      error mapping, limits, and how an Extension Server can call back into the
      Avernet Open API with its own App credential.
- [ ] A reference Extension Server, used for single-box and tests, verifies
      assertions and serves at least one plain endpoint and one SSE endpoint.

## In Scope

- Relay routes on the backend's Open surface and Internal surface.
- Registering Extension Servers through operator configuration, keyed by engine
  type.
- Loading Extension Manifests, enforcing them as the allow-list, and refreshing
  them.
- Extension Assertion: signing, publishing the public key set, and rotation.
- Header, body, and status handling, SSE streaming, timeouts, SSRF guards, rate
  and concurrency limits, the circuit breaker, audit, and tracing.
- Gateway configuration so that the relay path is covered by `route_security`,
  and the public key set is reachable through the Gateway without
  authentication.
- The engine-owner contract document and a reference Extension Server for
  single-box and contract tests.

## Out of Scope

- **WebSocket** relay to Extension Servers. HTTP and SSE only in phase 1. See
  plan §11.
- Extension Servers that tenants register themselves, or per-tenant overrides.
  Phase 1 is configured by the operator only.
- Moving the existing engine-specific endpoints (`/api/openclaw/*`, `aicoding`
  `/data/*`) onto the relay. That migration needs a follow-up spec for each
  engine.
- Relaying to a Bot's **device-local** engine adapter. The existing
  `EngineRuntimeRelay` keeps that path, and the relay targets only servers run by
  engine owners.
- Copying the operations from every manifest into the Gateway's published
  OpenAPI document. This is a candidate for phase 2.
- Billing or metering Extension Server usage.

## Open Questions

1. **Registration ownership.** This spec assumes phase 1 is operator-only (one
   Extension Server per engine type per deployment). Should tenants be able to
   bring their own server for an engine, for example a private deployment of an
   engine owner's server? If so, registration needs a database, an API, and
   approval, which is a materially larger design. The phase 1 design leaves room
   for it.
2. **Scope granularity.** Is `read`/`write` per operation enough? Or do App
   grants need engine-specific scopes, such as `ext:hermes:jobs.write`? The phase
   1 assertion carries the scope as a string, so finer scopes can be added
   without changing its format.
3. **On-behalf-of user for App callers.** When an App calls on behalf of a user,
   should the assertion name both (`act` plus `sub`), as source credentials do
   (`app:<id>:on-behalf-of:<user>`)? This spec assumes yes.
4. **Limit defaults.** Confirm the defaults: 10 MiB body, 30 s / 10 min
   timeouts, 64 concurrent requests per server, 20 requests per second per
   (tenant, Bot).
