# Cluster Executor Plane — Capacity and Cost Model

[简体中文](capacity.zh-CN.md) · Part of the [cluster executor plane design](design.md)

## 1. Statements per operation

| Operation | Statements | Frequency |
| --- | --- | --- |
| Heartbeat (renews all of a node's leases) | 1 write | per node per 5 s |
| Roster read | 1 read | per node per 5 s |
| Claim (either outcome) | 2 in one transaction (conditional increment + read-back) | per new episode; rarely after membership change |
| Fence step | +1 single-row PK update per fenced transaction | per owner write transaction |
| Release / transfer | 1 | per drain or rebalance |
| Inbound round persist | 1 | per round (required by write-before-ack) |
| Owner pickup of fallback input | 1 indexed query **per node per tick**, never per session | per node per tick |
| Failure discovery | 1 paginated query per survivor per dead node | per death |

These counts become `RecordingDb` regression tests, so a later change cannot
silently turn a per-node query into a per-session one.

## 2. Two scenarios

Little's law: in-flight sessions = arrival rate × average run duration.

| | 10 k API QPS, mostly reads | 100 k new rounds/s |
| --- | --- | --- |
| In-flight sessions (60 s runs) | small | ~6 M (6 M lease rows) |
| DB statements/s | ~10–30 k | ~1–5 M, mostly writes |
| New data | small | ~1–10 GB/s (~85–850 TB/day; assumes 10–100 KB per round) |
| Binding constraint | none | storage write throughput and volume |

At the 100 k scale: OceanBase with co-partitioning; fewer statements per
round (the repeated full reads flagged in
`specs/2026-09-21-nested-fixed-loops/capacity.md`); micro-batching inbound
writes; persist chunks or final output, not every token; L3 retention and
compaction become mandatory; size the tenant by load test. These figures
are estimates, not measurements.

## 3. How to replace estimates with measurements

1. Count statements per operation with `RecordingDb`.
2. Load-test one node against real storage (release build) to find its knee.
3. Load-test the storage tenant at the measured statement mix.
4. Kill a node at load to measure the rehydration spike.
