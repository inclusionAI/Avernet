# Provider Bot storage: rollout and rollback

## Contract

`bcs_bots` is the authority for `provider_id`, `provider_bot_ref`,
`connection_mode` (`plugin` or `gateway`) and nullable `webhook_url`.
Existing `is_deleted` is the lifecycle authority. Provider
affiliation alone does not imply HTTP delivery or grant gateway-only permissions.

No separate Provider timestamps are stored on the Bot. Bot lifecycle timestamps
remain unchanged; legacy binding response timestamps still come from the binding.
The Bot-mode read source selects direction and metadata from the Bot and obtains
only compatibility timestamps from its matching gateway binding. Missing gateway
bindings fail explicitly rather than inventing timestamp values.

Provider metadata writes use business-state conditions, unique constraints and
transactions, not a timestamp/version precondition. Concurrent explicit webhook
updates follow database write ordering; repeated affiliation is a no-op and cannot
restore a stale callback. The existing single-binding lock is unchanged; no new
Bot or Provider-wide explicit lock is introduced. Updating a Provider itself does
not update its Bots.

Provider-admin DTOs, registration token scopes, OpenAPI registration and Bot
storage reuse `bcs-domain::ProviderBotConnectionMode::{Gateway, Plugin}`.
`plugin` is the existing name for upstream WebSocket/plugin/bridge connections;
there is no separate `upstream` wire or stored value. The old Provider-admin
default remains `gateway`; token/register explicitly defaults to `plugin`.
Only this PR's undeployed MySQL 029 / SQLite 030 drafts are corrected: the SQLite
mode constraint uses the shared enum, and both omit duplicate Provider timestamps.
MySQL uses VARCHAR without a mode default or CHECK. Earlier
migration names and checksums are untouched. Retained development databases
using the discarded draft require an explicit upgrade or recreation, and draft
tokens must be reissued; never edit recorded checksums to bypass validation.

Gateway creation, webhook updates and soft deletion still maintain
`bcs_provider_bot_bindings`. Upstream never writes a binding. SQL couples the Bot
and binding writes in one transaction; the Memory implementation is process-local.
Owner edges are a separate operation: registration errors after Bot creation can
require operator repair. Scoped duplicate refs return 409, never a replayed token.
Different refs can reuse a still-valid registration token.

```toml
[provider_http]
downlink_detection_source = "binding" # default; compatible legacy direction reads
# downlink_detection_source = "bot_connection_mode" # opt in only after audit
```

The selector changes HTTP delivery classification, WS admission and downstream
authorization through the same injected projection. It never changes write policy.
Bot-mode reads fail on a missing migration state rather than guessing upstream.
Legacy Provider Bot list/discovery scopes remain gateway-only; this change does not
add an upstream membership-list API. Existing Provider-admin plugin registration
keeps its credential behavior; scoped token registration returns real Bot tokens.

## Schema expansion and read-source prerequisites

Apply MySQL migration **029** (`029_bot_provider_storage.sql`) before new server
code; SQLite bootstrap applies **030**. Earlier upstream migration names, numbers
and checksums remain frozen. New ordinary Bots explicitly store `plugin`, but
existing rows can retain nullable migration-state fields.

Historical data correction is a separate, reviewed deployment work order.
The repository ships no dedicated backfill command, report type or DB operation,
and server startup does not infer historical Provider membership. The correction
rules and execution checklist are maintained outside the product repository for
the deployment work order; no live data correction is performed by this change.

Keep `binding` reads until the work order has reconciled and validated the target
environment. Gate any switch to `bot_connection_mode` on an issue-free audit,
complete mode/affiliation data, preserved Bot credentials/owners/lifecycle,
consistent gateway projections and representative runtime verification. Missing
historical affiliation must not be guessed from credentials or display names.

The work order owns environment selection, backups, writer fencing, audited
target rows, database-specific execution and rollback. Stop or fence every writer
before correction; do not enable new cross-mode registration while old writers
remain. Resume only dual-write-capable writers after validation.

Verify legacy gateways, Provider upstream Bots, ordinary Bots, webhook
inheritance/overrides and deleted Bots before changing the read-source setting.
The switch requires a restart; it is not a live hot reload. An empty fresh
database normally has no historical rows to correct.

## Rollback and limitations

- To revert direction reads, restore `binding` and restart. Gateway dual writes
  remain enabled and upstream still has no binding. Keep the new schema and data.
- This is **not** permission to roll back to arbitrary old binaries: those do not
  maintain Bot affiliation or synchronized lifecycle and can invalidate the new
  invariants. Fence writes and use a separately reviewed reconciliation/snapshot
  plan for a binary rollback. Never drop the new columns as a routine rollback.
- No production migration is performed by development tests. SQLite schema,
  normal transaction rollback and runtime contracts are locally testable. Real
  MySQL full-chain migration/conformance and deployment-scale cutover must be
  verified on a disposable/staging MySQL instance before production rollout.
- Data-correction execution, capacity/lock planning and its evidence belong to
  the separate work order; product tests do not certify those deployment steps.
- Existing Provider caches retain their 30-second cross-instance visibility
  limit. Local successful writes invalidate their binding cache; restart/drain
  instances during cutover. No new distributed cache-coherence guarantee is added.
- This PR has never been deployed. Its discarded registration-table draft is
  removed from the migration chain; no cleanup/drop migration
  or compatibility table is required. Fresh databases never create it.
