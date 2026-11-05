# Cluster Executor Plane — Storage Choice

[简体中文](storage.zh-CN.md) · Part of the [cluster executor plane design](design.md)


## 1. Recommendation: OceanBase as the system of record

Rule: **anything that carries a fencing token or was acknowledged lives in
OceanBase; Redis only for state that can be lost and rebuilt.**

| Requirement | Redis Cluster | OceanBase |
| --- | --- | --- |
| Durable before ack | ✗ async replication can lose acked writes | ✓ Paxos majority commit |
| Token never goes backwards | ✗ not across failover (see below) | ✓ |
| Atomic check-and-write | Lua within one hash slot | Transaction (keep it single-partition) |
| Indexed queries | Hand-built | SQL |
| Months of data | RAM only | Disk (LSM, compressed) |
| Write scaling | Linear, sub-ms | By partitions; a few ms per commit |

**Redis is a good fit for:** soft rate limits, read
caches of rarely-changing content, best-effort wake-ups, and the
non-durable session channels that carry fan-out (design §7.4; sharded
pub/sub only).

## 2. What a storage failover does

![Storage failover: with asynchronous replication the master acks before the replica has the write; after promotion the replica lacks token 8 and a second claimant also gets token 8 — two owners. With majority replication an acked write is on a majority, every possible new leader has it, and the second claim's CAS touches zero rows.](assets/replication-failover.svg)

A store keeps copies; when the primary dies a replica is promoted. With
**asynchronous** replication (Redis; MySQL async) the primary acks before
replicas have the write, so a failover can drop an acked claim. Then a
second claimant is granted the *same* token number, and the fence can no
longer tell the two owners apart. A partitioned old primary can also keep
accepting writes until its timeout (split brain), and those are discarded
when it rejoins. `WAIT` does not make Redis strongly consistent.

With **majority** replication (OceanBase Paxos; Raft), a commit is acked only
after it is on a majority, any two majorities overlap, and only an
up-to-date replica can become leader — so no acked write is lost and a
minority-side leader cannot commit at all. The price: a few ms per commit,
and a few seconds of stalled writes during a leader switch.

The question to ask of any store: **"when it says OK, how many machines have
the write on disk?"** For tokens and acked rounds the answer must be "a
majority". (MySQL therefore requires lossless semi-synchronous replication.)

## 3. OceanBase specifics

- **Co-partition by bucket.** A fenced transaction touches the bucket's
  lease row and the session's rows, so partition `bcs_task_leases` and every
  per-session table by `bucket`, with the same partition count, in one
  **table group**. The transaction then stays on one partition. Otherwise
  every fenced write is a distributed (two-phase) transaction.
- Per-session tables include `bucket` in their primary keys (e.g.
  `(bucket, session_id)`), because a partition key must be part of the
  primary key.
- Default isolation is READ COMMITTED; the design relies only on row locks —
  the shared (or exclusive) lock of the fence step and the exclusive lock of
  a claim or transfer — which behave the same.
- Avoid hot rows (any cluster-wide counter touched per request). A bucket
  row is touched by every fenced write of its sessions, which is why the
  fence step takes a shared lock where it can (design §5.5).
- Handle unknown commit outcomes during leader switches by reading back
  ([design §5.5](design.md#55-fenced-writes)).
