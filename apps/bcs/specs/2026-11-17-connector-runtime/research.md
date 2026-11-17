# Research: mapping the Connector onto `bcs-store-api` and the code in `dev`

Read on 2026-11-17 at `dev` = `5c3e30ea`. Sources: `crates/plugin-api/bcs-store-api`
(README, `src/`), its conformance suite and model in `bcs-test-support`
(`contract/plugin/store/`, `store_model`), `crates/services/bcs-dist-store`,
the merged contracts, and the Notion pages listed in [spec](spec.md).

## 1. Verdict

Everything the Connector needs is in the trait and behaves as the design
assumes. Nothing is missing, so no provider code is blocked. Three points
behave differently from the Redis page and are absorbed in our code (§5).

## 2. Keys (all from `bcs_dist_store::layout::KeyLayout`; none built here)

| Key | Example | Store type | Connector does |
| --- | --- | --- | --- |
| `disp` | `bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1` | JSON | read; claim; holder writes |
| command stream | `bcn:v1:cmd:c01:main:a2a:p0` | stream + group `connectors` | group read, ack, idle takeover |
| held index | `bcn:v1:held:c01:main:conn-03:1794370950000` | sorted set, score = claim time ms | add before claim, remove after ENDED |
| `inbox`, `active`, `wake`, `admit` | — | — | only through `StoreInboxProducer` (unchanged) |

## 3. Operations → trait calls

| Step | Design command | Call | Version / condition |
| --- | --- | --- | --- |
| create the group | `XGROUP CREATE … connectors 0 MKSTREAM` | `create_group(cmd, "connectors", GroupStart::Beginning)`; `AlreadyExists` is success | — |
| read hints | `XREADGROUP … >` | `read_group(cmd, "connectors", "<node>:<incarnation>", ReadOptions { max_entries, block })`, one key per call, one in-flight call per partition | — |
| take over stuck hints | `XAUTOCLAIM` | `claim_idle(cmd, "connectors", consumer, min_idle, start, limit)`, paged by `next`; `deleted` ids are dropped | — |
| ack | `XACK` | `ack(cmd, "connectors", &[id])` after the claim decision; a second ack returns 0 and is success | — |
| list as held | `ZADD held` | `add(held, [{did, now_ms}], ZAddMode::Upsert)` before the claim | — |
| read `disp` | `JSON.GETTS disp $` | `get_json(disp)` → `Versioned { value, version }` | — |
| claim | `JSON.SETTS disp … (epoch+1)×10^9` | `mutate_json(disp, Newer(version::claim(epoch + 1)), Patch([Set /execution, Set /connector]), Expiry::Preserve)` | greater wins, equal rejected: one winner |
| holder write | `JSON.SETTS disp … <read+1>` | `get_paths(disp, ["/control", "/execution", "/connector"])`, check holder is me, `mutate_json(disp, Newer(version::next(read)), Patch([Set /execution, Set /connector]), Preserve)` | compare-and-set |
| poll for reply / cancel | `JSON.GETTS disp …` | `get_paths(disp, ["/control", "/execution"])` every 1–2 s per held call | — |
| unlist | `ZREM held` | `remove(held, [did])` after the ENDED write (or when the claim found nothing to hold) | — |
| key event | admission order | `InboxProducerPort::admit` (`StoreInboxProducer`) | unchanged |
| intermediate event | `XADD inbox` | `InboxProducerPort::append_stream` | unchanged |

Capabilities required at construction (`capability::require`): `AtomicJson`
(`disp`), `ZSet` (held index), `Stream` + `StreamGroups` (command feed). `Kv`
is required by `StoreInboxProducer` itself. The in-memory `Model` supports all
of them.

## 4. Versions (all from `bcs_dist_store::version`; none built here)

- Worker creates `disp` at `version::CREATE` (1), band 0.
- Claim: `version::claim(HolderEpoch(e + 1))` where `e` = the larger of
  `execution.holder.epoch` and `version::band_of(read version)`. The band
  keeps the epoch even while `holder` is `null`.
