# Research: mapping the Gateway storage design onto `bcs-store-api`

Status: evidence for [spec](spec.md) and [plan](plan.md). Written before any
provider code. "Decision n" refers to the project's design decision log, which
is kept outside this repository; the merged code in `dev` wins for type shapes.

Sources read in `dev` (`7d644824`):

- `crates/plugin-api/bcs-store-api/` (README, `src/`), and its conformance suite
  `bcs-test-support::contract::plugin::store` with the private model under
  `crates/test-support/bcs-test-support/tests/support/store/`.
- `crates/contracts/bcs-dist-model/`, `bcs-service-api` `application/dist_gateway.rs`,
  `core/dist_gateway.rs`, `port/repo/distributed/` (README first), `port/dist_agent.rs`.
- `scripts/ci/check-store-boundaries.sh` (R10), `check-deps.sh`, `check-import-rules.sh`.
- `specs/2026-11-15-distributed-contracts/tasks.md` (G1–G6, S1–S5).

## 1. Result

Every operation the Gateway needs is expressible through the five store traits,
with no raw command and no client-side CAS. Seven points are missing or behave
differently from the design notes; they are listed in §4 and each has a proposed
handling in the plan. None blocks the first PR (shared layout + admission).

Task S2 (an in-memory protocol store, `crates/services/bcs-dist-store/src/memory/`)
does **not** exist in `dev`. The only in-memory implementation of `bcs-store-api`
is the private conformance `Model`; it is deliberately not exported (§4.4).

## 2. Keys → store calls

`…` = `bcn:v1:<cluster>:<namespace>:<shard>`, e.g. `bcn:v1:c01:main:s0042`. No
hash tag (decision 31): key builders reject `{`, `}`, `:` and whitespace in ids.

| Step / use | Key | Store call | Notes |
| --- | --- | --- | --- |
| Admission 1: already admitted? | `…:admit:<input_id>` | `kv.get` | same digest = accepted, different = conflict (decision 24) |
| New session | `…:spec:<sid>` | `json.mutate_json(Newer(Version(1)), Replace(spec), Persistent)` | `ConditionNotMet` → `get_json`, compare scope and group; the stored frozen spec wins |
| Admission 2: new task | `…:task:<tid>` | `json.mutate_json(Newer(Version(1)), Replace(task), Persistent)` | `ConditionNotMet` = already exists: never replace a task the Worker advanced |
| Admission 3 | `…:inbox:<sid>` | `stream.append(Auto, fields)` | never retried automatically; `OutcomeUnknown` is returned to the caller |
| Admission 4: listener read | `…:ckpt:<sid>` | `json.get_paths(["/listen","/owner/epoch","/status"])` | **stubbed** (delayed contract D1): returns `NotApplied { code: "need_contract_not_implemented" }` |
| Admission 5 | `…:active` | `zset.add([{member: sid, score: now_ms}], ZAddMode::Lower)` | never `Upsert` |
| Admission 6 | `…:wake` | `stream.append(Auto, {sid})` | only without a valid listener; until D1 that is always |
| Admission 7: success point | `…:admit:<input_id>` | `kv.put_if_absent(record, Persistent)` | `false` → `kv.get`, compare digest |
| Intermediate agent event | `…:inbox:<sid>` | `stream.append(Auto, fields)`, then steps 4–6 | no `admit`, no `task` read |
| Frozen spec read | `…:spec:<sid>` | `json.get_json` | `SessionStorePort::get_spec` |
| Task snapshot | `…:task:<tid>` | `json.get_json` / `json.get_paths` | `restore`, resume validation, reply/cancel checks |
| Live output | `…:ev:<sid>` | `stream.read(&[one key], {max_entries, block: 1 s})` | one key per blocking call (decision 23) |
| Catch-up | `…:ev:<sid>` | `stream.range(Excluded(pos), Included(reader_pos), limit, false)` | paged |

Versions ("ts") are built only by helpers, never by hand:

| Helper | Value | Used by |
| --- | --- | --- |
| `CREATE` | `1` | Gateway (`spec`, `task`), Worker (`disp`) |
| `commit(epoch, commit_seq)` | `epoch × 10^9 + commit_seq`, `commit_seq < 10^9`, overflow checked | Worker (`ckpt`, `task`) |
| `seal(epoch)` | `epoch × 10^9` | Worker |
| `claim(new_holder_epoch)` | `new_holder_epoch × 10^9` | Connector (`disp`) |
| `next(read)` | `read + 1`, must stay inside the same `10^9` band | Worker, Connector (`disp`) |

A rejected write returns `ConditionNotMet` without the current version, so every
retry re-reads (`get_json` / `get_paths`), re-decides and only then writes again.

## 3. Confirmed behaviour (from the trait docs and the conformance model)

- `WriteCondition::Newer(v)`: applies only if `v` is above the key's whole
  version history; `v` becomes the version; equal is rejected.
- `JsonMutation::Patch` compares and advances the whole document's version;
  pointers are RFC 6901 (`/owner/epoch`).
- `put_if_absent` is `true` only for this call's creation. Success of an
  admission is decided by digest comparison, never by `created == true` alone.
- `append(Explicit(id))` at or below the head is `IdNotIncreasing`, which is not
  "already applied".
- `read(after: None)` starts at the oldest retained entry, never at the tail.
  Dropping a plain blocking read is safe (it advances no cursor).
- Every error except `OutcomeUnknown` means this attempt changed nothing.
- Capabilities needed by the Gateway: `Kv`, `AtomicJson`, `ZSet`, `Stream`.
  They are checked once at construction; a missing one fails construction.

## 4. Missing or different from the design notes

