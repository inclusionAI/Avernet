# Connection Endpoint — Gateway URL and Query-Parameter Credential

## Summary

The public connection endpoint hands a tenant a ready-to-use chat socket: a
finished WebSocket URL plus the credential needed to open it. Both halves are
currently wrong for the caller they are built for. The URL names our internal
engine proxy directly and spells its routing prefix into the path, and the
credential is returned in a response header — a form the intended consumer
cannot use, because a browser has no way to set a header on a WebSocket
handshake.

This feature republishes the same socket behind the gateway under an `engine`
path prefix, and moves the credential into the URL as a query parameter. The
socket itself, the bot gating, the credential's issuer, and its lifetime are all
unchanged.

## Motivation

**The response is unusable from a browser.** The tenant flow is: the tenant's
server calls this endpoint, hands the result to its own frontend, and that
frontend opens the socket. The consumer is therefore a browser, and a browser's
WebSocket API accepts only a URL and a subprotocol — there is no parameter for
request headers, by deliberate design of the specification. The only credential
a browser can carry on a handshake it did not compose is a cookie, and our
gateway issues no cookie to these callers: they authenticated with the tenant,
not with us. So the header we publish today can never be presented, and the
socket cannot be opened from the place it is meant to be opened from.

**The mechanism is already proven.** The internal console opens this exact
socket, against this exact upstream, with the credential as a URL query
parameter. The upstream accepts it in that position today; nothing new has to
learn a new format. We are adopting a shape already running in production, not
inventing one.

**The URL names infrastructure we do not want to publish.** The address handed
to a tenant points at the internal engine proxy by name and carries its routing
prefix as a path segment. Once an integrator writes code against that, both
become things we cannot change. Publishing the gateway instead — which is the
component tenants are supposed to talk to — restores our freedom to move what
sits behind it, and is the reason this endpoint exists in the first place.

**The credential's lifetime is described in a way that invites the wrong
behaviour.** The published wording reads as though the socket stops working when
the credential expires. It does not: the credential is checked once, at the
handshake. A caller who believes otherwise will either build a pointless refresh
loop or, worse, tear down healthy connections on a timer.

## User Stories

- As an external tenant, I want the socket URL I am given to be openable
  directly from my users' browsers, so that I do not have to proxy a WebSocket
  through my own server just to attach a credential.
- As an external tenant, I want the address I am given to name the gateway I was
  told to integrate against, so that my integration does not depend on which
  internal component currently serves the socket.
- As an external tenant, I want to know whether the expiry I am given bounds
  opening the connection or the life of the connection, so that I refresh at the
  right moments and not on a timer.
- As an external tenant, I want the credential to appear in exactly one place in
  the response, so that there is no ambiguity about which one the socket
  actually accepts.
- As the team operating the platform, I want this change to leave the internal
  console's own socket untouched, so that moving the public surface onto the
  gateway carries no risk to a working production path.

## Acceptance Criteria

- [ ] The socket URL published by the connection endpoint addresses the gateway,
      not the internal engine proxy.
- [ ] The published URL carries an `engine` path prefix directly after the host,
      and does not contain the internal proxy's routing prefix anywhere.
- [ ] The credential is carried as a query parameter on the published URL, under
      the same parameter name the upstream already accepts.
- [ ] The response no longer carries a headers field for a socket. The field is
      removed from the contract rather than published empty, so that there is
      exactly one place a caller can find the credential.
- [ ] The URL is complete and opaque: a caller opens it verbatim, appending
      nothing and rebuilding nothing.
- [ ] A socket is published only where one is published today. The bot-type and
      sharing gates, the choice of engine path, and the set of socket kinds are
      unchanged by this feature.
- [ ] Where no credential is available, the endpoint's behaviour is unchanged
      from today rather than publishing a URL carrying an empty credential.
- [ ] The published expiry is documented as bounding the *opening* of a socket
      only, stating explicitly that an already-open socket survives expiry, that
      a caller should fetch a fresh credential before connecting or
      reconnecting, and that a caller should not poll on a timer to keep a live
      socket alive.
- [ ] A deployment that has not been told where the gateway is fails with a
      named, diagnosable error rather than publishing an unusable address.
- [ ] The internal console's socket is byte-for-byte unaffected: no component
      shared with it changes behaviour.

## In Scope

- The URL and credential published by the public connection endpoint, and the
  contract describing them.
- The wording of the published expiry semantics, wherever that contract is
  stated.
- Configuring where the gateway lives, for deployments that serve this endpoint.

## Out of Scope

- **The gateway's own routing.** Rewriting the `engine` prefix onto the
  upstream, and exempting that prefix from the gateway's own authentication, are
  owned by a separate workstream. This feature only publishes an address that
  assumes those exist.
- **The internal console and every other caller of the engine proxy.** The
  console's chat socket, the group-chat relay, the HTTP invoke path, and the
  frontend all continue to address the engine proxy exactly as they do now. This
  feature deliberately does not consolidate them; doing so would move a working
  production path with no benefit to the tenant-facing contract.
- **The credential itself.** Its issuer, signature, payload, and lifetime are
  unchanged. This feature moves where it is published, not what it is.
- **HTTP requests.** Credentials on HTTP requests continue to travel as a
  header, and gain no query-parameter form. The split is deliberate: each
  transport carries the credential the way that transport natively supports, and
  neither gains a second mechanism.
- **Waking or repairing an unreachable device.** Unchanged.

## Open Questions

1. **Where the gateway's address comes from.** The backend has no existing
   configuration path for the gateway — the only proxy address it can resolve
   today is the engine proxy's, which is precisely what we are moving off. A new
   source is needed. The codebase already has a precedent for exactly this shape
   of setting: a deployment-supplied entry-point URL, read from the environment,
   with an explicit failure when a deployment has not set it.
   **Recommendation: follow that precedent** — it keeps the failure mode
   diagnosable and matches how the neighbouring service resolves the same class
   of value. Confirm the setting's name and whether a local-development default
   is wanted.

2. **Whether the credential in a URL is acceptable in this deployment.** A query
   parameter is visible to anything that logs request lines, including the
   gateway's own access log. The exposure is bounded — the credential is short-
   lived and bound to a single target — and the internal console already accepts
   this trade-off for the same credential against the same upstream. Flagged so
   the decision is recorded rather than inherited by accident.
   **Recommendation: accept**, matching the console.
