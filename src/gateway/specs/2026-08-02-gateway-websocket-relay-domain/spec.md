# Gateway WebSocket Relay — Serving the Published Tenant Socket

## Summary

The backend's public connection endpoint hands a tenant a finished WebSocket
address of the shape

```text
wss://<gateway-host>/openapi/v1/engine/<target>/api/openclaw/ws?x-proxypass-token=<token>
```

Nothing on the gateway serves it. The gateway forwards HTTP only — there is no
Upgrade handling anywhere in its code — its route-security table requires an
authenticated user on every path under `/openapi/v1`, and no domain named
`engine` exists to route to.

This feature makes that address work. It does so by **generalising the domain
map rather than special-casing a prefix**: a domain may now declare which
protocols it answers and one prefix rewrite, so `engine` is an ordinary domain
beside `bots` — same lookup, same config shape, same failure mode when absent —
that happens to serve the socket plane and to publish its upstream under a
different prefix.

## Motivation

**A published address that nothing serves is worse than no address.** The
connection endpoint is complete on the backend side and its contract is written
down (`src/backend/docs/openapi-v1/engine-surface.md`, the *connection* entry).
Its spec deferred this half explicitly — *Out of Scope: the gateway's own
routing* — and its plan carries it as *Risk 4 — the gateway route does not exist
yet*. The switch that makes the endpoint publish anything at all is the
backend's `user_config.gateway` block, and the agreed rollout ordering is that
the gateway route must exist **before** an overlay fills that block.

**The gateway is the component tenants are told to integrate against.** The
reason the endpoint stopped naming the engine proxy is that naming it makes the
proxy's hostname and routing prefix things we can never move. That freedom is
only real if the gateway terminates the socket, so the hop behind it stays ours
to change — including the option, later, of removing that hop entirely and
having the gateway serve the devices itself.

