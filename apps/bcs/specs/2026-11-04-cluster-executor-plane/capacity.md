# Cluster Executor Plane — Capacity and Cost Model

[简体中文](capacity.zh-CN.md) · Part of the [cluster executor plane design](design.md)

## 1. Statements per operation

| Operation | Statements | Frequency |
| --- | --- | --- |
| Heartbeat (renews all of a node's leases) | 1 write | per node per second |
| Roster read | 1 read | per node per second |
| Claim (either outcome) | 2 in one transaction (conditional increment + read-back) | per new episode; rarely after membership change |
| Fence step | +1 single-row PK update per fenced transaction | per owner write transaction |
| Release / transfer | 1 | per drain or rebalance |
| Inbound round persist | 1 | per round (required by write-before-ack) |
| Owner pickup of fallback input | 1 indexed query **per node per tick**, never per session | per node per tick |
| Failure discovery | 1 indexed query per survivor per dead node per poll, reading only that survivor's buckets, at most one batch (2,000 rows); once its own share is empty, 1 `LIMIT 1` probe over all buckets | while a node is dead, until it is retired; one per-tick budget across all dead nodes |
| Transferred, not-yet-adopted leases | 1 indexed query per node per tick (`adopted = FALSE`, at most one 2,000-row batch; usually empty) | per node per tick |
| Session event (control event for subscribers) | +1 insert inside an existing fenced transaction, no extra round trip | per state change, interrupt or step result — a few per step, never per token |
| Event log read | 1 indexed range read (`event_seq > :last`) | per gap, reconnect snapshot, or poll while a session channel is down |
| Tokens (deltas) | **0** database statements. Per 200 ms batch: 1 message dispatch gateway → owner and 1 publish on the session channel, fanned out by the pub/sub | — (network and pub/sub capacity, never storage) |
| Gateway ↔ executor messages | 1 per round, per control event, per delta batch and per dispatch result | — (network, not storage) |

The rule behind every row: costs scale with **nodes**, never with
**sessions**, except for work a session actually does — and never with
**tokens**: token traffic costs network and pub/sub capacity, never database
statements. These counts should be
pinned by tests so a later change cannot turn a per-node query into a
per-session one.

## 2. Two scenarios

Little's law: in-flight sessions = arrival rate × average run duration.

| | 10 k API QPS, mostly reads | 100 k new rounds/s |
| --- | --- | --- |
| In-flight sessions (60 s runs) | small | ~6 M (6 M lease rows) |
| DB statements/s | ~10–30 k | ~1–5 M, mostly writes |
| New data | small | ~1–10 GB/s (~85–850 TB/day; assumes 10–100 KB per round) |
| Binding constraint | none | storage write throughput and volume |

At the 100 k scale: OceanBase with co-partitioning; fewer statements per
round; micro-batching inbound writes; persist chunks or final output, not every token; L3 retention and
compaction become mandatory; size the tenant by load test. These figures
are estimates, not measurements.

**Step buffers.** Each in-flight dispatch's output so far is held twice: by
the dispatch gateway (to assemble the result and hand it to a new owner) and
by the owner (for snapshots). At about 10 KB per step, 300 k concurrent
dispatches need ~3 GB across gateways and ~3 GB across executors. No storage
operations; both copies are dropped when the step's result is recorded.

**Pub/sub.** At 200 ms batches a stream publishes 5 messages/s; 300 k
concurrent streams are ~1.5 M publishes/s before fan-out (usually one
subscriber per session). Size the sharded pub/sub for that, or raise the
batch interval.

## 3. How to replace estimates with measurements

1. Count statements per operation in tests.
2. Load-test one node against real storage (release build) to find its knee.
3. Load-test the storage tenant at the measured statement mix.
4. Kill an executor at load to measure the rehydration spike, and a gateway
   to measure the reconnect and snapshot spike.
