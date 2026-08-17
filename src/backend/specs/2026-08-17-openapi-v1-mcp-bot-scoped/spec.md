# Public API — MCP, completed (config lifecycle + bot-scoped activation)

## Summary

The public API's `mcp` category can store a credential for a marketplace MCP
server, and that is all it can do with one. It cannot clear the credential, it
cannot list the credentials it holds, and — the load-bearing one — it cannot make
any bot *use* the server. This closes all three, and it does the third without
carrying the skill-set concept onto the public contract.

## Motivation

An external tenant that configures an MCP server today gets a `200` and a stored
row, and nothing else happens. That is not a bug in the write path: the row is
the source of truth, and `build_mcp_sync_payload` reads it back on every later
sync and on artifact compose, so storing config for a server no bot yet carries
is a legitimate write-ahead. The problem is that the second half of the pair —
putting the server on a bot — has no public surface at all.

A bot's MCP set comes from `collect_bot_active_mcps`: the MCPs of the bot's
*active skill sets*, plus engine defaults, minus per-bot exclusions. The public
API can reach none of those three inputs. So an external tenant can hold a valid
API key for a real server forever and never have an agent call it.

Three things make this the right slice:

- **It is the last thing standing between the category and being usable.**
  Marketplace browsing, permission reporting and credential storage are all
  published. Activation is the one missing link in the chain, and without it the
  other five operations have no payoff.
- **The precedent already exists, in the mirror.** The public `skills` category
  publishes `activate`/`deactivate` and keeps skill sets entirely off the
  contract, by resolving the bot's *default* skill set inside the service. Every
  repository method that pattern needs on the MCP side already exists.
- **The two config gaps are cheap and sit in the same file.** A caller who can
  activate a server will immediately want to revoke its credential and to
  enumerate what it has configured. Shipping activation without them publishes a
  surface that can create state it cannot inspect or undo.

## User Stories

### Config lifecycle

- As an external tenant, I want to delete my stored configuration for an MCP
  server, so that a credential I no longer trust stops existing rather than being
  overwritten with a dummy value.
- As an external tenant, I want the deletion to reach my devices, so that a
  revoked credential is not still resident on a running agent.
- As an external tenant, I want to list the servers I have configured, so that I
  can audit my own state without already knowing every server code.
- As an external tenant, I want listed credentials masked exactly as the
  single-server read masks them, so that enumeration is not a way to exfiltrate
  what the single read protects.

### Bot-scoped activation

- As an external tenant, I want to add an MCP server to one of my bots, so that
  the credential I stored becomes reachable by that bot's agent.
- As an external tenant, I want to activate and deactivate a server on a bot
  without deleting it, so that I can turn a capability off and back on without
  re-adding it.
- As an external tenant, I want to list the MCP servers on a bot with their
  active state, so that I can see what that bot can actually call.
- As an external tenant, I want to remove a server from a bot entirely, so that a
  bot I am repurposing does not keep a capability I forgot about.
- As an external tenant, I want activation to take effect on the bot's runtime
  when the call returns, so that a success is not a promise about the future.
- As an external tenant, I want to reach only my own bots, so that a bot id I
  guess tells me nothing.
- As an integrator, I want never to encounter a "skill set" on this surface, so
  that the MCP contract is about MCP.

### Unchanged

- As a caller of the existing internal MCP API, I want every response to be
  exactly what it is today.
- As a caller of the five already-published MCP operations, I want their
  behaviour unchanged, including `PUT config`'s merge semantics and its
  write-then-push-then-roll-back-on-failure sequence.

## Acceptance Criteria

### Delete a configuration

- [ ] Deleting the caller's configuration for a server removes the stored row and
      reports success.
- [ ] The deletion is pushed to the caller's devices before it is reported. If the
      push fails, the row is restored and the call fails — mirroring the write
      path's atomicity, so a caller never believes a credential is gone while an
      agent still holds it.
- [ ] Deleting a configuration that does not exist succeeds and reports that
      nothing was deleted, rather than answering not-found. Revoking twice is not
      an error.
- [ ] Deleting a configuration for a server code that does not exist in the
      marketplace answers not-found, consistent with the write path refusing an
      unknown server before it touches the database.
- [ ] Deleting a configuration does not deactivate the server on any bot. The two
      are independent axes and the response says so.

### List configurations

