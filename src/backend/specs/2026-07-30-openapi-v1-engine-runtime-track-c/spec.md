# Public API — Engine Runtime Surface (Track C)

## Summary

The public `/openapi/v1` API today covers only what lives in the backend's own
records: a tenant can create and manage a bot, but cannot talk to it. A bot's
runtime — its conversation sessions, the approval mode governing what it may do
unattended, the models it can reach, what the bot is currently capable of — runs
on the bot's own device and has no public representation at all. This feature
adds seventeen bot-scoped endpoints that expose that runtime through the public
API, plus one endpoint that hands back a ready-to-use chat connection so a
tenant can hold a live conversation with its bot.

## Motivation

The internal console reaches a bot's runtime by first asking the backend for a
device connection and then calling the bot's device directly, composing the
address itself from a returned target and token. That arrangement is acceptable
between our own frontend and our own backend. Handing the same thing to an
external tenant is not:

- It publishes our internal routing topology and a raw device credential to a
  third party, and once published, both become things we cannot change without
  breaking integrators.
- It makes the device's own interface — which was never designed or reviewed as
  a public contract, and which differs endpoint by endpoint depending on which
  engine the bot runs — the thing an integrator writes code against.
- It leaves the public API unable to answer the most basic question a tenant
  has after creating a bot: *how do I actually use it?*

The gate that held this back is already cleared. Bots are tenant-isolated, so a
runtime call routed through a bot the caller owns inherits that isolation with
no new data work. The same relay shape — public request, resolve the caller's
bot, forward to that bot's device, return the answer in the public envelope —
already runs in production for scheduled tasks. This feature generalises a
proven path rather than inventing one.

## User Stories

- As an external tenant, I want to list, create, read, update and delete my
  bot's conversation sessions, and read or clear a session's message history, so
  that I can manage conversations programmatically.
- As an external tenant, I want a single call that returns a ready-to-use chat
  connection for my bot, so that I can open a live conversation without knowing
  anything about how my bot is hosted.
- As an external tenant, I want to read and set my bot's approval mode for a
  session, so that I control what the bot may do without asking me first.
- As an external tenant, I want to see which models my bot can reach, so that I
  can choose one when starting a conversation.
- As an external tenant, I want to ask what my bot is currently capable of
  before I call anything, so that I can adapt my integration instead of
  discovering unsupported operations as failures at runtime.
- As an external tenant, I want a runtime call against a bot that is not mine to
  be indistinguishable from a call against a bot that does not exist, so that
  the API cannot be used to probe for other tenants' bots.
- As an external tenant, I want a clear, distinct answer when my bot's device is
  not currently reachable, so that I can retry rather than treat it as a
  permanent failure or a bug in my request.

## Acceptance Criteria

- [ ] Seventeen endpoints are served, all scoped to a single bot the caller
      owns: seven for sessions, three read-only for engine state, two for
      models, three for approvals, one for nodes, and one for connection.
- [ ] Every response uses the public API's standard envelope, with list
      endpoints returning the standard page shape and an accurate total.
- [ ] Every endpoint resolves the caller's identity from the request principal
      and serves only bots belonging to that caller and tenant. A runtime call
      naming a bot owned by someone else, or belonging to another tenant, is
      answered identically to one naming a bot that does not exist.
- [ ] No endpoint accepts a caller-supplied user identity, device identifier,
      binding identifier, or engine override. Where the underlying runtime
      expects a user identity, it is filled from the authenticated caller.
- [ ] The connection endpoint returns, for each socket the bot's engine actually
      serves, a complete connection address and the headers required to use it,
      together with an expiry the caller can act on. It never returns a routing
      target, a connection type, or a bare credential as a separate field, and
      the caller never has to assemble an address itself.
- [ ] The connection endpoint lists exactly the sockets the bot's current engine
      supports — no more, no fewer — and never contradicts what the capabilities
      endpoint reports.
- [ ] An operation the bot's engine does not support is answered as an explicit,
      documented "not supported by this bot" outcome that names the capabilities
      endpoint, not as a generic server error.
- [ ] An operation the bot's engine supports only partially still returns its
      result, and the caller is told the result may be incomplete.
- [ ] A bot whose device is unreachable — cold, dormant, or restarting — is
      answered with a single, consistent, retryable outcome across all seventeen
      endpoints, distinct from both "not found" and "server error".
- [ ] Error responses never expose internal identifiers, internal-language text,
      device addresses, or credentials.
- [ ] The internal API surface and the internal test suite are unchanged.
- [ ] The feature introduces no new stored records and no schema change.

## In Scope

- Sessions: list, create, read, delete, read message history, clear message
  history, and partial update.
- Engine state, read-only: runtime status, declared capabilities, and the list
  of engines available on the bot with the active one marked.
- Models: list, and read one by identifier.
- Approvals: read the mode for a session, set the mode for a session, and list
  the modes that exist.
- Nodes: list.
- Connection: one endpoint returning usable socket connections for the bot.
- A single, shared treatment of capability limits, device unreachability, and
  the mapping from the device's response shape to the public envelope, so all
  six groups behave identically.

## Out of Scope

- **Changing or restarting a bot's engine.** The public API fixes a bot's engine
  at creation and refuses to change it on update; a runtime endpoint that
  switched it would contradict that. Restarting is already offered at the bot
  level, and a second restart with a different blast radius would be a trap.
- **Relaying chat traffic through the public API.** No request/response chat
  endpoint and no streaming relay. The public API hands back a connection; the
  caller holds the conversation. This keeps the conversation protocol out of the
  public contract.
- **Anything the public API already fronts through the backend.** Scheduled
  tasks are already the routines category; skills, MCP configuration, resource
  materialisation, workspace files and bot configuration all already have, or
  are planned to have, a backend-owned public contract. Exposing the device's
  own versions of these would create a second, divergent path to the same
  behaviour.
- **Arbitrary command execution and interactive shell on a tenant's device.**
  Not a v1 public capability at any scope.
- **Session favourites and the engine-specific gateway diagnostics** (connection
  test, disconnect, gateway config, default config, zero-check). Deferred, not
  cancelled — each is additive and breaks no published contract if added later.
- **Product-specific runtime surfaces** that are not part of the tenant
  contract.
- **Making the public surface callable.** Like every other public category, this
  one answers unauthenticated until the separate caller-authentication
  workstream lands.

## Open Questions

1. **Partial-support signal.** When a bot's engine supports an operation only
   with a caveat, the device returns the result plus a human-readable warning.
   The public envelope has nowhere to put it today. Adding an optional warning
   field changes a contract shared with every other public category and needs
   the bots owner's agreement. Alternatives: return it as a response header, or
   drop the signal. **Recommendation: add the optional field** — dropping it
   silently degrades correctness for the caller.
2. **Unreachable-device behaviour.** Should an unreachable device be reported
   immediately as a retryable "not ready", or should the API attempt to wake the
   device and then retry before answering? This must be one choice for all
   seventeen endpoints, and it determines whether these endpoints can block for
   seconds.
3. **Connection lifetime.** What expiry should a returned chat connection carry,
   and may a caller request a shorter or longer one? A short expiry forces
   frequent re-fetches; a long one widens the window on a leaked credential.
4. **Ownership.** Track C has no assigned owner. Its groups are bots-adjacent
   and share a single shared foundation, which argues for one owner rather than
   a split.
5. **Non-owner access.** Bots support collaborators internally. Does a runtime
   call require the caller to own the bot, or is collaborator access enough?
   This spec assumes owner-only; anything broader needs stating before planning.
