# bcs-edge-permission-store Context

## Provides

- The SQL authority implementation (`DbBotAuthorityStore`) for the bot
  authority management edges (bot-authority spec §5/§6/§7): owner/manager
  edge reads and the registration/lifecycle composition steps, manager
  single-item edits and trusted full-set sync, trusted ownership sync
  (owner replace, previous owner fully removed), ownership transfer
  lifecycle (request/refresh/accept/reject/cancel with one pending row
  per `(env, bot_id, target_owner_actor_id)`), the paged reads behind
  the twelve `/api/v1/collaboration/` authority routes, and the
  `bot_authority_changes` permission-scope audit ledger.
- One transaction per authority change, serialized on the Bot row:
  bot lock → owner guard → count guards → Human-existence guards →
  exact diff writes → post-state guard. Bounded retry (≤3) only for
  deterministic local rollbacks; `Timeout` maps to the pinned
  `bot_authority_tx_outcome_uncertain` and is never retried. Audit
  writes stay ≤99 rows per statement. Owner normalization excludes the
  owner from every manager operand; no path fabricates rows.
- `DbEdgeGrantStore` for the runtime edge-grant domain (grants,
  revocation, friend dual-write reads), carrying the new
  `(from_id, to_id, env, grant_kind, grant_ref_id)` unique key and the
  one-approved-owner-per-Bot uniqueness from migration 033/034.
- A MySQL conformance surface for the authority contracts
  (`tests/authority_mysql.rs`, executed explicitly by CI with an
  injected fresh `BCS_TEST_MYSQL_URL`); SQLite runs the same harnesses
  by default.

## Consumes

- `bcs-service-api` port/repository contracts (`port::repo`) and domain
  types (`bcs-domain` edge_permission).
- DB plugin contracts (`bcs-db-api`); both SQL flavors through dialect
  helpers, never dialect-specific parsing in callers.

## Allowed dependencies

- `service-api/*`, `bcs-domain`, plugin API crates, utility crates.

## Forbidden dependencies

- `bootstrap/bcs`, `adapters/*`, transport/framework crates, and any
  raw environment/catalog access outside constructor parameters.

## Batch read cost

`roles_for` deduplicates requested (Human actor, Bot) pairs and reads those exact
pairs in chunks of 64 (at most 128 identity parameters plus the bound env per
statement), not one query per user and not the Cartesian product. Returned
management rows are bounded by two per pair. Bot state/owner completeness and
Human liveness remain chunked at 128 distinct ids; legacy creator reads touch
only version-0 Bots when fallback is enabled. For initialized Bots, round trips
are `2*ceil(B/128) + ceil(P/64) + ceil(H/128)` for B Bots, H Humans and P distinct
pairs; 100 users viewing one initialized Bot therefore use 5 SQL reads, not
400 scalar-role reads. Duplicate inputs preserve output order without more reads.
Storage/integrity failures still fail the whole batch. No positive cache, write
or transaction is added. MySQL uses the existing binary identity predicates;
SQLite query/row counts do not establish MySQL EXPLAIN or lock/contention results.

### Candidate owner projection

`ownerships_for` deduplicates exact input Bot IDs before bounded queries and
restores input order and multiplicity in its result. Missing/deleted/version-0
Bots project `None` (or the legacy creator under the migration shim); corrupt
initialized owner sets and storage failures still error the batch. Returned
owner rows are not deduplicated, so actual integrity failures remain observable.

For B distinct IDs the normal path uses `2*ceil(B/128)` statements (states and
owner actors); the legacy shim adds `ceil(L/128)` creator reads for L version-0
IDs. Empty input performs no reads. A healthy, fully-owned 100-item candidate
page therefore adds two statements, or three with legacy creators; zero-owner
cases add the re-check below. In a healthy initialized state, these return at
most B state rows plus B owner rows, not B rows in total.

A retirement can commit its tombstone and owner revocation between the state and
owner SELECTs. For U initialized IDs with no returned owner row, one liveness
re-check per 128-ID chunk (`ceil(U/128)` extra statements) distinguishes deleted
Bots (`None`) from still-live corrupt owner state (whole-batch error). Healthy
pages add no statements; the re-check starts no transaction and takes no lock.
This reasoning uses the existing single-primary, per-statement read visibility;
routing these reads to a lagged replica would require a shared snapshot instead.

