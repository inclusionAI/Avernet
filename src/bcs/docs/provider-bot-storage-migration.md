# Provider Bot storage: rollout and rollback

## Contract

`bcs_bots` is the authority for `provider_id`, `provider_bot_ref`,
`connection_mode` (`upstream` or `gateway`), nullable `webhook_url`, and Provider
metadata timestamps. Existing `is_deleted` is the lifecycle authority. Provider
affiliation alone does not imply HTTP delivery or grant gateway-only permissions.

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

## Expand, fence, backfill, validate, switch

1. Back up the database and retain a recoverable pre-upgrade snapshot. Apply
   MySQL migration **029** (`029_bot_provider_storage.sql`) using the normal
   migration tooling. SQLite bootstrap applies version **030**. Earlier upstream migration
   names, numbers and checksums must remain unchanged. Existing rows start with
   nullable migration-state fields; new ordinary Bots explicitly use upstream.
2. Keep `binding` reads while deploying the dual-write code. Do not enable new
   cross-mode registration while old writers remain. Before the backfill, stop or
   fence **every** writer for the target environment, including old binaries,
   admin scripts, registration and lifecycle mutation paths. The CLI assertion
   below is an operator confirmation, not an automatic distributed lock.
3. Run the audit from `src/bcs` against an already-expanded database. `SERVER_ENV`
   must match the existing data; `--env` is an additional assertion, not a selector
   that bypasses configured environment isolation. Example for a local database:

   ```sh
   SERVER_ENV=local cargo run --locked -p bcs --example provider_bot_backfill -- \
     --sqlite /absolute/path/to/bcs.db --env local
   ```

   MySQL uses `--mysql-config /absolute/path/to/protected-mysql.toml` instead of
   `--sqlite`. The file contains the existing `MysqlDbConfig` format; keep secrets
   out of command arguments, source control and logs. The utility never prints it.
4. Resolve every audit issue and rerun. The audit reports orphan Bots/bindings,
   missing Providers, partial metadata, duplicate refs and binding/Bot lifecycle
   mismatches. It does not guess
   whether an old `disabled` binding should delete an active Bot. Operators must
   choose the intended lifecycle state and reconcile it explicitly. No automatic
   cleanup, token rotation, ownership changes or tombstone resurrection occurs.
5. With all writers still fenced, append `--apply --writers-fenced` to the same
   command. Any audit issue blocks the whole apply; checked updates share one SQL
   transaction. Run another audit and require empty `issues` and zero remaining
   backfill counts before resuming only dual-write-capable writers. Repeat for
   every data environment.
6. Verify representative legacy gateways, upstream Provider Bots, ordinary Bots,
   webhook inheritance/overrides and deleted Bots. Then switch to
   `bot_connection_mode` and restart the affected instances. Validate WS rejection
   for gateways, upstream WS reconnect, HTTP routing and Provider callbacks. The
   switch is configuration-driven at process assembly, not a live hot reload.

The utility copies gateway identity/override/timestamps from legacy bindings.
Ordinary unmigrated Bots become upstream; existing Bot-owned Provider metadata
is validated and preserved. Old plugin registrations without durable Provider
metadata or a binding have no recoverable affiliation evidence. Reconcile those
identities through verified Provider-admin registration before relying on complete
membership reporting. Backfill does not change or duplicate Bot credentials and
does not use their values as affiliation evidence.

## Rollback and limitations

- To revert direction reads, restore `binding` and restart. Gateway dual writes
  remain enabled and upstream still has no binding. Keep the new schema and data.
- This is **not** permission to roll back to arbitrary old binaries: those do not
  maintain Bot affiliation or synchronized lifecycle and can invalidate the new
  invariants. Fence writes and use a separately reviewed reconciliation/snapshot
  plan for a binary rollback. Never drop the new columns as a routine rollback.
- No production migration is performed by development tests. SQLite migration,
  backfill, rollback-on-failure and runtime contracts are locally testable. Real
  MySQL full-chain migration/conformance and deployment-scale cutover must be
  verified on a disposable/staging MySQL instance before production rollout.
- The backfill loads one environment's relevant rows in memory and applies one
  transaction. Size the maintenance window and DB locks using a staging copy;
  it is not an online, unbounded-data migration worker.
- Existing Provider caches retain their 30-second cross-instance visibility
  limit. Local successful writes invalidate their binding cache; restart/drain
  instances during cutover. No new distributed cache-coherence guarantee is added.
- This PR has never been deployed. Its discarded registration-table draft is
  removed from the migration chain and backfill code; no cleanup/drop migration
  or compatibility table is required. Fresh databases never create it.
