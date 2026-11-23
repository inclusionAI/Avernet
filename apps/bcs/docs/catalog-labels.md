# Configured catalog labels

`catalog_labels` is platform management metadata, separate from raw `agent_card`
and Group `card_template`. It is a flat object of string keys to string lists.
Labels do not confer authorization, infer status/strategy, or alter discovery
eligibility. No lifecycle is assigned implicitly.

## Policy and bootstrap

Distribute an independent regular `catalog-labels.toml` beside the selected BCS
config file(s), **before** starting the new binary:

```toml
[labels.lifecycle]
allowed_values = ["draft", "trial", "running", "offline"]
allow_multiple = false
```

The policy is loaded once, without environment overlays or `${VAR}` expansion,
and injected into Agent, scoped registration and Group application assembly.
Every key requires a finite unique nonblank `allowed_values` list and an explicit
`allow_multiple` boolean. Keys use ASCII letters/digits/underscore/hyphen;
`visibility` and `user_visibility` are reserved. Missing/malformed files fail
configuration loading. `[labels]` is a valid empty dictionary accepting only empty
objects. Configuration changes require a controlled restart. Do not shrink a
dictionary without planning for retained data: reads/publication do not silently
filter persisted labels through a newly smaller dictionary.

## Request contract

Canonical Agent registration, named/Provider/scoped registration and explicit
Agent update accept a top-level field; Group create/PATCH and legacy/browser
aliases use the same normalization. For example:

```json
{"catalog_labels":{"lifecycle":["running"]}}
```

Reject outer null, non-object values, unknown keys (including deletion keys),
unknown/case-modified values, non-array/non-null values and non-string array
entries. Preserve exact strings; deduplicate equal values in first-occurrence
order, then enforce distinct multiplicity. Known-key null/[] deletes that key.
The supplied object replaces the **whole** collection, not individual keys.
Invalid combined writes persist neither metadata nor Card and emit no committed
catalog-change notification. Existing authorization and response envelopes apply.

Provider authentication/name resolution is read-only. Public and named registration,
updates and refreshes validate the Card's persistence projection before reserving
the Provider slug; rejected label/owner/reference/target/Card inputs must not pin a
previously mutable name. Successful get/find results reserve the name only after
validating their target/view; an empty find does not reserve it. A valid update
reserves once before its first CAS commit, and a concurrent slug change is still
fenced. Later transaction/CAS/storage failures do not roll back the separate
reservation; no broader all-failure atomicity is implied.

Legacy Group create/PATCH, Provider Card configuration/registration and named
registration return structured HTTP 400 for label deserialization/type errors,
with `params.code = invalid_request`, a precise `params.field` (including a list
index when applicable), and `params.reason = INVALID_TYPE`. Their unrelated
parsing errors retain the existing status/envelope behavior. Group create policy
violations also retain their structured field and reason.

The Gateway-published contract includes the complete generated optional register
request body, every Group label property and the required membership of every
Group detail response variant. Request labels remain optional. Consistency tests
compare all generated label locations and their parent required semantics, not
only a Group-filtered property fragment. Selective generated publication leaves
unrelated historical schema differences untouched and passes the regular
compatibility checker.

| Operation | Omitted | Explicit `{}` | Nonempty normalized object |
| --- | --- | --- | --- |
| Agent registration | initialize empty | empty | replace |
| Agent otherwise-valid explicit update | **preserve** | clear | replace |
| Agent label-only update | no operation; existing UPDATE_REQUIRED | valid clear | replace |
| Group creation | initialize new resource empty; preserve on reuse | empty | replace |
| Group PATCH | **preserve** | clear | replace |
| URL/manual/background refresh | **preserve** | no parameter | no parameter |
| Internal Group/template maintenance | **preserve** | no implicit clear | no implicit replace |

Normal manager-worker/state-machine Groups may carry labels while private and
later publish them on a public transition. Chat/DM accept omitted/normalized-empty
labels but reject nonempty labels; this does not add Card support or Triple APIs.
Management views return `{}`, not null/omission, for empty/legacy labels. Group
list summaries do not gain a label field or per-result label lookup.

## Detail response consistency

Versioned Group PATCH carries metadata and labels captured by its own commit.
GET and create details for ManagerWorker/StateMachine Groups instead use an
uncached `read_detail_snapshot`: metadata and participants are read first, then
the indexed Group/Card read fences their version and supplies labels. A version
change retries the complete local read at most three times; exhaustion is a
Conflict, and storage/JSON failures propagate. No transaction or lock spans
Bot-profile display reads or StateMachine runtime calls. The create response
may reflect a later successfully committed Group version, but must not combine
that version's labels with an earlier name/context. Legacy Group details and
label/status/visibility response projections use the same coherent read.
Participant-only V1 responses contain no labels; a subsequent detail GET uses
the strict snapshot. Chat/DM details remain empty-labeled, and list summaries
retain their existing lightweight path.

