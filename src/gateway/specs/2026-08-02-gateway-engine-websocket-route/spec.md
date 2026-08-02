# Gateway `/engine` Route — Serving the Published Tenant Socket

## Summary

The backend's public connection endpoint hands a tenant a finished WebSocket
address of the shape

```text
wss://<gateway-host>/engine/<target>/api/openclaw/ws?x-proxypass-token=<token>
```

Nothing on the gateway serves it. `/engine` is outside the gateway's version
base, so the request is denied as an unknown route before anything else runs;
even if it were routed, the gateway is an HTTP forwarder with no Upgrade
handling anywhere in its code, and its route-security table requires an
authenticated user on every path.

This feature makes that address work: the gateway accepts `/engine/**`, rewrites
it onto the engine proxy's own `/proxypass/` prefix, proxies the WebSocket in
both directions for the life of the connection, and exempts the prefix from
gateway authentication because the credential the socket carries is checked by
the hop behind it.

## Motivation

**A published address that nothing serves is worse than no address.** The
connection endpoint is complete on the backend side and its contract is written
down (`src/backend/docs/openapi-v1/engine-surface.md`, the *connection* entry).
Its spec deferred this half explicitly — *Out of Scope: the gateway's own
routing* — and its plan carries it as *Risk 4 — the gateway route does not exist
yet*. The switch that makes the endpoint publish anything at all is the
backend's `user_config.gateway` block, and the agreed rollout ordering is that
the gateway route must exist **before** an overlay fills that block. This feature
is the thing that ordering waits on.

**The gateway is the component tenants are told to integrate against.** The
reason the endpoint stopped naming the engine proxy is that naming it makes the
proxy's hostname and routing prefix things we can never move. That freedom is
only real if the gateway actually terminates the socket, so the hop behind it
stays ours to change.

