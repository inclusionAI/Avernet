# BCS streaming registration recovery

## Problem and scope

A Bot can disconnect before onboarding creates its persistent record. The old
connection flow hides an expired memory record during initial reads, then treats
its retained Bot/token entry as registered during claim. The same request makes
repeated empty DB reads and returns AlreadyRegistered.

This change makes that decision consistent, reuses identity reads within one
handshake, and avoids writing loaded capabilities back on successful reconnect.
The approved September 16 revision puts connection policy in BotCore and storage
mechanics in the existing registry implementations.

Base: `origin/dev` at `0a0a4d00b15c1af6fd50208af8add8cbccf1fdf4`.
Branch: `codex/bcs-streaming-registration`; PR: inclusionAI/Avernet#2219 to `dev`.

## Ownership and call flow

```text
BotCore
  begin_identity_operation() on the injected, existing BotRepoPort
    -> operation borrows PersistentBotRepo or MemoryBotRepo
  resolve token candidate; select Bot ID
  lock_identity(id) -> raw memory facts; hold the per-Bot lock
  stored_identity() -> complete row or successful absence, reused in this scope
  decide reconnect / create / reclaim / MOCK promotion / refusal
  apply(chosen update) -> conditional persistence update and existing-map updates
    -> consuming the operation or dropping it releases the lock
```

Core depends on the contract. The selected store implements that contract and
never calls Core. These are successive calls to one registry, not two Repo layers.
PersistentBotRepo keeps its existing memory maps and SQL DB. MemoryBotRepo remains
the alternative memory/local-file registry.

### BotCore owns

- Token-first identity selection, Bot ID validation and ID/token generation.
- Credential validation and the new/reconnect result.
- Temporary heartbeat expiry: strictly greater than 300 seconds.
- Protection of active connections, durable identities and deleted identities.
- MOCK-promotion and expired-temporary recovery rules, business errors and logs.
- The compatibility `connect_or_promote_streaming` service method, through the
  same Core policy as streaming `connect_bot`.

### Existing registries own

- Raw memory snapshots, complete SQL/file identity reads and index lookup.
- Request-local lookup reuse and the per-Bot lock through decision and update.
- Conditional SQL token replacement, file persistence and existing map updates.
- Maintaining all token aliases and binding indexes on successful attachment.
- Releasing locks when the operation completes, errors, is dropped or is cancelled.

No global Bot map lock or database transaction spans Core's decision. Core does
not call other lock-taking registry methods while its identity scope is live.
The internal policy-bearing repo methods are replaced by
`begin_identity_operation`; the public Core compatibility method is retained.

## Lookup reuse and failures

The operation distinguishes `NotLoaded`, `Loaded(Some(identity))` and
`Loaded(None)`. A successful ID miss belongs to the one locked Bot UUID. It is
not inserted into the registry's shared `bots` or `token_to_bot` maps, and a later
request performs its own read. A read error propagates before Loaded is recorded.
It cannot clear existing memory, authorize recovery, or masquerade as absence.

The token lookup is a separate candidate lookup, not authentication. A token miss
cannot satisfy an ID lookup. A complete cold token row can satisfy the subsequent
ID read only when the IDs match and the process-local mutation epoch is unchanged.
An overlapping local writer forces a fresh read after acquiring the Bot lock.

Identity mutations, heartbeat updates and disconnect cooperate with the same
per-Bot lock. Token replacement uses the loaded expected row; a conditional-write
conflict or persistence failure stops before memory attachment. This coordination
is process-local and does not exclude another instance or direct SQL/file writers.

## Admission rules

| Identity facts | Decision |
| --- | --- |
| No persistent identity and no memory identity | Create temporary identity |
| No persistent identity; unexpired, disconnected temporary identity | Original token reconnects; missing/wrong token is refused |
| No persistent identity; expired, disconnected temporary identity | Reclaim and create a new token; remove all old aliases |
| Active temporary connection | Valid token can reconnect; expiry cannot authorize a new claim |
| Durable identity | Require its current credential; heartbeat expiry cannot reclaim it |
| Deleted identity | Refuse |
| Durable MOCK identity with no active connection | Replace durable token conditionally, then attach |
| Persistent lookup or write fails | Return error; do not treat as missing or attach |

