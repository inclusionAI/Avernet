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

`StorePlugin::supports` declares support for this configured instance. The
operation traits are independently injectable; no all-capabilities adapter,
factory registry or business wrapper is required.

| Capability | Trait and surface | Guarantee |
| --- | --- | --- |
| DocumentCas | `DocumentStorePlugin`: read_document, read_document_paths, create_document, replace_document, patch_document | Persistent whole-document version; atomic create and exact-version replacement/ordered patch; consistent projections |
| StrictLease | `LeaseStorePlugin`: register_lease, read_lease, update_lease, retire_lease | Provider-timed expiry and version checks; stable termination prevents revival |
| Kv | `KvStorePlugin`: get, mget, set, put_if_absent, delete, expire, ttl | Byte values; atomic single-key writes/NX; explicit expiry |
| VersionedKv | `VersionedKvStorePlugin`: create_versioned, get_versioned, delete, ttl | Atomic creation, consistent value/version read, unconditional deletion and TTL query; Persistent expiry is always supported |
| ConditionalKv | Same trait: compare_and_set | Exact-version update of a live value; never recreate a missing/expired key |
| VersionedKvTtl | Same trait: expiry arguments and expire_if_version | All expiry modes; atomic condition/content/version/TTL update and conditional renewal |
| VersionedKvConditionalDelete | Same trait: delete_if_version | Atomic comparison and deletion; no cross-incarnation history implied |
| MonotonicKv | Same trait: set_if_newer; stronger history for all versioned mutations | Caller-assigned versions and retained history across expiration/deletion/recreation |
| AtomicJson | `JsonStorePlugin`: get_json, get_paths, mutate_json | Whole-key condition, ordered path edits/replacement/deletion, version and expiry commit together |
| ZSet | `ZSetStorePlugin`: add, remove, range | Atomic batches; bounded ordered score/member queries |
| VersionedZSet | Same trait: snapshot, update_if_version | One independent version for the entire collection; atomic check/mutate/advance |
| Stream | `StreamStorePlugin`: append, read, range, trim | Ordered append/read and exact trimming; IDs keep advancing after trim-to-empty |
| StreamRecovery | Same read/range/trim methods | Atomic entries/head/removed-prefix evidence and atomic trim publication |
| StreamGroups | Same trait: create_group, read_group, ack, claim_idle | Pending delivery tracking, acknowledgement and bounded idle reclamation |

Kv and VersionedKv are independent. ConditionalKv requires VersionedKv.
Lacking CAS does not disable versioned creation, reads, deletion or TTL queries.
VersionedKvTtl, VersionedKvConditionalDelete
and MonotonicKv each require ConditionalKv, not one another. VersionedZSet
requires ZSet. StreamGroups and StreamRecovery independently require Stream. All methods for an unsupported capability return `Unsupported` before
mutation. There are no best-effort conditional defaults. Consumers check their
required capabilities at assembly/startup, not after accepting durable work.

## Writes and versions

An ordinary single-key set is atomic too. Conditional operations additionally
make **checking the condition and applying the write one atomic action**.

- `put_if_absent`: create only if missing/expired; no version supplied.
- `create_versioned`: create a versioned key only if absent; never change an existing value or its TTL.
- `compare_and_set`: the live version must equal the observed version. Content,
  the strictly higher next version and requested expiry are committed together.
  Missing/expired values return ConditionNotMet, never recreation.
- Both methods accept `Expiry`. Without VersionedKvTtl, only Persistent is
  supported; Preserve/After fail Unsupported before I/O. With it, Preserve keeps
  the existing deadline (new keys are persistent), Persistent removes expiry and
  After sets a positive rounded-up duration. Failed conditions change no TTL.
- `expire_if_version` (VersionedKvTtl): atomically compare, keep content, change
  TTL and advance version. Missing/mismatch returns ConditionNotMet; zero TTL
  is invalid. Successful renewals invalidate old tokens even for identical TTL.
- `delete`: unconditional deletion, true only when this attempt removed live data.
  It is deliberately available on both traits and is not an ownership fence.
