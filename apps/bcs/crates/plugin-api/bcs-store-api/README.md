# BCS Store API

Status: public contract definitions and reusable test harnesses. No production
provider, service repository, transport client or runtime wiring is included.

This Plugin API defines **storage capabilities**, not a Redis command wrapper.
Implementations can use native commands or an equally strong server-side
mechanism. A native command existing does not prove that the configured deployment
satisfies the contract. Unsupported guarantees must fail explicitly.

## Boundary

```text
Gateway / Worker / Connector
  -> bcs-service-api repository ports
  -> service-owned store implementation
  -> bcs-store-api
  -> configured storage plugin
```

Service stores own business keys, serialization, admission, ownership validation,
checkpoint sequencing, outbox replay, snapshot coverage and recovery. Providers
own storage atomicity, version metadata, connection management and error mapping.
Do not put Session/Task/Checkpoint types, raw backend commands or SDK response
types in this crate. Existing business models gain no additional revision field.

`bcs-cache-api` remains a cache contract with different guarantees;
`bcs-storage-api` remains object/file storage; `bcs-db-api` remains SQL execution.
A future SQL or different-queue repository can implement the existing business
ports directly: it need not emulate these data structures.

## Capabilities and operation traits

`StorePlugin::supports` declares support for this configured instance. Four
operation traits are independently injectable; no all-capabilities adapter,
factory registry or business wrapper is required.

| Capability | Trait and surface | Guarantee |
| --- | --- | --- |
| Kv | `KvStorePlugin`: get, mget, set, put_if_absent, delete, expire, ttl | Byte values; atomic single-key writes/NX; explicit expiry |
| ConditionalKv | Same trait: create_versioned, get_versioned, compare_and_set | Persistent versioned creation/read and atomic exact-version update within a live key incarnation |
| MonotonicKv | Same trait: set_if_newer, delete_if_version; stronger history for all KV mutations | Caller-assigned versions and retained history across expiration/deletion/recreation |
| AtomicJson | `JsonStorePlugin`: get_json, get_paths, mutate_json | Whole-key condition, ordered path edits/replacement/deletion, version and expiry commit together |
| ZSet | `ZSetStorePlugin`: add, remove, range | Atomic batches; bounded ordered score/member queries |
| VersionedZSet | Same trait: snapshot, update_if_version | One independent version for the entire collection; atomic check/mutate/advance |
| Stream | `StreamStorePlugin`: append, read, range, trim | Ordered append/read and exact trimming; IDs keep advancing after trim-to-empty |
| StreamRecovery | Same read/range/trim methods | Atomic entries/head/removed-prefix evidence and atomic trim publication |
| StreamGroups | Same trait: create_group, read_group, ack, claim_idle | Pending delivery tracking, acknowledgement and bounded idle reclamation |

ConditionalKv requires Kv; MonotonicKv requires ConditionalKv. VersionedZSet
requires ZSet. StreamGroups and StreamRecovery independently require Stream. All methods for an unsupported capability return `Unsupported` before
mutation. There are no best-effort conditional defaults. Consumers check their
required capabilities at assembly/startup, not after accepting durable work.

## Writes and versions

An ordinary single-key set is atomic too. Conditional operations additionally
make **checking the condition and applying the write one atomic action**.

- `put_if_absent`: create only if missing/expired; no version supplied.
- `create_versioned`: create a persistent versioned key only if absent.
- `compare_and_set`: current persistent live version must equal the observed
  version. The provider atomically chooses a strictly higher next version.
  These basic methods intentionally have no TTL parameter. Do not mix ordinary
  SET/expiry with basic versioned keys; a provider may use different native value
  types. A lease requiring conditional TTL renewal needs a proven separate contract.
- `set_if_newer` (MonotonicKv): supplied version must exceed retained history; that supplied
  version becomes the new version. Equal versions are rejected, even for equal data.
- JSON uses `WriteCondition` to select these same rules. `Any` is unconditional;
  it still advances version. Patches compare and advance the entire document.
- ZSet CAS uses an exact collection version. `None` expects no history, not an
  empty collection; an emptied collection retains its version.

Versions are storage metadata, not time, score or necessarily a business epoch.
A provider may encode business versions only when the complete protocol proves
the required checks. A greater timestamp alone does not prove correct owner,
expected business sequence or matching commit content.