General MemoryBotRepo reads historically retain disconnected token-bearing
objects. Streaming uses raw heartbeat time without changing those general-read
semantics. Runtime agent credentials survive authenticated reconnect and MOCK
promotion; expired temporary recovery creates fresh capabilities.

## Verified SQL budgets

Counts include identity and capability reads inside streaming Core/Store admission.
They exclude Provider checks and later onboarding. Cold lookups with concurrent
local mutations may require an additional verification read.

| Scenario | Reads | Writes | Result |
| --- | ---: | ---: | --- |
| New Bot ID, no token | 1 | 0 | Temporary registration |
| Expired, disconnected temporary identity with remembered old token | 1 | 0 | New token and removal of old aliases |
| Unexpired temporary identity, no credential | 0 | 0 | Refused |
| Valid temporary token reconnect | 1 | 0 | Same identity and token |
| Hot durable reconnect | 1 | 0 | Same identity; no capability write-back |
| Uncontended cold token reconnect | 1 | 0 | Complete token row reused |
| Expired durable identity, no credential | 1 | 0 | Refused |
| Deleted identity, supplied Bot ID | 1 | 0 | Refused |
| MOCK promotion | 1 | 1 | Conditional durable token update |

An unknown token followed by a supplied Bot ID can require two distinct queries.
The reproduced stale temporary path previously made two empty identity reads and
then refused the Bot; it now makes one read and admits it. These statement counts
do not predict total production QPS or prove that client retry traffic has fallen.

## Code and test organization

- `crates/service-api/bcs-service-api/src/port/repo/bot.rs`: facts, update and
  request-scoped operation contracts.
- `crates/services/bcs-bot/src/core/bot_core.rs`: the connection decision.
- `crates/services/bcs-bot-store/src/admission.rs`: lookup state and locking.
- `admission_sql.rs` / `admission_memory.rs` in the same directory: storage-specific
  reads and updates using the original registry fields.
- Store tests verify rows/misses/errors, cancellation, locking, aliases and writes.
  Core tests verify policy, SQL budgets, concurrency, token rotation and credentials.
- The centralized BotIdentityOperationPort conformance harness runs against both
  SQL and local-file registries. Former repo policy tests now exercise Core.

The existing store roots and inline tests remain intact. General file splitting
belongs to the separately requested refactor. No new registry, background cleanup,
cross-request negative cache, schema, config or wire-protocol change is introduced.
Provider, Eventing and client retry behavior remain outside this change.

## Validation (September 16 revision)

- The new boundary regression first failed because Store still owned
  BotConnectParams; it passes after moving policy to Core.
- Host `cargo test --offline --manifest-path src/bcs/Cargo.toml -p bcs-bot-store
  -p bcs-bot -p bcs-service-api -p bcs-ws`: **648 passed, 0 failed, 18 existing
  ignored tests**, across 65 test/doc-test runs. The sandbox could not bind a local
  socket; the host run passed that WebSocket test and the complete selected suites.
- Port purity, forbidden-symbol, store-boundary and interceptor checks pass.
- `cargo check --offline --manifest-path src/bcs/Cargo.toml -p bcs
  -p bcs-bot-store -p bcs-bot -p bcs-service-api -p bcs-ws --all-targets` passes,
  including bootstrap and downstream consumers. Existing unrelated warnings remain.
- Import, trait-naming and R25 static checks match the unchanged DEV archive:
  4, 6 and 151 distinct failures, with zero new findings. The dependency script's
  line-36 unbound-variable failure also reproduces on that archive. The complete
  architecture runner, full-workspace test suite and Singlebox suite were not run
  for this revision; only the R25 static portions were executed.
- Independent review of the revised policy/storage boundary found no blocking or
  important issues. It checked query errors, token reuse, conditional writes,
  deletion, credential preservation and lock lifetime.
- `git diff --check` passes. Added production sources and BotCore are below 1,000
  lines. Existing oversized roots remain: Store lib.rs 3,759, memory.rs 2,733 and
  test-support contract/repo/mod.rs 2,962 (one new harness module declaration).
  File-size refactoring is deferred per the requested scope; no CI rule or
  allowlist is changed.

The contract revision updates both production stores, Core and the test wrapper
together. Downstream source implementations of BotRepoPort must implement the
new operation. Existing wire fields and error classes remain compatible. Rollback
is a code revert with no data migration. Deployment is separate from this PR.