- `delete_if_version` (VersionedKvConditionalDelete): atomically compare and
  delete. Returns bool: false means absent/mismatched, not proof that a previous
  attempt succeeded. It does not return a new live-value version or promise ABA safety.
- `set_if_newer` (MonotonicKv): supplied version must exceed retained history;
  that version becomes the new version. Equal versions are rejected, even for
  equal data. Non-Persistent expiry also requires VersionedKvTtl.

Do not mix the two KV interfaces on the same logical key: native representations
may differ. Inject only the required trait and keep key namespaces disciplined;
the traits do not enforce physical isolation of arbitrary String keys. Both
traits can share a provider's connections and private DEL/PTTL implementation.

- JSON uses `WriteCondition` to select these same rules. `Any` is unconditional;
  it still advances version. Patches compare and advance the entire document.
- ZSet CAS uses an exact collection version. `None` expects no history, not an
  empty collection; an emptied collection retains its version.

Versions are storage metadata, not time, score or necessarily a business epoch.
A provider may encode business versions only when the complete protocol proves
the required checks. A greater timestamp alone does not prove correct owner,
expected business sequence or matching commit content.

With MonotonicKv, **every versioned mutation retains the same history**, including
unconditional deletion, expiry and identical writes. Ordinary KV is a separate
key discipline, not an alternate versioned write path. AtomicJson and VersionedZSet
likewise retain history across all their mutations, including ordinary ZSet
add/remove, expiry changes and identical content. Versions survive
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
- `kv_store_plugin_contract_tests` for ordinary KV;
- `kv_versioned_contract_tests` for VersionedKv, plus `kv_versioned_optional_contract_tests` for explicit refusals;
- `kv_conditional_contract_tests` for the strict ConditionalKv extension;
- `kv_versioned_ttl_contract_tests` and `kv_conditional_delete_contract_tests` for their independent extensions;
- `kv_monotonic_contract_tests` for MonotonicKv;
- `kv_expiry_contract_tests` for VersionedKvTtl with a clock/wait callback exceeding TTL rounding; retained-history assertions run only when MonotonicKv is advertised;
- `kv_unknown_outcome_contract_tests` for VersionedKv with provider/proxy response-loss injection;
- `json_store_plugin_contract_tests`;
- `document_store_contract_tests` for persistent create/read/replace, projection, patch and competing writes;
- `document_version_exhaustion_contract_tests` with a provider-seeded document at its final usable version;
- `document_unknown_outcome_contract_tests` with provider/proxy response-loss injection for create, replace and patch;
- `z_set_store_plugin_contract_tests` and `versioned_zset_contract_tests`;
- `stream_store_plugin_contract_tests`, `stream_group_contract_tests` and
  `stream_blocking_contract_tests`.

The `conformance_store` test uses `bcs_test_support::store_model::Model`, which executes these assertions and includes
intentionally broken CAS, stale conditional deletion, rejected TTL mutation,
lost KV history, JSON rollback, interleaved JSON/ZSet check-write and same-score invalidation variants that
the harness must reject. It is exported for tests of `*-store` crates only and is
not a production in-memory plugin.
Type tests also check trait-object use, ID ordering and outcome distinctions.

From `apps/bcs`:

