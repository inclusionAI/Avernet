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
| Subscription replay | 1 indexed range read (`event_seq > :last`) | per re-subscribe, i.e. after an owner change or a reconnect |
| Tokens (deltas) | **0** statements; relayed in gateway memory | — |
| Gateway ↔ executor messages | 1 per round, per control event and per dispatch result, plus 1 batched re-assertion per gateway–executor pair every 5 s | — (network, not storage) |

The rule behind every row: costs scale with **nodes**, never with
**sessions**, except for work a session actually does — and never with
**tokens**, which stay in the gateway tier. These counts should be
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

**Gateway memory.** Each in-flight dispatch keeps a step buffer of the
current step's output until the owner records the result: about 10 KB at a
typical step size, so ~3 GB across all gateways for 300 k concurrent
dispatches. It costs no storage operations and is dropped when the step
ends.

## 3. How to replace estimates with measurements

1. Count statements per operation in tests.
2. Load-test one node against real storage (release build) to find its knee.
3. Load-test the storage tenant at the measured statement mix.
4. Kill an executor at load to measure the rehydration spike, and a gateway
   to measure the reconnect and snapshot spike.
