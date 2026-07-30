# Public API — MCP Category (Track B)

## Summary

The public API's `mcp` category is six route definitions whose handlers raise
`NotImplementedError`. This wires them to the MCP services that already back the
internal surface, so an external tenant can browse the MCP marketplace, see
whether it is permitted to use a server, and read and write its own per-server
configuration — with that configuration pushed to its agents' devices.

This is the second of seven Track B categories, and the first one that stores
caller-supplied secrets. Its data was isolated by Track A Stage 5 (PR #564,
merged); the handoff board marks `mcp` P1 and unblocked.

## Motivation

MCP servers are how an agent reaches third-party tools. An external tenant that
cannot configure them can create a bot but cannot make it useful — so this
category is on the critical path to the public API being worth calling at all.

Three things make it the right slice to take now:

- **Its dependency is satisfied.** Track A Stage 5 isolated
  `ac_user_mcp_config` and `ac_bot_mcp_call_config` and replaced the unique key
  so two tenants can hold a configuration for the same user identifier. Nothing
  else gates it.
- **It is the highest-consequence category to get wrong.** The configuration
  holds API keys and authorization headers for third-party servers. A read that
  escapes its tenant does not leak a bot name — it leaks a credential.
- **It is the second use of the Track B primitives.** The bots slice built the
  envelope, the error mapping, the principal seam and the app-level backstop
  once, and asserted the other six categories would reuse them. Until a second
  category does, that is a claim rather than a fact.

## User Stories

- As an external tenant, I want to browse the MCP servers available to me and
  read one server's detail including its tools, so that I can decide which to
  configure for my agents.
- As an external tenant, I want to know whether I am permitted to call a server
  before I configure it, so that I do not spend effort on a server I will be
  refused at call time.
- As an external tenant, I want to store my own API key and headers for a
  server and have them reach my agents' devices, so that my agents can actually
  call that server.
- As an external tenant, I want my stored credentials confined to my own tenant
  and my own caller identity, so that no other tenant can read or overwrite them
  and I cannot reach theirs.
- As an external tenant, I want a credential I have stored to come back masked
  rather than in full, so that reading my configuration back is not a way to
  exfiltrate the secret.
- As a caller of the existing internal MCP API, I want every response to be
  exactly what it is today, so that this addition is invisible to me.
- As an integrator, I want failures on this category to arrive in the same
  envelope, with the same error shape, as every other public endpoint.

## Acceptance Criteria

### Marketplace

- [ ] Listing MCP servers returns a page of servers for the caller's page and
      page size, with a total, and accepts an optional keyword that filters on
      name and server code.
- [ ] Only servers on the network types the internal surface already permits are
      visible. A server outside that set is absent from the listing, and asking
      for its detail answers not-found — the same not-found a genuinely unknown
      server code answers, so the restriction is not a way to learn a server
      exists.
- [ ] A server's detail includes its tools. Internal-only plumbing carried on a
      tool's input schema is removed before the tool reaches an external caller,
      exactly as the internal surface removes it.
- [ ] Listing MCP tenants returns the available tenants with their codes, names
      and categories.
- [ ] An upstream marketplace failure is reported as an upstream failure, not as
      an empty result and not as a caller error.

### Permission

- [ ] Asking about a server reports whether the caller may use it, at what
      access level, and the caller's per-tool permissions.
- [ ] The permission asked about is always the *caller's own*. There is no way
      for a caller to ask about another identity's permission. (The internal
      endpoint takes the user identifier as a query parameter; the public one
      must not.)
- [ ] A server the deployment serves locally is reported as permitted without
      consulting the external authorization service.

### Configuration — read

- [ ] Reading the caller's configuration for a server returns the endpoint
      environment, transport protocol, headers, and whether a configuration
      exists at all.
- [ ] A stored API key is returned masked, never in full — for any key length,
      including short ones.
- [ ] Reading a server the caller has never configured succeeds and reports that
      no configuration exists, rather than answering not-found.

### Configuration — write

- [ ] Writing a configuration stores it and pushes it to every device under the
      caller's identity, then returns the resulting configuration with the API
      key masked.
- [ ] A field the caller omits is left unchanged; the write is a merge, not a
      replacement.
- [ ] The endpoint environment and transport protocol are validated against the
      values the system accepts, and an invalid one is a caller error naming
      which field was wrong — not a stored bad value.
- [ ] Headers are validated before anything is stored.
- [ ] Writing a configuration for a server that does not exist is a not-found,
      and nothing is stored.