When a provider advertises MonotonicKv, AtomicJson or VersionedZSet, **every mutation through all
its entry points participates in the same version history**, including ordinary
set/add/remove, expiry changes, and writes of identical content. Versions survive
expiration/removal/recreation so an old token cannot become valid again (ABA).
A backend that cannot preserve this history cannot advertise the capability.
Version zero and overflow are errors; there is no wraparound. Basic ConditionalKv
only guarantees advancement during a live incarnation and may reuse a version
after deletion/recreation. Its callers must prevent key reuse while stale writers
can act, or require MonotonicKv; an integer token alone is not a lifetime fence.

`ConditionNotMet` means this attempt changed nothing. It is not `AlreadyApplied`.
After uncertainty, service stores compare stable operation identity AND content
against durable facts. This API does not create or validate business commit proofs.

## JSON atomicity

JSON paths are RFC 6901 pointers, not a vendor query language. Projection preserves
request order and distinguishes a missing path from JSON null. Values and version
come from one consistent read. Root replacement is explicit; patches contain
nonempty ordered Set/Remove edits and existing parents. Array insertion, wildcard
updates and arbitrary expressions are outside this contract.

For a patch, all paths, the whole-key version condition and expiry are handled in
one atomic operation. If the second edit fails, the first must not remain visible.
A path-only ownership update invalidates an old whole-document CAS. Replacement
and patch are single-document operations, not transactions across documents.

## Sorted sets and scheduling

Score and version are independent. Scores accept positive/negative infinity,
reject NaN, and sort ascending with member bytes breaking ties. Queries use a
bounded `(score, member)` continuation rather than a mutable offset. Individual
pages are consistent; changing scores during a multi-page enumeration requires
service-level reconciliation. This is not a snapshot iterator.

A versioned mutation validates the entire batch, checks the collection version,
changes all requested members and advances the version atomically. An accepted
same-score update still advances it. Ordinary no-op add/remove also invalidates
older observations. Members share one version: concurrent unrelated members may
conflict. Retry requires re-reading/re-deciding, not replaying an old decision with
a newer version. Empty collections retain history; deleting a member cannot erase
newer activation. Scheduling ownership/fencing metadata and its coordination with
these mutations remain a responsibility of the service-store protocol; collection
CAS alone does not establish who owns a session.

## Streams and consumer groups

`StreamId` is an ordered pair of integers, independent of wall time. Appends accept
auto-generated or explicit IDs; IDs must advance even after trim-to-empty. Rejected
older/equal IDs are `IdNotIncreasing`, not success. Providers never retry an
uncertain auto append with a fresh ID automatically.

`read` supports multiple keys and explicit per-key positions; None reads from the
earliest retained entry, never tail. The count is a **global** return limit across
keys. Return a page for every requested key, including empty pages. Return available
data promptly; a block duration is a maximum empty wait, not a batching delay.
There is no cross-key snapshot. Providers either support the requested key routing
or reject it before blocking; they do not silently omit keys. Stable ready streams
must be served fairly across repeated bounded reads.

With StreamRecovery, each page atomically pairs entries with the retained last
ID and highest removed prefix ID in `StreamPage.recovery`. Basic Stream returns
`recovery: None`: evidence is unavailable, not proof that nothing was trimmed. This metadata is required to distinguish trimming from an idle stream;
first remaining ID alone cannot prove a gap when IDs are sparse. With StreamRecovery, `trim` atomically
publishes deletion evidence. All streams retain ID ordering after trim-to-empty. `Before` is an exact lower
boundary; `MaxLen` is an exact length limit, suitable for disposable hints. Native
approximate trimming is not silently substituted. This optional metadata/atomicity may require backend extensions, just like
conditional JSON or versioned ZSets; its absence does not disable basic Stream.

Service stores verify snapshot/checkpoint coverage before trimming and coordinate
that check with concurrent work. Prefix evidence does not prove that an old effect
was executed; completion evidence and retention policy belong above this API.
No stream deletion/recreation or TTL eviction is exposed: cursor lifetime must not
silently restart. Namespace retirement is an explicit future protocol.

Groups deliver new records and register pending ownership atomically. Creating an
existing group never resets its cursor or pending list. ACK removes pending entries,
not log data; a missing group has zero entries to acknowledge and returns zero. Idle reclamation returns a bounded page and continuation; deleted
payloads are reported separately. Group ownership is not a business execution
fence and does not make remote calls exactly-once. Cancelling a group read may leave
pending delivery behind; it differs from cancelling a plain non-destructive read.

## Errors, durability and resource limits

