# Cluster Executor Plane — Capacity and Cost Model

[简体中文](capacity.zh-CN.md) · Part of the [cluster executor plane design](design.md)

## 1. Statements per operation

| Operation | Statements | Frequency |
| --- | --- | --- |
| Heartbeat (renews all of an executor's buckets) | 1 write | per node per second |
| Roster read | 1 read | per node per second |
| Claim of a bucket (either outcome) | 2 in one transaction (conditional increment + read-back) | only after a crash or at first start: ~4096/N per dead executor, split over the survivors; never per session |
| Fence step | +1 single-row PK checked UPDATE of the bucket row (token must match; exclusive lock) | per owner write transaction |
| Transfer of a bucket | 1 | per drain or rebalance: ~4096/N per executor |
| Inbound round persist | 1 | per round (required by write-before-ack) |
| Owner pickup of fallback input | 1 indexed query over the owner's buckets **per executor per tick**, never per session | per executor per tick |
| Failover | 1 indexed read of the dead executor's buckets (~4096/N rows) per survivor, then per claimed bucket, reads of its pending sessions in batches of 2,000 | once per dead executor; nothing to watch afterwards |
| Transferred, not-yet-adopted buckets | 1 indexed query per executor per tick (`adopted = FALSE`, at most a few hundred rows; usually empty) | per executor per tick |
| Event (state change for the PA) | No extra statement: the Task record, `last_event_seq` and `unpublished` change inside the existing fenced transaction; then 1 stream append | per state change, interrupt or step result — a few per step, never per token |
| Reconnect snapshot | **0** database statements: built from the session stream | per reconnect |
| Tokens (deltas) | **0** database statements. Per 200 ms batch: 1 publish on the bucket's delta channel (gateway → owner) and 1 append to the session stream (owner → gateways) | — (network and pub/sub capacity, never storage) |
| Gateway ↔ executor messages | 1 per round, per control event and per dispatch result | — (network, not storage) |

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

**Message queue.** At 200 ms batches a stream publishes 5 delta messages/s
into its bucket's delta channel and the owner appends 5 more to the session
stream; 300 k concurrent streams are ~3 M operations/s before fan-out
(usually one reader per stream). Size Redis for that, or raise the batch
interval.

**Session stream memory.** A stream keeps entries since its latest snapshot
(at most one step's output, ~10 KB, plus the snapshot itself) while its Task
is open, and is deleted after the retention period. At 300 k open Tasks
that is a few GB, plus replicas for quorum durability.

## 3. How to replace estimates with measurements

1. Count statements per operation in tests.
2. Load-test one node against real storage (release build) to find its knee.
3. Load-test the storage tenant at the measured statement mix.
4. Kill an executor at load to measure the rehydration spike, and a gateway
   to measure the reconnect and snapshot spike.