GET authorizes the fresh metadata it will return, rather than a cached public
visibility that another instance has changed to private. Display profiles,
Session outputs and StateMachine configuration remain separately projected;
this is not a promise of general multi-service snapshot isolation. Custom Core
and repository implementations must implement this additive read capability;
the default fails closed instead of falling back to stale cached metadata.

SQL cost is three reads per attempt: one Group row, the existing batched
participant query, and one indexed Group/Card row (at most nine reads under
continuous version churn). There is no per-participant label query, new cache,
new write, migration or schema change. An absent Group returns after one read.
The V1 create flow can project both its legacy management result and final V1
result; each label-bearing projection uses its own three-read snapshot (six
normal snapshot reads in that flow), in addition to existing orchestration and
authorization reads. Memory reads hold the existing groups-before-labels guards together. Tests cover
Memory/SQLite conformance, 128 participants without N+1, stale-cache bypass,
bounded churn, storage failure, and deterministic two-facade response races.

## Persistence, concurrency and publication

Owning columns: `bcs_bots.catalog_labels` and
`bcs_group_agent_cards.catalog_labels`; not `bot_info`, `bcs_groups`, Card JSON or
A2A runtime tables. New migrations: MySQL/OceanBase **048**, SQLite **046**.
Both columns are nullable JSON/TEXT for old rows and old inserts. New writes encode
normalized `{}` deterministically. SQL NULL/missing historical Group Card rows
read empty; malformed non-null JSON is an error. Explicit eligible Group label
writes materialize a missing template in the Group transaction; reads do not
backfill.

Legacy normal-Group creation is create-only, including caller-supplied IDs. An
existing identity returns conflict without replacing its management anchors,
participants, immutable kind/strategy, version or labels. This is enforced by the
SQL unique constraint in the creation transaction and by the memory store's write
lock, not an out-of-transaction exists check. Use authorized PATCH to modify an
existing Group. DM pair-based reuse and trusted internal metadata upserts retain
their separate contracts; creation is not an authorization bypass for either.

Versioned Group writes return their already validated, version-fenced committed
candidate after the transaction, leaving the cache invalidated. No fallible
response read is performed between COMMIT and the Core catalog notification.
Transaction failures still propagate and produce no catalog notification. This
moves the normal Group/participant reads before commit without increasing their
count on a warm Core path; it is not a durable outbox or a
process-crash recovery guarantee.

Agent writes use the existing identity/revision CAS and transaction; label-only
updates advance the same revision. Omitted update labels are taken from the latest
record on each local CAS attempt, not the initial/pre-fetch snapshot. Provider-ID
legacy updates without an expected revision retry at most three times; a supplied
revision never retries. URL sources are fetched once outside the retry loop.
Each preserve attempt adds one bounded Card read and binding/authorization checks;
there are no per-label queries or extra outbound fetches. Superseded refreshes fail rather than replay
stale metadata. Group writes explicitly preserve or replace labels in the
existing Group/event transaction; template display upserts never overwrite them.
Invalidation/catalog notification occurs only after commit. The joined Group
snapshot still reads Group and Card in one query. Labels are read/written as one
serialized value, not per-key queries. Detail projections can request one fixed
snapshot read; list summaries are unchanged. No database lock covers outbound
Card fetch or Fuse delivery.

Each Fuse V2 write sends all persisted custom labels plus current system labels:
Agents retain `visibility` and `user_visibility`; Groups retain only their
existing `visibility`. System labels cannot be overridden. Clearing emits a full
system-only map, not tombstones/empty-list patches. The queue stores identities
and re-reads current state on every retry. Downstream full-replacement/stale-key
removal is externally owned and is **not** proven by BCS payload tests.

## Compatibility and rollout

Service API **7.0.0** adds required Rust struct fields; external literals and
consumers rebuild together. `UpdateAgentCommand`/`A2aBotManagementService::update_agent`
carry source/visibility/label presence. Legacy update methods remain and preserve
labels on omission. New unsupported trait methods refuse explicit label data;
there is no default that drops nonempty data. `upsert_with_catalog_labels` is the
Group Core/repo preserve/replace boundary. No Java or Group Triple API is added.

Old Agent requests handled by the new server preserve labels on omission,
including source/visibility-only updates and local CAS retries. New writers are rejected by old closed REST DTOs; strict old response
readers can reject the new field. Expand schema and provision the file, roll all
server replicas, verify nonempty readback, then enable label-aware clients.
Standalone public source availability is not an embedded host upgrade. OCB must
adapt source/assembly, pin an independently published verified public revision,
and pass its own integration gates.

Rollback disables new writers first and retains columns, values and migration
history. Old binary reads/upserts/runner compatibility and old publication loss
must be assessed separately; do not run destructive down migrations. Real
MySQL/OceanBase and deployed rollout remain separate acceptance gates. See the
[implementation validation record](../specs/2026-11-19-configured-catalog-labels/validation.md)
for actual commands/results and remaining NOT_RUN/NOT_VERIFIED items.