`StoreError::OutcomeUnknown` is distinct from `NotApplied`, rejected conditions and
invalid input. A mutating method may return an ordinary error only if this attempt
definitely made no changes. Any partial/possibly delivered write, including group
pending-state mutation, needs OutcomeUnknown. Cancelling a write future cannot
retract a submitted request; callers must also treat that as uncertain.

Reads used for conditional protocols are current, per-key consistent reads; errors
are never missing values. Successful durable writes must survive the provider's
documented failure model. Providers document acknowledged-write durability,
replication/failover behavior, version retention, TTL precision and partition
restrictions. A deployment used for acknowledged admission/checkpoints must meet
the business port's durability requirements; the trait signature is not evidence
that a particular server does.

Providers publish configured limits for key/value/document sizes, patch/batch
sizes, stream fields, scan sizes, blocking keys/connections and timeout precision.
Over-limit mutations fail before changes; over-limit reads fail instead of dropping
records or advancing a hidden cursor. Multi-get may split into bounded partition
requests and is not atomic. Pipeline/batching reduces round trips, not transaction
boundaries. No unbounded scan or one-connection-per-session requirement is implied.

## Lifecycle and integration

This is a trusted in-process infrastructure boundary. Bootstrap constructs a
provider from validated configuration, verifies required capabilities and injects
operation traits into service stores. The API reads no environment variables and
selects no backend. Providers may access only their configured storage namespace;
there are no shell/raw-command/administrative escape hatches.

The composition root stops producers and drains/cancels operations before releasing
provider resources. Cancellation stops additional read work; an in-flight native call may drain
within documented native timeouts before releasing its bounded resource. Providers
keep blocking IO from exhausting the ordinary operation pool. Existing
configured handles are shared through Arc; this change adds no lifecycle registry.

## Verification and provider mounting

Shared harnesses live at
[`bcs-test-support::contract::plugin::store`](../../test-support/bcs-test-support/src/contract/plugin/store/mod.rs).
Use a fresh isolated namespace/prefix for each invocation. Tests that use multiple
streams require keys within the provider's supported routing scope. Call:

- `store_plugin_contract_tests` for capability declarations;
- `kv_store_plugin_contract_tests`, and `kv_conditional_contract_tests` when supported;
- `kv_monotonic_contract_tests` for MonotonicKv;
- `kv_expiry_contract_tests` for MonotonicKv with a clock/wait callback exceeding TTL rounding;
- `kv_unknown_outcome_contract_tests` for MonotonicKv with provider/proxy response-loss injection;
- `json_store_plugin_contract_tests`;
- `z_set_store_plugin_contract_tests` and `versioned_zset_contract_tests`;
- `stream_store_plugin_contract_tests`, `stream_group_contract_tests` and
  `stream_blocking_contract_tests`.

The private `conformance_store` test model executes these assertions and includes
intentionally broken CAS, JSON rollback, interleaved JSON/ZSet check-write and
same-score invalidation variants that
the harness must reject. It is not a production or exported in-memory plugin.
Type tests also check trait-object use, ID ordering and outcome distinctions.

From `apps/bcs`:

```sh
cargo test -p bcs-store-api
cargo test -p bcs-test-support --test conformance_store
cargo clippy -p bcs-store-api --all-targets -- -D warnings
cargo doc -p bcs-store-api --no-deps
```

Real providers must additionally test actual node failover/migration durability,
server-side atomicity under forced interleavings, request loss before/after commit,
TTL rounding, size boundaries, blocking pool cancellation/exhaustion, concurrent
trim/read consistency and representative hot-shard contention. Model tests do not
certify these operational guarantees. No production latency/throughput claim is made.

## Compatibility

This is an additive API; no existing service port, cache API or provider is changed.
The intended enterprise provider is `bcs-tbase-store` in the host repository; its
SDKs, credentials and command mapping remain outside Avernet. A Redis provider
may advertise only supported capabilities. Full CAS, versioned paths/collections
and trim metadata need explicit semantic verification, not command-name matching.

## Capability split compatibility

Before merging this contract, consumers must migrate CAS initialization to
`create_versioned` and remove expiry arguments from basic `compare_and_set`.
Require MonotonicKv for caller versions, conditional deletion or key-reuse safety.
Read `page.recovery`/`trim.recovery` explicitly; services requiring loss detection
must reject a provider without StreamRecovery instead of treating None as a clean
stream. Service-owned snapshot/checkpoint and retention protocols remain required.
