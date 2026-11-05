# Cluster Executor Plane — Capacity and Cost Model

[简体中文](capacity.zh-CN.md) · Part of the [cluster executor plane design](design.md)

## 1. Statements per operation

| Operation | Statements | Frequency |
| --- | --- | --- |
| Heartbeat (renews all of an executor's buckets) | 1 write | per node per second |
| Roster read | 1 read | per node per second |
| Claim of a bucket (either outcome) | 2 in one transaction (conditional increment + read-back) | only after a crash or at first start: ~4096/N per dead executor, split over the survivors; never per session |
| Fence step | +1 single-row PK read of the bucket row, shared lock (`FOR UPDATE` if shared locks are unavailable) | per owner write transaction |
| Transfer of a bucket | 1 | per drain or rebalance: ~4096/N per executor |
| Inbound round persist | 1 | per round (required by write-before-ack) |
| Owner pickup of fallback input | 1 indexed query over the owner's buckets **per executor per tick**, never per session | per executor per tick |
| Failover | 1 indexed read of the dead executor's buckets (~4096/N rows) per survivor, then per claimed bucket, reads of its pending sessions in batches of 2,000 | once per dead executor; nothing to watch afterwards |
| Transferred, not-yet-adopted buckets | 1 indexed query per executor per tick (`adopted = FALSE`, at most a few hundred rows; usually empty) | per executor per tick |
| Session event (control event for subscribers) | +1 insert inside an existing fenced transaction, no extra round trip | per state change, interrupt or step result — a few per step, never per token |
| Event log read | 1 indexed range read (`event_seq > :last`); catch-up reads up to 500 sessions per query | per gap or reconnect snapshot; batched catch-up only after a pub/sub disconnect, plus a safety check per unfinished session silent for 30 s — at 300 k streams ≤ 10 k sessions/s, i.e. ~20 queries/s |
| Tokens (deltas) | **0** database statements. Per 200 ms batch: 1 message dispatch gateway → owner and 1 publish on the session channel, fanned out by the pub/sub | — (network and pub/sub capacity, never storage) |
| Gateway ↔ executor messages | 1 per round, per control event, per delta batch and per dispatch result | — (network, not storage) |

The rule behind every row: costs scale with **nodes and buckets**, never
with **sessions**, except for work a session actually does — and never with
**tokens**: token traffic costs network and pub/sub capacity, never database
statements. These counts should be
pinned by tests so a later change cannot turn a per-node query into a
per-session one.

## 2. Two scenarios

Little's law: in-flight sessions = arrival rate × average run duration.

| | 10 k API QPS, mostly reads | 100 k new rounds/s |
| --- | --- | --- |
| In-flight sessions (60 s runs) | small | ~6 M (6 M session rows; still 4,096 lease rows) |
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