```sh
cargo test -p bcs-store-api
cargo test -p bcs-test-support --test conformance_store
cargo test -p bcs-test-support --test coordination_primitives --test document_subset
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

Before merging this contract, move versioned implementations/callers from
KvStorePlugin to VersionedKvStorePlugin and pass an explicit Expiry to versioned
creation/CAS. Declare VersionedKv for base operations and ConditionalKv only for
strict CAS; absent CAS must not disable direct deletion. Use Persistent for providers without VersionedKvTtl. Conditional
deletion now returns bool and requires VersionedKvConditionalDelete, independently
of MonotonicKv. Require MonotonicKv for caller versions or key-reuse safety.
Unconditional delete/ttl have the same method names on both traits: inject a
narrow trait object, or use trait-qualified calls when a concrete provider
implements both. Shared harnesses must use the corresponding trait; ordinary
KV calls are no longer used to exercise versioned history.
Read `page.recovery`/`trim.recovery` explicitly; services requiring loss detection
must reject a provider without StreamRecovery instead of treating None as a clean
stream. Service-owned snapshot/checkpoint and retention protocols remain required.


## Persistent documents and strict leases

`DocumentStorePlugin` / `DocumentCas` is an independent, smaller contract than
`AtomicJson`: consistent whole-document reads and path projections,
create-if-absent at version 1, and exact-version replacement or ordered patch.
Every accepted update advances the version, including identical content.
It does not imply deletion, TTL editing, `Newer` or
retained version history after external deletion. All writers in its namespace
must use the same format and CAS discipline. Persistent coordination records
must not expire, be evicted, be deleted or be restored to an earlier version.
Business JSON remains ordinary JSON, including real null, removed fields and
metadata-like field names. Provider-private storage metadata is never exposed as
a reserved business field. Unsupported old formats fail rather than being
silently reinterpreted. The reusable suite is
`contract::plugin::store::document::document_store_contract_tests`.

`read_document_paths` shares `JsonProjection` and RFC 6901 semantics with
`AtomicJson`. Paths and version come from one document; a real null and a missing
path remain distinct. `patch_document` accepts nonempty ordered `JsonEdit`s and
compares the whole-document version. Request shape is validated before reading;
document-dependent errors are checked only after the condition matches. Parents
must exist, Remove requires a target, and array Set replaces an existing element
using a canonical nonnegative index. Root replacement uses `replace_document`.
Any failed edit leaves the complete document and version unchanged.

The portable defaults keep existing DocumentCas providers compatible without
requiring full AtomicJson or additional backend commands. Projection performs
one consistent full-document read. Patch performs at most one full-document read
and one atomic `replace_document` with the original expected version, computing
all edits before submitting the write. An intervening writer rejects the entire
candidate. These are bounds per call, with no retry loop; services own bounded
conflict retries and backpressure. Projection does not reduce bytes fetched,
and patch writes the full replacement under the provider's document-size limit.
An optimized native override must pass the same harness. Neither implementation
may turn an unknown replacement into a retry or infer AlreadyApplied from a
version mismatch. The default implementation rejects unsupported providers
before I/O. The model's path/patch methods use these same portable defaults;
forced interleavings and response-loss tests validate their CAS composition.

`LeaseStorePlugin` / `StrictLease` adds a separate provider-timed registration
lifetime. Expiry/version checks, optional deadline extension and payload changes
are atomic. A terminal lifetime cannot be revived by delayed registration or
renewal; retirement also fences a still-pending first registration. Missing is
not stable termination. Retrying registration preserves both its original
identity and deadline, even after its mutable payload has changed. A failed
response can be `OutcomeUnknown`; consumers resolve it without treating absence
as death. The test-only model uses one lock and a controllable clock. It is not a
production lease service and proves neither durability nor failover behavior.
Document CAS and ordinary TTL alone do not establish StrictLease support.

## Ordinary connection-pool observations

`StorePlugin::pool_observation` supplies current, body-free local resource facts
without storage IO. `None` means unavailable evidence; it is never an idle pool.
`Unpooled` is explicit for providers without a connection pool. `Bounded` reports
one consistent ordinary-pool capacity, checked-out count, waiter count and the
monotonic duration for which all slots are occupied or a caller is queued.
That congestion duration survives direct slot handoff to waiting callers and
resets only when neither condition holds. Connection setup and authentication
occupy the checked-out slot; cancellation releases queued and checked-out state.
Separate blocking-read pools do not contribute to the ordinary-pool snapshot.

Wrappers forward this metadata. The configured provider's resource kind and
capacity remain stable; a current snapshot that cannot be obtained returns
`None`. Consumers own pressure thresholds and business admission policy. This
observation neither authorizes writes nor changes their atomicity or uncertainty.