MySQL state/creator/owner projection reads use an indexable raw identity `IN`
prefilter plus a binary residual predicate. The residual preserves byte-exact
case and trailing-space matching on retained collations. Each statement binds
at most 128 distinct IDs twice (257 parameters including env); SQLite binds each
ID once (129 including env). Query-count bounds alone do not establish scanned
row bounds. The opt-in `authority_ownerships_mysql` test runs the full migration
chain, captures generated statements, checks identity index key parts on a
20,000-Bot dataset and checks current/legacy collation behavior. No new cache,
write, lock, transaction, schema, or migration is added to the read path.

### Human friend Bot lookup

Group creation queries only non-public requested Bots that did not pass the
existing management check. For B distinct candidate IDs, SQL uses
`ceil(B/100)` reads and returns at most B IDs using the current approved
Human-to-Bot grants on active default profiles. Empty input adds no read;
errors stop the operation. MySQL uses indexed identity prefilters and a
byte-exact identity check (at most 203 bound values per batch). No full friend
list scan, new cache, write, or lock is added to Group creation. This is not a
MySQL query-plan or contention measurement.

## Runtime ownership

Ownership sync guards both the target's and previous owner's approved manager
counts, including zero, under the Bot row lock. This adds at most two bounded
count queries per transaction; owner and Bot liveness pre-read query counts
stay unchanged. The command still uses at most four transaction attempts and
the existing ten-second transaction deadline. A manager grant racing pre-read
forces a fresh attempt; retirement is classified before owner-integrity errors.
Owner identity validation finishes before any write, and ownership audit states
use JSON serialization while preserving the existing field order.

Authority writes are the only permission-scope writers here; audit rows
append with a monotonic per-mutation sequence. Reads never audit.
Store-level error surfaces re-desensitize (stable codes), exposing no
SQL text. The environment is resolved once at construction.

## Tests

- `cargo test -p bcs-edge-permission-store`
- Candidate projection MySQL gate: `cargo test -p bcs-edge-permission-store
  --test authority_ownerships_mysql -- --ignored` (fresh empty
  `BCS_TEST_MYSQL_URL`; separate database from other MySQL gates).
- Plan §6 MySQL gate: `cargo test -p bcs-edge-permission-store
  --test authority_mysql authority_mysql_conformance -- --ignored
  --exact --nocapture` (needs `BCS_TEST_MYSQL_URL`, fresh empty DB).

Authority role lookup excludes Human management targets before owner/fallback
resolution, in both scalar and batched Bot-state queries. Actor kind is an
additional predicate on the existing statements; query counts and chunk bounds
are unchanged. A Human row with created_by is never a legacy-owned physical Bot.

## Request reads and source layout

`PermissionRequestRepoPort::list_inbox` and `list_sent` return `ServiceResult`.
SQL errors and invalid persisted rows fail the complete list; implementations
must not return empty/partial success. Connect listing and pending lookups
propagate these errors, including through legacy/V1 friendship facades.

SQL implementations are separated into `edge_grants.rs`, `profiles.rs`,
`requests.rs` and `bot_config.rs`; shared row/statement mapping is in
`mapping.rs`. `memory.rs` implements the same repo ports, with conformance
coverage for both SQL and memory stores. The result-type change does not add
queries or change ordering: one SELECT per inbox/outbox, two for All. Pending
connect checks still scan the target's pending inbox (existing behavior), so
this change does not claim bounded pagination or improved high-fan-in cost.

## Friendship cutover read authority

