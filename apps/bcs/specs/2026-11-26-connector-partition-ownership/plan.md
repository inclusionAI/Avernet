# Plan: Connector partition ownership

## Reuse

- **Owner records and their transitions**: `ShardLeasePort` as implemented by
  `CoordinationStore` (claim with expected epoch and `Dead`-only takeover,
  receiver-committed handoff, `clear_handoff`). A partition is a `ShardId`, its
  owner epoch a `WorkerEpoch`. Only the key differs: a `CoordinationStore`
  built with `with_partition_owners(pool)` stores owner records under
  `bcn:v1:<c>:<ns>:<pool>:p<nnnn>:owner` instead of
  `bcn:v1:<c>:<ns>:s<nnnn>:owner`. The same instance also serves as the
  Connector's `MembershipPort`.
- **Candidates**: `MembershipPort::members(RuntimeRole::Connector, …)` (live,
  non-draining, with control address) and HRW ranking (a Connector-local copy
  of the Worker's `rank_candidates` with its own domain string).
- **Peer transport**: `DistWorkerPeerPort` / `HttpWorkerPeer` (with a
  `/internal/bcn/connector/` path prefix) and a `connector_router` in
  `bcs-dist-worker-http` over `DistConnectorRuntimeService::accept_handoff`.
  `source_done` is not used: the source stops every call before it offers a
  partition and retires right after, so the target can clear the handoff once
  the source is `Dead`.

## Changes by crate

### `bcs-dist-store`
- `layout.rs`: `partition_owner(pool, p)`, `partition_held(pool, p)`,
  `partition_text(p)` (`p0003`).
- `coordination`: owner-key scope (Worker shards, or the partitions of a pool), builder
  `with_partition_owners(pool)`; `shard.rs` uses `owner_key(shard)`.

### `bcs-service-api`
- `DispatchClaimRequest.partition: Option<ShardClaim>`: the claimant's proof of
  partition ownership.
- `DispatchQueuePort::command_partitions()` / `partition_of(did)`: the engine
  learns the partition layout from the board instead of depending on the
  store crate.
- `ClaimOutcome::NotOwner`: the proof is stale or names another instance.
- `DispatchQueuePort::held_in(pool, partition, limit)` (default: unsupported).
- `CommandFeedPort::assign(&[u32])` (default: no-op; the claim still checks).
- `DistConnectorRuntimeService::accept_handoff` (default: unsupported).

### `bcs-dist-connector-store`
- `held_index.rs`: per-partition set keyed from `spec.pool` and the did's
  partition; removed only when a claim reads it `Ended`, or after `finish`.
- `queue.rs`: new constructor parameter `partitions: Arc<dyn ShardLeasePort>`;
  claim rule; `held_in`.
- `feed.rs`: `assign` limits group reads and idle takeover to owned partitions
  (reads in flight are never dropped).

### `bcs-dist-connector`
- `config.rs`: `ownership_interval` (1 s), `candidate_backoff` (500 ms),
  `peer_timeout` (2 s), `handoff_reserve` (5 s); ports `partitions`, `peers`.
  This process always counts itself as a candidate; with the stub membership
  it is the only one (a single Connector owns every partition).
- `engine/partitions.rs` (new): ownership round, recovery of acquired
  partitions, outgoing handoff on drain, incoming `accept_handoff`.
- `call.rs`: shared `yielding` flag; the follow loop and `park` stop at a safe
  point (`Stop::Yielded`).
- `engine.rs`: run the ownership round each pass, assign the feed, pass the
  ownership proof with every claim, handle `NotOwner`; drain = request yield →
  wait → stop leftovers → hand off partitions → retire.

### `bcs-app-dist-runtime`, `bcs-dist-worker-http`, `bcs-dist-worker-client`
- Connector facade forwards `accept_handoff` after binding the caller.
- `connector_router`; `HttpWorkerPeer::connector(...)`.

## Risks

- Every claim reads the partition owner record once more (one document read).
- Hints for a partition whose owner died wait until a new owner claims it
  (≤ one ownership round plus the HRW stagger) — the Worker re-posts anyway.

## Drain outcome

`remaining` now also counts partitions that were not handed over (no peer, or
no time left): a lone Connector's drain reports `DeadlineReached { remaining:
1 }` for its partition, which is claimed once it is `Dead`.