**The credential cannot travel any other way.** A browser's `new WebSocket(url,
protocols)` accepts no headers, so the credential rides in the query string and
is checked once, at the handshake, by the proxypass hop. The gateway must
therefore not demand a second credential of its own on this prefix, and must not
impose an idle-read deadline that would kill a healthy long-lived socket whose
one-time credential has since expired — expiry bounds *opening* a socket, not
its life.

## User Stories

- As an **external tenant**, I want the socket URL the connection endpoint gave
  me to open verbatim against the gateway host, so that I do not have to know
  what sits behind it.
- As an **external tenant**, I want the socket to stay open and carry frames in
  both directions for as long as my user is chatting, so that a long, quiet
  conversation is not dropped by infrastructure between us.
- As a **gateway operator**, I want the `/engine` prefix, the upstream it lands
  on, and its authentication posture to be visible in `application.yaml` beside
  every other routing decision, so that reading one file tells me what the
  gateway serves and what it requires.
- As a **gateway operator**, I want a deployment that fronts no engine proxy to
  answer `/engine/**` as an unknown route, so that the community build advertises
  no socket it cannot serve.
- As a **platform owner**, I want this change to leave every existing forwarded
  path byte-identical, so that adding a socket carries no risk to the HTTP plane.

## Acceptance Criteria

- [x] A WebSocket handshake to `/engine/{rest}` is proxied to the configured
      engine-proxy upstream at `/proxypass/{rest}`, with everything after the
      prefix — the routing target, the engine path, and the query string
      carrying the credential — passed through **verbatim**, byte for byte, with
      no re-encoding.
- [x] The rewrite is anchored at the root of the gateway's host. `/engine` is not
      nested under the version base, and no configuration can move it, because
      the address the backend publishes assumes the root.
- [x] Frames pass in both directions for the life of the connection. Text and
      binary frames are relayed unchanged, and the gateway applies **no idle-read
      deadline** — a socket that is quiet for hours stays open.
- [x] When either side closes, the other is closed, and the closing side's code
      and reason are carried across wherever the WebSocket protocol allows them
      to be.
- [x] The `/engine` prefix requires no gateway identity, and that exemption is
      **declared in the route-security table** rather than implied by the code
      path that serves it. A deployment can tighten it by editing configuration.
- [x] A deployment with no engine route configured, and a path that is not under
      the prefix, both refuse the handshake rather than opening a socket to
      nowhere.
- [x] An upstream that cannot be reached refuses the handshake — the gateway
      never accepts a client socket it has not already connected to the upstream.
- [x] Every existing HTTP forwarding behaviour is unchanged: the same paths
      resolve to the same servers, with the same authentication and the same
      verbatim relay.
- [x] The community build fronts no engine proxy and therefore serves no
      `/engine` route; the shipped configuration documents the block an overlay
      fills.

## In Scope

- Accepting, routing, authenticating, and proxying a WebSocket on the `/engine`
  prefix, and the configuration that names its upstream and its identity
  requirement.

## Out of Scope

- **The published address itself.** How the backend composes the URL, which
  credential it carries, and when that credential expires are settled and
  unchanged. This feature serves the address; it does not restate it.
- **HTTP under `/engine`.** The prefix publishes one thing, a socket. An HTTP
  request there stays an unknown route, as it is today.
- **Every other forwarded path.** The version-base domains, their servers, their
  identity requirements, and the HTTP forwarder are untouched.
- **Who may open the socket.** The bot-type and sharing gates that decide whether
  a socket is published at all live on the backend and are unchanged; the
  credential in the query is what the proxypass hop authorises.
- **The corp gateway host.** The backend's `user_config.gateway` values and this
  gateway's engine-proxy upstream are both overlay concerns, owned outside this
  repository.

## Resolved Questions

1. **Whether to front the gateway with Nginx instead of teaching the gateway to
   proxy WebSockets.** *Resolved 2026-08-02: the gateway proxies it.* Nginx is
   the cheaper implementation and the wrong seam. `/engine` is a routing
   decision — which upstream, which rewrite, which identity requirement — and
   every other routing decision this gateway makes is declared in
   `application.yaml`, which its own configuration file calls the only runtime
   configuration the gateway reads. Moving one route out to a component in front
   would put its upstream host in a second place, and would leave
   `route_security`'s `"/**": {user: required}` describing a host that in fact
   serves an unauthenticated prefix — with nothing in the gateway's own
   configuration saying so.

   The practical arguments point the same way. This repository ships no Nginx at
   all: no configuration file, no image, no compose service. The template offered
   (`src/baas/tests/unit/adapters/web/websocket/test_nginx_websocket.py`) is a
   test asserting on a string literal declared inside the test itself, so it
   validates no deployed artifact and would give this route no real coverage
   either. Proxying in-process is testable end to end against a live socket in
   the suites that already exist.

   The cost is one dependency, `websockets` — which is also what
   `uvicorn[standard]` installs, and is required for the gateway's own server to
   accept an Upgrade at all, so it is not avoidable by choosing a different
   client.

   *Consequence worth recording:* any L7 hop a deployment puts in front of the
   gateway must still pass the Upgrade through and must not impose a read
   timeout on this prefix. That is an operational requirement on a component
   this repository does not own, and it is now documented rather than assumed.

2. **What the prefix requires of a caller.** *Resolved 2026-08-02:* nothing, and
   said so in the route-security table as an explicit empty requirement rather
   than by omission. Omission fails closed — the top-level `"/**"` rule would
   demand a user — so the exemption has to be written down, which is the
   property we want: the table stays an honest description of the gateway's
   security posture, and tightening the prefix is a configuration edit.

3. **What a deployment without an engine proxy does.** *Resolved 2026-08-02:*
   refuses the handshake, exactly as an unknown path does. This mirrors the
   backend's neutral-empty `gateway` block: the community build fronts no
   gateway and no engine proxy, and both sides say so rather than publishing or
   serving an address nothing answers.