Both legacy and V1 friend facades read edge-owned friendship membership and
`permission_requests`. Legacy friend-request rows are not backfilled, read as
a fallback or required for bootstrap readiness. The current schema/field widths
are sufficient; no cutover migration is required. The legacy unpaged list
reuses the existing fallible, Bot-filtered edge-page query in batches of 100,
not the older unpaged N+1 profile reader. See [list access cost and semantics](../bcs-friend/CONTEXT.md#edge-owned-compatibility-list-access-cost)
and [rollout](../../../migrations/README.md#legacy-friend-request-cutover).

## Agent invocation reader compatibility

The SQL and memory edge stores decode `agent_invoke`, validate its canonical
shape and include it in the existing runtime candidate listing/existence
projection. Raw repository writes are trusted infrastructure operations, not a
new public authorization lifecycle. No Agent-approval endpoint or production
writer is enabled here; callers must not expose raw insert/upsert as such an API.

`list_runtime_grants_checked` performs one indexed pair/env read, with exact
identity residuals (also on legacy case-insensitive schemas), excludes known
authority metadata, and rejects unknown kinds and malformed runtime rows instead
of returning partial/empty success. It does not create profiles or make external
identity calls. The row count remains the number of approved runtime candidates
for that pair, not a bounded governance page; it is not used to scan whole graphs.
Legacy generic admission retains one pair read and filters out AgentInvoke.
No cache, historical migration or index is changed. Shared repo conformance
runs on memory/SQLite; the MySQL driver explicitly requires a disposable empty
database and the full migration chain, and is not proof of MySQL execution unless
run. The durable command implementation is described below; governance query
APIs and production activation remain separate work. Reader compatibility alone
is never sufficient to enable production writers.


## Synthetic local Agent invocation lifecycle

`local_agent_invoke::LocalAgentInvokeStore` implements the new facts and atomic
command contracts for an isolated synthetic namespace. It is NOT bootstrap
wiring, a durable store, a legacy table mirror or a production eligibility
provider. It consumes only Service API/domain contracts and an injected pure
policy Core service. All reads/writes/facts share one local mutex; no network
call, async wait, legacy friend write or profile seed occurs inside it.

It stages fallible work before mutation, scopes keys by principal within the
bound namespace/env, preserves original receipt state on replay, advances
approval/request/identity/eligibility/authority generations, and keeps typed
before/after audit records. Grant/revoke and qualification invalidation cannot
race within this local driver. Roles/delegations and qualification restoration
never silently reset epochs. Active/pending limits are explicit finite positive
values. Local degree checks scan local maps; this is not a SQL aggregate or a
scalability claim. There is no positive authorization cache.

Conformance driver: `tests/conformance_agent_invoke_local.rs`, sharing the
application/port harness `bcs-test-support::contract::agent_invoke`. SQL schema,
atomic rollback/audit failure, query plans, real DB contention, host freshness,
HTTP routes remain separate unverified work. Durable SQL tests below provide
independent transaction/restart evidence, not production integration evidence.


## Durable Agent invocation foundation (not mounted)

`agent_invoke::DbAgentInvokeStore` implements the trusted `AgentInvokeFactRepoPort`,
`AgentInvokeFactsPort` snapshots, trusted governance publication and scope
initialization/activation. `DbAgentInvokeCommandStore` provides atomic commands
on the same database. They consume `DbPlugin` and SQLite 048/049 or MySQL 050/051, and never enables a writer/runtime or invents
eligible facts during initialization. The legacy tables have no tenant key;
`UNIQUE(env)` therefore makes each env exclusive to a single physical namespace.
Exact Bot identities use indexable keys plus binary residuals. Configuration
limits must match persisted values on reopen. Activation requires revision CAS
and cannot enable a writer without runtime capability.

Publication uses two external point SELECTs (scope, joined fact/registration)
and a twelve-step transaction: scope CAS, physical registration lock, fact CAS,
two directed invalidation ranges (overflow guard/audit/metadata/edge per range),
then publication audit. Changed/expired qualification requires advancing
eligibility generation. Binding/revision changes require newer binding or
eligibility generations. Stale incident approvals are durably revoked and
versioned in the same transaction. Native outcome-aware transaction failures distinguish acknowledged rollback
from unknown commit. Legacy opaque errors remain unknown and are never blindly
retried; deadline causes alone do not override an unknown outcome. Reads return
expired facts for publisher recovery, not runtime authorization. No public
HTTP endpoint or bootstrap selects these stores. Host identity resolution and
automatic identity-revision advancement in registry lifecycle writers are still
unimplemented; the new column is not proof of an active freshness capability.

Tests: `tests/agent_invoke_facts.rs` runs the full SQLite chain, exact identity,
CAS, deterministic concurrency, audit rollback, file reopen and actual query
counts. `tests/conformance_agent_invoke_mysql.rs` additionally checks facts and
rollback on a dedicated EMPTY MySQL database in CI; ignored locally is not a
pass. Neither suite proves HTTP/dispatch/host E2E or production capability.


## Durable Agent invocation snapshot boundary

The same `DbAgentInvokeStore` now implements `AgentInvokeFactsPort`. Runtime
reads use one exact-pair SQL statement joining configured scope/capability,
real registrations, published facts/leases and canonical edge/approval metadata.
The real invocation application still applies its pure operation/lineage and
current-generation policy. Missing/expired/unavailable/corrupt data never becomes
a successful default or cached permit. Published true cannot override a missing,
Human-kind, hidden or retired registration.

Management snapshots use six read-only transaction statements: scope lock, two
separate Bot point locks in Rust byte order, pair, exact request, strict authority
projection. Combined IN/ORDER BY is not treated as physical lock-order proof.
Human roles require live Humans, initialized Bots and canonical single-owner
integrity. Original-requester authority is separate from approver authority.
Fences include durable qualification revisions, authority audit epochs and actual
Human registration IDs; the DB clock alone does not change a fence.

Governance requires exact principal hash plus full identity/pair/action residuals.
At most two extra point queries and one scope-revision check detect publication
between lock release and delegation read (publishers must advance scope revision
atomically). Thus the ordinary Human path has six statements, worst governance
path nine, with no N+1 loop; aggregate/index/lock costs still need real MySQL
measurement. The command store below consumes this material but revalidates it inside its
mutating transaction. Neither snapshot reads nor command tests replace a
production host feed.

`tests/conformance_agent_invoke_snapshots.rs` and the synthetic local conformance
entry call the same `agent_invoke_facts_port_contract_tests`; the explicit MySQL
gate uses that harness too. Focused tests additionally record actual plugin SQL
counts/rows and use Notify handshakes for qualification/delegation races, without
sleep-based ordering. HTTP/SDK/dispatch and host integration remain
unimplemented. Test seeding SQL is never production management orchestration.

## Durable Agent invocation commands (not mounted)

`agent_invoke::DbAgentInvokeCommandStore` implements request, direct grant,
approve, reject, cancel and revoke against the actual SQL tables. A command
rechecks current operator authority, recovers an existing receipt before mutable
qualification/version checks, then builds one transaction covering edge/request,
approval versions, pending-pair uniqueness, audit and receipt. Regrant preserves
the edge ID and advances its approval version. Old receipts never restore a
revoked edge; the same key with different canonical input conflicts.

The transaction locks scope, each Bot separately in byte order, involved Humans,
and exact edge/request rows. It then consumes same-transaction query results
for row-count and null-safe byte-exact guards over every trusted pre-read,
including original requester registration/role/delegation and degree aggregates.
Scope CAS serializes competing commands and trusted publications. No legacy
write chaining, external I/O or automatic retry is used. Tests bound an ordinary
management command to at most 48 statements total, 32 transaction steps and 512
binds per statement; runtime remains one pair statement. These are plan bounds,
not MySQL scanned-row or contention measurements.

Fact invalidation takes separate outbound/inbound indexed ranges rather than a
whole-scope OR scan (MySQL FORCE INDEX/STRAIGHT_JOIN; SQLite INDEXED BY/CROSS
JOIN). Work is proportional to incident active approvals; degree limits bound
new grants, not corrupt or preexisting out-of-limit data. Large retained graphs
and real MySQL execution plans still require independent verification.

Trusted `AgentInvokeGovernanceRepoPort` publication changes scoped delegation,
revision and audit atomically; audit identifies the trusted publisher, not the
subject as its own issuer. No public DTO or endpoint exposes this capability.

SQLite runs the same ten lifecycle scenarios as the synthetic local driver,
plus governance-feed conformance. Atomicity tests cover audit rollback, lost
commit acknowledgement/replay, role changes between precheck and commit,
requester/delegation/registration ABA fences, file reopen and actual plan cost.
The independent empty-MySQL CI gate now runs these lifecycle scenarios too.
A local ignored MySQL test is never a passing real-MySQL result. The outcome-aware DB method now supplies typed transaction certainty.
Confirmed rollback maps to closed conflict/unavailable errors; any unknown
outcome remains CommitUnknown, even for duplicate/timeout-looking causes.
Legacy/custom plugins retain a conservative fallback and must not pretend
to have native commit/rollback acknowledgement. Writer/runtime activation must
remain disabled in production until registry producers, trusted ingress,
governance HTTP APIs, HTTP/SDK, dispatch and real E2E integration are complete.

Outcome regression tests use actual SQLite audit failure to prove rollback
does not consume the receipt key, and a recording wrapper loses the detailed
commit acknowledgement after a real commit to prove recovery returns the old
receipt without a second mutation. Existing legacy-wrapper ambiguity tests stay
independent. No transaction retry, statement-count increase, schema change or
production activation is added by the typed-outcome increment.


## Governance query snapshots (not HTTP publication)

`DbAgentInvokeQueryStore` implements the separate `AgentInvokeQueryRepoPort`.
One point-only authorization preflight rejects unauthorized scans; it does not
license a response. A read transaction locks scope, anchor and (for Human)
operator, rereads current authority and queries data. Pure Core reauthorizes
that snapshot before returning it; torn scope IDs/revisions fail closed.
No eligibility/runtime gate, write, revision increment or positive cache is
introduced. Missing counterpart registration preserves history with absent
display fields, never fabricated qualification. Malformed canonical rows,
metadata, delegation or owner graph and DB errors fail the read, not empty it.

Human page/exact operations use 6 SQL statements total (5 for Governance);
degrees use 9 (8 for Governance), including preflight. Lists use one bounded
joined data statement (limit+1 <=101 rows), no OFFSET, per-edge network calls or
N+1 inventory reads. Degrees use four directed aggregates: distinct stored
approved peers in/out and pending requests in/out. Count basis is explicitly
StoredApproved, not live invoke eligibility. Aggregate corruption checks reject
bad scope/version/canonical shape and invalid Agent endpoints.

SQLite 050 / MySQL 052 add request-pair and audit pair/time and action/time
indexes; earlier migrations remain frozen. Keysets use stable edge/internal
request IDs and audit (created_at_ms,id). Audit omits raw snapshots. Peer name
and agent_code are joined display data, not authorization evidence.

SQLite regressions include a 110-peer fixture, maximum page/query/bind budgets,
actual EXPLAIN, audit timestamp ties, no read-side writes, failed lookahead and
metadata decoding, lost authority after preflight (Notify), torn scope, gates
off and historical reads. Shared query consumer/repo/policy harnesses cover
concrete implementations. The native empty-MySQL gate additionally EXPLAINs
actual captured query SQL for seven hinted indexes; local ignored status is
not MySQL proof. A small contract fixture and fixed statement count do not prove
large-graph rows-examined cost, lock contention or scalability. Production
registry/host, trusted ingress, HTTP/SDK, dispatch and E2E are still separate.


## Physical registry producers and current actor status

Runtime/management pair reads now include the actual `bcs_bots.status` as well
as visibility and registration identity/revision. A fresh published hidden=false
cannot override a physically hidden Bot; unknown actor status fails closed.
The existing pure policy, command guards and one-statement runtime read remain
unchanged in call count. SQL registry lifecycle writers now atomically advance
physical identity revision; changed and restored values cannot match an old
approval or fact binding again. `authority::lifecycle`'s tombstone builder
advances revision with its existing checked transition, rejecting exhaustion.

`tests/agent_invoke_registry.rs` drives actual registry/Provider APIs then the
real invocation application; its shared scenario is also in the disposable
native MySQL gate. The SQLite revision-exhaustion and Notify approve/registry
race tests are separate evidence, not native MySQL concurrency proof. Current
hand-built fixtures include the previously committed 049 fence column; no
historical schema/migration is edited. Additional dependencies on Bot store and
configuration are dev-only concrete test assembly, not new production edges.
External inventory/Provider-wide invalidation, host capabilities, trusted HTTP
ingress, dispatch and E2E are still required before production activation.
