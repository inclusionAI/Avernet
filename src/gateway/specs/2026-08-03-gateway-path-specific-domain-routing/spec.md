# Path-specific gateway domain routing, and the bot socket under `bots`

## Summary

The gateway today picks an upstream by the single path segment after the version
base, so every public prefix is one word and every word belongs to exactly one
upstream. This adds path-specific routing — a domain claims a *path pattern* and
the protocol it answers — and uses it to move the bot's chat WebSocket from
`/openapi/v1/engine/**` to `/openapi/v1/bots/messages/**`. After the change the
whole bots scope, management and runtime alike, lives under one public prefix.

## Motivation

`GET /openapi/v1/bots/connection/{bot_id}` hands a tenant a finished socket URL,
and that URL points at a *sibling* top-level prefix:

```text
today   wss://<gw>/openapi/v1/engine/<target>/api/openclaw/ws?x-proxypass-token=…
after   wss://<gw>/openapi/v1/bots/messages/<target>/api/openclaw/ws?x-proxypass-token=…
```

Two things are wrong with the status quo.

**Scope coherence.** The BCN collaboration-prefix design
(`src/bcs/docs/plans/2026-08-03-bcn-collaboration-prefix-design.md`, approved
2026-08-03) settled that the leading segment names the *owner* of a surface, not
the process serving it. Backend management and the engine-proxy runtime are one
team and one scope, so they belong under one prefix; `engine` sitting as a
sibling of `bots` splits one owner's surface across two namespaces — the exact
problem that design was written to stop.

**Naming the hop.** The connection service's own stated goal is that "nothing
names the hop behind the gateway." A top-level `engine` domain is close to
naming one, and it is not even a separate owner.

Neither is reachable by a config edit: a domain *is* its leading segment today,
so `bots` cannot be both a backend prefix and the parent of a socket prefix
served by a different upstream.

`messages` is the socket prefix (rather than, say, `socket` or `ws`) because it
names the channel the messages actually travel over, and because other domains
are expected to grow their own `messages` endpoints. One vocabulary across
domains is the point.

## User Stories

- As a tenant integrating with the public API, I want every address for my bot —
  management and chat socket alike — under one prefix, so that I learn one scope
  rather than discovering a second top-level namespace from a response body.
- As a gateway operator, I want a domain's public path decoupled from the
  upstream that serves it, so that the address space reflects ownership rather
  than process topology.
- As the owner of a future `/openapi/v1/bots/messages/{bot_id}` HTTP endpoint, I
  want reserving the socket prefix today not to make that address unreachable
  tomorrow.
- As a reviewer, I want it to remain impossible to configure the gateway as an
  open proxy, even though prefixes are now patterns rather than single words.

## Acceptance Criteria

- [ ] A WebSocket handshake to `/openapi/v1/bots/messages/<target><engine-path>`
      is relayed to the engine proxy, with everything past the prefix — routing
      target, engine path, credential in the query — travelling byte for byte,
      exactly as it does at the old address today.
- [ ] A handshake to `/openapi/v1/engine/**` no longer resolves.
- [ ] An HTTP request under `/openapi/v1/bots/**`, **including**
      `/openapi/v1/bots/messages/...`, still reaches the backend. Reserving the
      socket prefix must not make the future HTTP address unreachable.
- [ ] An HTTP request to `/openapi/v1/bots/messages/...` is not served by the
      engine proxy, and a WebSocket handshake elsewhere under
      `/openapi/v1/bots/**` is not relayed.
- [ ] A domain declares which **plane** it answers — request/response or relayed
      socket — and that declaration participates in *selecting* the domain, not
      only in checking one already selected. A path claimed for one plane must
      leave the other plane's resolution of that same path untouched.
- [ ] The socket prefix requires no caller identity; every other path under
      `/openapi/v1/bots/**` still requires an authenticated user. The exemption
      is written down explicitly rather than inferred.
- [ ] `GET /openapi/v1/bots/connection/{bot_id}` publishes socket URLs at the new
      address. Its own path, name, and response shape are unchanged.