- Every other write: `version::next(read)`; it refuses to leave the band, so a
  holder write can never look like a claim.
- At any moment `version = holder_epoch × 10^9 + k`.

## 5. Where the trait differs from the Redis page (absorbed)

1. `ConditionNotMet` carries no current version → re-read, re-check the
   holder, rebuild the edit from the fresh read, retry (bounded). Never retry
   with a bigger number.
2. Paths are JSON pointers (`/execution/phase`), not JSONPath.
3. A `Patch` needs existing parents. The Worker creates `disp` without
   `connector`, so the claim sets the whole `/connector` object (a `Set` on a
   root field creates it); later writes also replace the whole object.
4. `OutcomeUnknown` on a `disp` write is resolved by content: re-read; if the
   document shows my holder identity and the state I was writing, it applied;
   otherwise retry the step from the fresh read.
5. `read_group` cancelled mid-call may have registered pending entries. The
   feed therefore never drops an in-flight read: it keeps one read future per
   partition alive across `receive` calls.
6. No call removes a dead consumer; their pending entries are taken over by
   `claim_idle`. Not requested.
7. Outside the trait, not designed around: blocking-connection limits (TBase
   question 8) and ordering across connections (D1).

## 6. Stored `disp` layout

The merged `DispatchView` JSON (`spec`, `control`, `execution`) plus a
Connector-owned `connector` object. Ownership: Worker writes `spec` (creation)
and `control`; the holder writes `execution` and `connector`, always as whole
objects built from what it just read.

```json
{
  "spec": { "schema_version": 1, "did": "s0042.7f3a9c.b21e.n2.a1", "…": "…" },
  "control": { "cancel": null, "pending_input": [] },
  "execution": {
    "holder": { "instance": { "node": "conn-03", "incarnation": "1794370950000" }, "epoch": 1 },
    "phase": "accepted",
    "remote": { "task_id": "rt-77c2", "context_id": "ctx-91" },
    "confirmed_input_through": null,
    "cancellation": { "kind": "not_sent" },
    "outcome": null
  },
  "connector": {
    "v": 1,
    "claim_request": "conn-03:1794370950000:s0042.7f3a9c.b21e.n2.a1:1",
    "sending": null,
    "finish_evidence": null
  }
}
```

`connector.sending` records the operation a holder was about to send
(`{"kind":"start","request_id":…}` or `{"kind":"input","seq":2,"request_id":…}`),
which the merged `DispatchExecution` cannot express for an input sent while the
phase is `accepted`. Event `seq` is not stored: it restarts at 1 per claim.

## 7. Existing code reused

| Need | Reused | Notes |
| --- | --- | --- |
| key names, shard parsing | `layout::KeyLayout` | unchanged |
| versions | `version::{CREATE, claim, next, band_of}` | unchanged |
| error mapping, stub error | `error::{from_write, from_read, need_contract, NEED_CONTRACT}` | unchanged |
| capability check | `capability::require` | unchanged |
| digests | `identity::{digest_fields, digest_json, hex_of}` | unchanged |
| JSON codec + version check | `codec::{encode_json, decode_json}` | unchanged |
| reporting | `inbox::StoreInboxProducer` | unchanged; `Accepted` and `Duplicate` both success |
| in-memory store | `bcs_test_support::store_model::Model` | S2 (a `memory/` port model) does not exist in `dev`; tests use `Model` behind the providers and hand-written port fakes for the engine |
| failure injection | recording wrapper in `bcs-dist-store/tests/common/mod.rs` | copied pattern (test code), not moved |
| port harnesses | `bcs_test_support::contract::distributed::{dispatch, services}` | queue, feed, held, agent client, runtime control |

Shared-module change needed (the Gateway and the Worker depend on the crate):
one new module `bcs_dist_store::command` with the command-stream partition
count type, the `did → partition` function and the group name. Additive; no
existing module is edited.

## 8. Existing outbound A2A client and architecture rules

See [plan](plan.md) §6 and §9; filled from the code survey.