- [ ] If the push to devices fails, the stored configuration is rolled back to
      what it was before the call and the call fails. A caller never ends up
      with a configuration that is saved but not in effect. *(Decision 2 below.)*
- [ ] An unknown or unsupported field in the request body is rejected rather
      than silently ignored.

### Isolation and caller identity

- [ ] Every read and write is scoped to the caller's own identity as resolved
      from the principal, not to any identifier the caller supplies.
- [ ] Against the real Track A guard, a configuration belonging to another
      tenant is invisible: reading it reports no configuration, and writing
      creates the caller's own row rather than overwriting the other tenant's.
- [ ] Two tenants each holding a configuration for the same user identifier and
      the same server neither see nor displace each other.

### Error contract

- [ ] Every failure on this category answers in the standard envelope, with a
      fixed public message that carries no internal identifier and no
      internal-language text.
- [ ] Every domain error these handlers can raise is mapped. Nothing this
      category raises reaches the generic internal-error fallback.

### The internal surface

- [ ] The internal MCP API's request and response shapes are unchanged, and its
      existing tests pass unmodified.

## Decisions taken

1. **Paths stay nested: `/openapi/v1/bots/mcp/...`.** The handoff README flagged
   an unresolved divergence between the routers (nested) and PR #363's overview
   (top-level). Resolved in favour of the router, which the README already names
   as authoritative. The other five stub groups inherit the precedent. *(Owner
   decision, 2026-07-30.)*
2. **A failed device push rolls the write back and fails the call.** The
   internal surface already does this; matching it keeps one write path across
   both surfaces and lets the shared logic be extracted without changing
   behavior. Reporting partial success instead — issue #560's leaning — would
   pre-empt a ruling that has not been made, and would need per-bot sync results
   in the public response shape. *(Owner decision, 2026-07-30.)*
3. **The write model's `sync_mode` field is dropped.** The stub advertised
   `single | broadcast`, but no single-device push path exists — the only
   service operation pushes to every device under the identity. Following the
   bots slice's ruling on `engine_options`, the surface does not advertise what
   the server ignores; with unknown fields rejected, sending it is a validation
   error rather than a silent no-op.

## In Scope

- The six `mcp` endpoint handlers, wired to the existing MCP market, auth,
  config and sync services.
- The public request/response models for the category, including masking.
- This category's domain errors added to the shared error mapping.
- Any behavior shared with the internal surface extracted so both call one
  implementation, rather than copied.
- Tests: per-handler success and mapped-failure coverage, masking, merge
  semantics, rollback-on-push-failure, and cross-tenant isolation proven against
  the real Track A guard.
- Handoff board and changelog updated in the same change.

## Out of Scope

- Applying for permission on a server. The internal surface has an apply
  endpoint; the public category's six routes do not include one, and adding a
  seventh is a contract change to ratify separately.
- Any Track A work. MCP data was isolated by Stage 5; nothing here adds a
  column, a guard or a DDL step.
- The real caller-identity verifier. The principal stays a stub, so — like every
  other public route — these answer 401 to a real request until the auth
  workstream lands. Handlers, contracts and tests are what "done" means here.
- The other five stub categories, and the marketplace's own tenant model (see
  Open Question 3).
- Changing internal MCP behavior. Any defect found while extracting shared logic
  is recorded, not fixed in passing, unless leaving it would make the public
  surface wrong.

## Open Questions

1. **Permission checks currently fail open.** When the marketplace lookup
   errors, the internal permission check reports the caller *as permitted*.
   Preserved as-is, an external caller is told "yes" whenever an upstream
   dependency is down. Recommendation: preserve it. This endpoint is advisory —
   the MCP server itself is the enforcement point, so a wrong "yes" here costs
   the caller a failed call, whereas failing closed would make a marketplace
   outage look like a permission revocation. Needs a ruling before the plan
   fixes the branch either way.
2. **Should writing a configuration require permission on the server first?**
   The internal surface does not check; it stores whatever the caller supplies.
   Recommendation: do not add a check — storing a credential for a server you
   cannot yet call is legitimate (permission may be granted later), and adding
   the check would diverge the two surfaces.
3. **The marketplace is not tenant-scoped, and cannot be.** Server listings,
   detail and the MCP tenant list come from an external system with no Avernet
   tenant axis, so every external tenant sees the same catalog. This is believed
   correct — a marketplace is a shared catalog — but it should be a stated
   product position rather than an artifact of where the data lives, because
   "MCP tenants" and "Avernet tenants" being unrelated concepts on the same
   surface is a durable source of confusion.