- [ ] The existing evasion guards still hold at the longer prefix: a handshake
      whose raw path percent-encodes any part of the routing prefix is refused,
      and a path carrying a `.`/`..` segment in any spelling is refused.
- [ ] A configuration that would make the gateway an open or over-broad proxy is
      refused at startup, naming the offending domain — not resolved at request
      time and not silently accepted.
- [ ] A configuration whose declared prefix substitution could never fire is
      refused at startup, as it is today.
- [ ] Two domains that would answer the same path on the same protocol are
      refused at startup rather than resolved by an undefined order.
- [ ] Every domain that does not opt into a path pattern keeps its current
      address and behaviour byte for byte.
- [ ] `messages` is recorded as a reserved component name under
      `/openapi/v1/bots` in the published English and Chinese API docs, and the
      record cannot silently fall out of step with the routes.
- [ ] The published English and Chinese engine-surface docs quote the new socket
      address.

## In Scope

- Gateway domain resolution keyed on (path pattern, protocol) instead of the
  leading segment alone, with startup validation for over-broad patterns and for
  two domains colliding on one path and protocol.

  "Protocol" here means the **plane** — request/response, or relayed socket — and
  not the URL scheme. `ws` and `wss` are one plane, as `http` and `https` are;
  the scheme says only whether the connection is TLS, and one configured upstream
  is already addressable on either plane. A domain that answered `wss` but not
  `ws` would be describing its transport security, which is not a routing
  question.
- Moving the shipped socket domain, its route-security exemption, and the address
  the backend publishes, to `/openapi/v1/bots/messages`.
- Reserving `messages` as a component name under `/openapi/v1/bots`, and the
  documentation and tests that keep that reservation honest.
- English and Chinese documentation updates for both the reserved-name list and
  the engine-surface socket address.

## Out of Scope

- **Hiding the engine.** `/api/openclaw/ws` stays in the path,
  `x-proxypass-token` stays in the query, and `engine` remains a documented field
  on the connection response. Secrecy is not the goal; scope coherence is.
  Making any of them opaque would break the verbatim prefix-substitution
  property the forwarding design rests on.
- **Renaming `GET /openapi/v1/bots/connection/{bot_id}`.** It hands out a
  connection; it is not a message resource.
- **A compatibility alias at `/openapi/v1/engine/**`.** That surface has no
  reachable external caller — its route-security rule requires a Google-resolved
  user identity, which a tenant presenting an access key cannot satisfy — so
  there is no contract to preserve. Same reasoning as PR #706.
- **Implicit protocol-based fallback.** Resolution must not "try the most
  specific pattern, then silently try the next one when the protocol does not
  match." Two deliberate per-protocol declarations at one pattern are safe; a
  silent retry is a smuggling hole and is not built.

  The distinction is not academic, because both reach the backend for today's
  HTTP `messages` request. The plane belongs in the *candidate set*: a domain
  that does not answer this plane is never a candidate, so the most specific
  remaining match wins outright. Under a retry, a request that was ranked into
  one domain and refused there gets a second attempt at another — which is a
  request being served by a domain that did not win.
- **Moving any other domain under `bots`.** `sessions`, `messages` and `runs`
  belong to BaaS and `collaboration` to BCS; those are different owners and the
  ownership principle keeps them where they are.
- **Adding the future `/openapi/v1/bots/messages/{bot_id}` HTTP endpoint.** This
  change only has to leave the address reachable.

## Open Questions

*(none blocking — the three objections raised in review were answered before this
was written: ownership rather than process decides the prefix; the auth-exemption
precedence already ranks the longer literal prefix higher; and hiding the engine
was never the goal.)*

One coordination item, informational rather than blocking: the owner of
`src/bcs/docs/plans/2026-08-03-bcn-collaboration-prefix-design.md` should be told
that longest-prefix routing has moved from "out of scope" to "in scope". BCN does
not need the mechanism — it owns `/openapi/v1/collaboration/**` outright — and the
ownership framing above leaves its one-prefix-per-owner principle intact, but they
wrote that constraint down the same week and should hear it from us.