**The credential cannot travel any other way.** A browser's `new WebSocket(url,
protocols)` accepts only a URL and a subprotocol, so the credential rides in the
query string and is checked once, at the handshake, by the hop behind the
gateway. The gateway must therefore not demand a second credential of its own on
this path, and must not impose an idle-read deadline that would kill a healthy
long-lived socket whose one-time credential has since expired — expiry bounds
*opening* a socket, not its life.

**Two planes sharing one routing table needs the planes named.** The gateway's
HTTP catch-all and the socket entrypoint both resolve through the domain map. If
a domain does not say which plane it belongs to, adding a socket domain silently
opens an HTTP route into its upstream as a side effect. Declaring the protocol
is what keeps one addition from meaning two.

## User Stories

- As an **external tenant**, I want the socket URL the connection endpoint gave
  me to open verbatim against the gateway host, so that I do not have to know
  what sits behind it.
- As an **external tenant**, I want the socket to stay open and carry frames in
  both directions for as long as my user is chatting, so that a long, quiet
  conversation is not dropped by infrastructure between us.
- As a **gateway operator**, I want every routed path — its upstream, its
  protocol, its authentication requirement, and any rewrite — declared in
  `application.yaml`, so that reading one file tells me what the gateway serves
  and what it requires.
- As a **gateway operator**, I want a domain that serves sockets to answer
  *only* sockets, so that adding one does not quietly expose its upstream to
  HTTP callers.
- As a **platform owner**, I want every existing forwarded path to behave
  byte-identically, so that adding a plane carries no risk to the one in
  production.

## Acceptance Criteria

- [x] A WebSocket handshake to a socket domain's path is relayed to that
      domain's upstream, with everything after the domain's prefix — the routing
      target, the upstream path, and the query string carrying the credential —
      passed through **verbatim**, byte for byte, with no re-encoding.
- [x] A domain declares which protocols it answers. Unset means `http`, so every
      domain that predates this behaves exactly as before.
- [x] **The entrypoint for a plane a domain does not declare refuses it.** An
      HTTP request to a socket-only domain is an unknown route, indistinguishable
      from a domain that is not configured at all.
- [x] Every `base_url` is held to one standard, enforced by the router: it
      carries a scheme, and startup refuses one that does not, naming the
      server. The scheme says the origin and whether the connection is TLS; the
      router re-spells it per plane, so no operator has to know which planes a
      given upstream is used for.
- [x] Forwarding is verbatim by default: only the origin changes. A domain may
      declare exactly one prefix substitution instead, and a substitution
      anchored anywhere but the domain's own prefix is refused at startup rather
      than silently never matching.
- [x] Frames pass in both directions for the life of the connection. Text and
      binary frames are relayed unchanged, and the gateway applies **no idle-read
      deadline** — a socket that is quiet for hours stays open.
- [x] When either side closes, the other is closed, and the closing side's code
      and reason are carried across wherever the WebSocket protocol allows.
- [x] A socket domain requires no gateway identity, and that exemption is
      **declared in the route-security table** rather than implied by the code
      path serving it. A deployment can tighten it by editing configuration.
- [x] An upstream that cannot be reached refuses the handshake — the gateway
      never accepts a client socket it has not already connected to the upstream.
- [x] No delivery adapter names a domain. The socket entrypoint is mounted once
      per socket domain from configuration.
- [x] Every existing HTTP forwarding behaviour is unchanged: the same paths
      resolve to the same servers, with the same authentication and the same
      verbatim relay.

## In Scope

- Declaring a domain's protocols and its optional prefix rewrite; the socket
  entrypoint that serves socket domains; the configuration that names the engine
  proxy upstream and its identity requirement; and moving the backend's
  published prefix into the API namespace so `engine` can be an ordinary domain.

## Out of Scope

- **Which credential the socket carries, and when it expires.** Settled on the
  backend and unchanged. This feature serves the address; it does not restate it.
- **Verifying that credential at the gateway.** The hop behind the gateway
  authenticates it and will continue to; a presence check here would be belt and
  braces, and is deliberately deferred rather than half-built.
- **Who may open the socket.** The bot-type and sharing gates live on the
  backend and are unchanged.
- **Concurrency limits and socket observability.** The gateway now holds
  long-lived connections, which is new, but a limit picked without measurement
  turns a capacity problem into an outage. Tracked separately: measure first,
  then bound.
- **The corp engine-proxy host.** An overlay concern, like every other upstream.

## Resolved Questions

1. **Whether to front the gateway with Nginx instead of teaching the gateway to
   proxy WebSockets.** *Resolved 2026-08-02: the gateway proxies it.* Nginx is
   the cheaper implementation and the wrong seam. Which upstream, which rewrite,
   which identity requirement — these are routing decisions, and every other
   routing decision this gateway makes is declared in `application.yaml`, which
   its own configuration file calls the only runtime configuration the gateway
   reads. Moving one route out to a component in front would put its upstream
   host in a second place, and would leave `route_security` describing a host
   that in fact serves an unauthenticated prefix, with nothing in the gateway's
   own configuration saying so.

   This repository also ships no Nginx at all: no configuration file, no image,
   no compose service. The template offered
   (`src/baas/tests/unit/adapters/web/websocket/test_nginx_websocket.py`) asserts
   on a string literal declared inside the test itself, so it validates no
   deployed artifact and would give this route no real coverage. Proxying
   in-process is testable end to end against a live socket.

   The cost is one dependency, `websockets` — which is also what
   `uvicorn[standard]` installs, and is required for the gateway's own server to
   accept an Upgrade at all, so it is not avoidable by choosing a different
   client.

   *Consequence worth recording:* any L7 hop a deployment puts in front of the
   gateway must still pass the Upgrade through and must not impose a read
   timeout on these paths. That is an operational requirement on a component
   this repository does not own, and it is documented rather than assumed.

2. **Whether the socket sits at the host root or inside `/openapi/v1`.**
   *Resolved 2026-08-02: inside.* The first implementation anchored it at the
   root, which forced a parallel routing concept — its own config block, its own
   resolver, its own "is it configured" state — because the domain map resolves
   the segment *after* the version base. Inside the namespace, `engine` is just
   another domain: same lookup, same config shape, same absent-means-404. It is
   also where a tenant would expect it, since the endpoint that hands out the
   address lives at `/openapi/v1/bots/{bot_id}/connection`.

   This required moving the backend's published prefix, which supersedes an
   approved acceptance criterion in
   `src/backend/specs/2026-07-31-engine-connection-gateway-url/spec.md`; the
   superseding decision is recorded there as Resolved Question 4. **Free to do
   now, not later:** the endpoint is absent from the published
   `bots.openapi.json` and has no integrators, so the address moves at no cost.

3. **What the socket prefix requires of a caller.** *Resolved 2026-08-02:*
   nothing, and said so in the route-security table as an explicit empty
   requirement rather than by omission. Omission fails closed — the top-level
   `"/**"` rule would demand a user — so the exemption has to be written down,
   which is the property we want: the table stays an honest description of the
   gateway's security posture, and tightening it is a configuration edit.

   Recorded because of what it changes: this is the **first rule under
   `/openapi/v1` that requires no identity**. The namespace was uniformly
   authenticated before.

4. **What stops a socket domain becoming an HTTP proxy.** *Resolved 2026-08-02:*
   the protocol declaration, enforced at the entrypoint. Both planes resolve
   through the same domain map, so without it, registering a socket domain would
   also register an HTTP route that forwards a caller-controlled path into the
   upstream — under an auth-exempt prefix, and built from the *decoded* path, so
   `%2e%2e` segments would collapse to `..` in the upstream URL. Naming the plane
   is what makes one addition mean one thing.

5. **Where the outbound socket transport lives.** *Resolved 2026-08-02:*
   `adapters/web/`, not `plugins/`. `plugins/` means an edition-swappable
   implementation of a plugin contract, and every other entry there earns that.
   There is no corp variant of "dial a WebSocket", so filing it under `plugins/`
   would advertise a split that does not exist and carry a config selector with
   one legal value. Rule 7 bans transport frameworks from *core*, which makes
   the layer whose job is speaking protocols their home.

   *Not settled here:* the HTTP `Forwarder` sits in `plugins/forwarder/httpx`,
   and whether the same reasoning applies depends on whether the enterprise
   flavour its SPI docstring anticipates is real. Owned by that code's authors.

6. **Whether a socket upstream needs a different `base_url` standard.**
   *Resolved 2026-08-02: no — one standard for every server, enforced by the
   router.* The first implementation required a scheme on the socket upstream
   and left the two HTTP samples bare, then explained the difference in a
   comment. That put the rule in human knowledge, and it was documenting a bug:
   `backend.sample.com` + `/openapi/v1/bots` is a **relative** URL with an empty
   host, so those samples never worked — nothing forwarded to them in tests.

   `Server` now validates its own `base_url` in `__post_init__`, so every
   upstream is held to the rule however it was constructed, and a bad value
   fails the boot with the server named rather than the first call. `http`,
   `https`, `ws` and `wss` are all accepted, because what the scheme actually
   encodes is the origin plus TLS-or-not; `http_base_url` and
   `websocket_base_url` spell that for whichever plane is asking. Which planes
   an upstream serves is the domain's declaration, not the scheme's.