- [ ] Listing returns the servers the caller has configured, paginated, with a
      total.
- [ ] Every entry carries the same fields the single-server read returns, with the
      api_key masked by the identical rule — for any key length, including short
      ones.
- [ ] A caller who has configured nothing gets an empty page, not an error.
- [ ] The listing is confined to the caller's own configurations. There is no
      parameter that points it at another user.

### Add a server to a bot

- [ ] Adding a marketplace server to a bot succeeds and reports the server as
      present and active.
- [ ] Adding a server that is already on the bot succeeds and reports that nothing
      changed, rather than erroring or creating a duplicate.
- [ ] Adding a server that does not exist in the marketplace, or that the
      network-type rule hides, answers not-found — the same not-found, from one
      site, so the restriction is not a way to learn a server exists.
- [ ] Adding to a bot the caller does not own answers not-found, identically to a
      bot id that does not exist.
- [ ] Adding a server does not require a stored credential. A server may be added
      before its config is written, and the credential is picked up on the next
      sync.

### Activate / deactivate

- [ ] Activating a server on a bot makes it reachable by that bot's agent, and the
      bot's runtime reflects it before the call returns.
- [ ] Deactivating removes it from what the agent can call, again reflected before
      the call returns, and without removing it from the bot.
- [ ] Both are idempotent: acting on a server already in the requested state
      succeeds and reports `changed: false`.
- [ ] If the runtime cannot be reconciled, the stored state is restored and the
      call fails. A caller is never told a server is active while the agent cannot
      call it.
- [ ] Activating a server that is not on the bot answers not-found rather than
      silently adding it.

### List a bot's servers

- [ ] Listing a bot's MCP servers returns each server with its active state,
      paginated, with a total.
- [ ] Engine-default servers appear in the listing, with their active state
      reflecting whether the caller has deactivated them. A default the caller
      never touched reads as active.
- [ ] Reading a single server's state on a bot returns the same shape as one
      listing entry, and answers not-found for a server that is not on the bot.

### Remove a server from a bot

- [ ] Removing a server from a bot takes it out of the listing and out of what the
      agent can call.
- [ ] Removing a server the bot does not have succeeds and reports that nothing
      was removed.
- [ ] Removing an engine-default server is refused, with an error saying to
      deactivate it instead. A default is supplied by the engine rather than
      stored, so "not on the bot" is not a state it can hold — only
      "deactivated" is.
- [ ] Removing a server from a bot does not delete the caller's stored credential
      for it — the credential is account state and outlives any one bot.

### Cross-cutting

- [ ] Every new operation appears in the admission table exactly once, and the
      surface and the table agree in both directions.
- [ ] The bot-scoped operations are reachable by an application acting under a
      grant, on the same terms as the equivalent `skills` operations. The two new
      account-level config operations are not, on the same terms as the config
      operations they join.
- [ ] Every failure arrives in the standard envelope with the standard error
      shape.
- [ ] The word "skill set" appears nowhere in the published document.

## Open Questions

1. **Should `DELETE config` also deactivate the server everywhere?** Recorded as
   *no* above — credential and activation are independent axes, and conflating
   them means a caller rotating a key has to re-activate on every bot. Flagged
   because the opposite reading ("revoke means revoke") is defensible and this is
   the one place the two axes touch.

2. **Should adding a server to a bot activate it?** Recorded as *yes* (added
   servers arrive active) because the alternative makes the common case two calls
   for no benefit. `skills` differs here — an uploaded skill is inert until
   activated — but a skill upload is a package transfer, whereas adding an MCP
   server is a statement of intent to use it.

## Out of Scope

- **Caller identity.** `McpCallType` OWNER/CALLER and `ac_bot_mcp_call_config`
  are untouched. This surface does not publish, read or change execution
  identity.
- **Requesting access.** Applying for a permission on a non-public server is
  issue [#1109](https://github.com/inclusionAI/Avernet/issues/1109).
- **Non-staff entity types.** `proj` and `team` stay unreachable, consistent with
  every other group on this surface.
- **Multi-tenant key changes.** Single tenant assumed; the `ac_user_mcp_config`
  unique-key swap is not a prerequisite here.
- **Changing `PUT config`.** Its merge semantics, its push, and its rollback are
  correct and stay exactly as they are.
- **Registering custom MCP servers.** The catalogue remains the marketplace plus
  whatever the deployment serves locally.
