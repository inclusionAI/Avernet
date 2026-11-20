# Research: mapping the Gateway design onto `bcs-store-api`

Read on 2026-11-17 against `dev` at `5c3e30ea`: `crates/plugin-api/bcs-store-api`
(README, `src/`), the conformance model `bcs_test_support::store_model::Model`,
and the shared base `crates/services/bcs-dist-store` (#4115).

Result: **everything the Gateway needs is covered by the trait.** Nothing is
missing and nothing behaves differently from the brief. Three things are not in
the shared base yet and are listed in §4.

## 1. What already exists and is reused unchanged

| Need | Where |
| --- | --- |
| Key names, shard parsing | `bcs_dist_store::layout::KeyLayout` (`spec`, `ckpt`, `inbox`, `ev`, `task`, `admit`, `active`, `wake`) |
| Versions | `bcs_dist_store::version` (`CREATE`, `next`, bands) |
| Error mapping | `bcs_dist_store::error::{from_read, from_write, need_contract}` |
| Capability check | `bcs_dist_store::capability::require` |
| Stored shapes | `bcs_dist_store::codec` (`admit`, `inbox`, `wake`, `spec`, `task`; `SCHEMA_VERSION = 1`) |
| Admission | `bcs_dist_store::inbox::StoreInboxProducer` (order, decision 24, D1 stub) |
| Test store | `bcs_test_support::store_model::Model`; `bcs-dist-store/tests/common/mod.rs` `Recorder` (lost / failed n-th write) |

There is no in-memory provider of the distributed ports (task S2) in `dev`.
Gateway tests therefore run the real providers over `Model`, plus a small
"fake Worker" test helper that writes `ev` and `task` the way the Worker will.

## 2. Operation mapping

| Gateway operation | Design command | Trait call |
| --- | --- | --- |
| admission (all steps) | see Redis page §5 | existing `StoreInboxProducer::admit` |
| task index add (new) | `ZADD tasks:<sid> NX <created_at> <tid>` | `ZSetStorePlugin::add(tasks, [entry], ZAddMode::InsertOnly)` |
| read spec | `JSON.GET spec` | `JsonStorePlugin::get_json(spec)` → `codec::decode_json::<SessionSpec>` |
| read task snapshot | `JSON.GET task` | `get_json(task)` → `decode_json::<TaskSnapshot>` |
| catch-up after a position | `XRANGE ev (<id> +` | `StreamStorePlugin::range(ev, Excluded(id), Unbounded / Included(P), limit, false)` |
| live read of a session | `XREAD BLOCK 1000 STREAMS ev:<sid> <last>` | `read(&[StreamRead { key, after }], ReadOptions { max_entries, block: Some(1 s) })`, **one key per call** (decision 23) |
| list a session's tasks | `ZRANGE tasks:<sid> BYSCORE LIMIT` | `ZSetStorePlugin::range(tasks, ZSetRange { min, max, after, limit })` (keyset paging) |
| finished task after expiry | — | `ResultStorePort::get_final` (injected port, no store call of ours) |
| large content | — | `BlobStorePort::get` (injected port) |

Capabilities required at startup (fail fast through `capability::require`):
`Kv`, `AtomicJson`, `ZSet`, `Stream`. Not required: `StreamGroups`,
`StreamRecovery`, `VersionedZSet`, any versioned KV.

## 3. Behaviour of the trait that shapes the design

- **`read` with `after: None` reads from the oldest kept entry**, never "from
  now". A subscriber with no coverage yet (task just submitted,
  `snapshot_event_id = None`) therefore starts at the beginning of the
  session's `ev` and skips other tasks' entries by `tid`. This is correct and
  needs no "latest id before admission" read; its cost is bounded by how much
  of earlier tasks the Worker has not trimmed yet.
- **Basic `Stream` cannot report trimming** (`StreamPage.recovery` is `None`
  without `StreamRecovery`). We don't need it: with the agreed trim rule a live
  task's position at or after its `snapshot_event_id` is always readable, and
  an older one is treated as a gap. A finished task's entries may be trimmed at
  any time, so a resume on a finished task is always answered snapshot-first.
- **`max_entries` is a total across keys**; with one key per call it is the
  per-read batch size.
- **A blocking read holds a provider resource** until it returns. One per
  served session per Gateway process; the limit is TBase question 8 and is not
  designed around here. The reader stops when its last subscriber leaves.
- **Ids are two numbers**: `StreamId { major, minor }` ↔ `EventId { commit,
  index }` (`major = commit_seq`, `minor = n`). `minor` must be 1..=u32::MAX; an
  `ev` entry that breaks this is skipped with a warning.
- **Reads never write**, so a read error is always `NotApplied` (`from_read`),
  never `OutcomeUnknown`.
- **`ZAddMode::InsertOnly` is idempotent**: an admission retry repeats the
  index write without changing the first score.
- **`ZSetRange.after`** is the last entry of the previous page of the same
  query; the page token of `list` encodes exactly that `(score, member)`.

## 4. Not in the shared base yet (changes to `bcs-dist-store`)

| # | Change | Why | Affects |
| --- | --- | --- | --- |
| 1 | `KeyLayout::tasks(sid)` → `…:tasks:<sid>` | per-session task index for `tasks/list` | Gateway only writes and reads it |
| 2 | Admission writes the index right after the task creation | approved in the brief | shared admission provider; Connector admissions never create a task, so their write sequence is unchanged |
| 3 | `codec::ev`: stored shape of one `ev` entry | the Gateway decodes what the Worker writes; no codec exists | **Worker (executor team) must write this shape** |
| 4 | `AdmitOutcome::Duplicate` removed (decision 24) | approved in the brief | Connector call sites that match `Duplicate` |

Item 3 is the only one not already approved. The Notion pages sketch flat
fields (`type`, `state`, `text_ref`, …) and disagree with each other; the merged
model type is `OutputEvent { id, tid, body: OutputBody }`, and merged code wins
for shapes. The proposal follows the existing `inbox` codec style:

```text
XADD …:ev:s0042.7f3a9c 12-1  v 1  tid s0042.7f3a9c.b21e  kind status  body '{"kind":"status","status":"working","interactions":[]}'
```

`v` = schema version, `tid` and `kind` flat so entries can be inspected and
filtered without parsing, `body` = JSON of `OutputBody`. The entry id is the
`EventId`. An entry with an unsupported `v` or an undecodable body advances the
position, is not forwarded, and is logged.

## 5. Outside the trait (not designed around)

- How many blocking reads one client may hold (TBase question 8).
- Ordering across connections (part of delayed D1).
- Layout and retention of final records (`ResultStorePort` provider).