### 4.1 `Absent` is unsafe for `task`; use `Newer(Version(1))`

With `Absent` the store picks the first version. After a finished task's key
expires (7 days), `Absent` would succeed again and pick a version above the
retained history, i.e. above `epoch × 10^9 + commit_seq`. That recreates a
SUBMITTED task under a finished id and lands in the Worker's version band.
`Newer(Version(1))` gives exactly ts 1 on first creation and is rejected forever
after, including after expiry. The same condition is used for `spec`.

### 4.2 No append-with-trim (`XADD wake MAXLEN ~ 100000`)

`append` has no trim argument; `trim(key, MaxLen(n))` is a separate, exact call.
Until D1 every admission writes `wake`, so trimming on every append would add
one round trip per input. Proposal: the writer trims on a sample (about one in
128 wake appends, `MaxLen(100_000)`); a failed trim is ignored because `wake` is
a hint. Alternative: the Worker trims what it has read. See plan Q5.

### 4.3 Live `ev` reads cannot see a trim without evidence

The rule "a position at or after `snapshot_event_id` is resumable" is enough to
validate a resume point with one `task` read. It does not cover the shared live
reader: the Worker may write entries, publish a snapshot and trim before the
reader's next read returns, and a basic `StreamPage` (`recovery: None`) cannot
show that entries were removed. Ids are sparse, so contiguity cannot be checked
either. Exact detection needs one of:

1. the optional `StreamRecovery` capability (`removed_through` in every page), or
2. a `prev` field on each `ev` entry (the id of the previous entry of the
   session); the merged `AppendCommittedEvents.previous` already carries it.

Proposal: use `page.recovery` when the provider returns it, use `prev` when the
entry carries it, and otherwise fall back to the conservative snapshot-id check
after a catch-up read. Ask the executor side to write `prev`. See plan Q3.

### 4.4 No exported in-memory store

`Model` passes the conformance suite but lives under `bcs-test-support/tests/`
and the README states it is not exported. Provider tests need it. Proposal:
include it from the store crate's integration tests with `#[path]` (no copy, no
edit of the PR #4070 files). Alternative: export it from `bcs-test-support`
behind a module, with the PR #4070 author's agreement. See plan Q7.

### 4.5 `TaskReaderPort::list` has nothing to enumerate

No key lists the tasks of a session or of a principal, the store API has no scan,
and `ResultStorePort` has only `save_final` / `get_final`. `list` therefore needs
an index that the key catalogue does not have. Proposal: `…:tasks:<sid>` (sorted
set, member `tid`, score `created_at`), added by admission right after the `task`
creation with `ZAddMode::InsertOnly`; `list` requires a session filter in the
MVP. Alternative: `list` is a "Need contract, not implemented yet" stub until the
result-store layout exists. See plan Q1.

### 4.6 `SessionStorePort` is one trait

The Gateway needs only `get_spec`, but a Rust provider must implement all six
methods. Proposal: the spec read is a free function over `JsonStorePlugin` that
the Worker's full provider can reuse, plus a read-only `StoreSpecReader` whose
Worker-owned methods return `NotApplied { code: "not_supported_by_spec_reader" }`.

### 4.7 Stored shapes are a cross-team wire format

The Gateway writes `spec`, the initial `task`, `inbox`, `wake`, `admit`, and
reads `task` and `ev` written by the Worker. The merged types win for shapes, so
the proposal is:

| Key | Stored form |
| --- | --- |
| `spec` | `serde_json` of `SessionSpec` |
| `task` | `serde_json` of `TaskSnapshot` |
| `admit` | `{"digest","sid","tid"}` |
| `inbox` entry | fields `v`, `kind`, `input_id`, `tid`, `digest`, `body` (= JSON of `InputBody`) |
| `wake` entry | field `sid` |
| `ev` entry | id `(commit, index)`; fields `v`, `tid`, `kind`, `event` (= JSON of `OutputEvent`), optional `prev` |

`kind`, `input_id` and `tid` are duplicated as flat fields for inspection; the
decoder rebuilds `InputEnvelope` from `body`, `digest`, the flat ids and the key.
The Worker needs `digest` to write a missing `admit` itself. Encoders and
decoders live in one module so both sides share them. Needs executor-side
sign-off. See plan Q2.

## 5. Outside the trait (not designed around)

- How many blocking reads a client may hold (one per served session).
- Ordering across connections, part of delayed contract D1.
- Durability and failover of a successful write: a provider guarantee, proven by
  running the conformance suite and failover tests against the real provider.

## 6. Consequences worth knowing

- A retried, unfinished admission appends a second `inbox` entry with the same
  `input_id`. The Worker's consumer must drop physical repeats by `input_id`.
- With the D1 stub, `append_stream` writes `inbox`, `active` and `wake` for every
  intermediate agent event (three round trips instead of one).
- A new task has no `snapshot_event_id`, so its first subscriber starts at the
  beginning of the session's retained `ev` and skips other tasks' entries. Tasks
  of a session run serially and finished tasks are trimmed, so this is bounded
  by the retained tail of the previous task.

## 7. Layering checks that constrain the layout

| Rule | Effect |
| --- | --- |
| R10 (`check-store-boundaries.sh`) | `bcs_store_api` only in `crates/services/*-store` |
| LINT-1 (`check-import-rules.sh`) | `crates/services/**` cannot import `application`; `crates/adapters/**` cannot import `core` or `port` |
| DEP-6 (`check-deps.sh`) | `bcs-store-api` and `bcs-dist-model` depend on no `bcs-*` crate |
| 1,000-line file limit | modules are split by responsibility from the start |
