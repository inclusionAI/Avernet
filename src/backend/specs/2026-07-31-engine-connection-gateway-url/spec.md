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

- [x] The socket URL published by the connection endpoint addresses the gateway,
      not the internal engine proxy.
- [x] The published URL carries an `engine` path prefix directly after the host,
      and does not contain the internal proxy's routing prefix anywhere.
- [x] The credential is carried as a query parameter on the published URL, under
      the same parameter name the upstream already accepts.
- [x] The response no longer carries a headers field for a socket. The field is
      removed from the contract rather than published empty, so that there is
      exactly one place a caller can find the credential.
- [x] The URL is complete and opaque: a caller opens it verbatim, appending
      nothing and rebuilding nothing.
- [x] A socket is published only where one is published today. The bot-type and
      sharing gates, the choice of engine path, and the set of socket kinds are
      unchanged by this feature.
- [x] Where no credential is available, the endpoint's behaviour is unchanged
      from today rather than publishing a URL carrying an empty credential.
- [x] The published expiry is documented as bounding the *opening* of a socket
      only, stating explicitly that an already-open socket survives expiry, that
      a caller should fetch a fresh credential before connecting or
      reconnecting, and that a caller should not poll on a timer to keep a live
      socket alive.
- [x] A deployment that has not been told where the gateway is fails with a
      named, diagnosable error rather than publishing an unusable address.
- [x] The internal console's socket is byte-for-byte unaffected: no component
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

## Resolved Questions

1. **Where the gateway's address comes from.** *Resolved 2026-07-31:* it is host
   configuration, so it lives where every other upstream host in this service
   already does — a `gateway` block in `application.yaml`, with separate pre and
   prod values selected by the running environment. The community build ships
   neutral empty values, which reproduce today's behaviour exactly: a deployment
   that fronts no gateway says so by name rather than publishing an address
   nothing serves. Corp values land in a separate overlay, owned outside this
   repository. (This supersedes the original recommendation of an
   environment-variable setting; the env-var precedent exists in this codebase
   but is the outlier, not the convention.)

   *Consequence worth recording:* a developer running the community profile
   locally now gets the named "no gateway" error from this endpoint until they
   set a value by hand. That is correct — community genuinely fronts no gateway —
   but it is a change from the env-var draft, which carried a localhost default.

2. **Whether the credential in a URL is acceptable in this deployment.**
   *Resolved 2026-07-31: accepted.* The exposure is bounded — the credential is
   short-lived and bound to a single target — and the internal console already
   makes the same trade for the same credential against the same upstream.
   Recorded here so it is a decision rather than something inherited by accident.

3. **Whether a provider can hand back a relay URL this endpoint cannot
   re-address.** *Resolved 2026-07-31:* no — a tenant-facing personal bot cannot
   be served by the platform that answers with a session-keyed relay URL instead
   of the routing-target shape. The URL is therefore composed from the target and
   engine path directly. A guard refuses any other shape rather than trusting the
   assumption silently, so if it ever stops holding it surfaces as a named
   server-side error instead of a socket that will not open.
