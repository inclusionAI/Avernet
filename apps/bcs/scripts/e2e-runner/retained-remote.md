# Supervised retained gateway attachment

This is the incremental remote path for RETAIN-GROUP-01 and RETAIN-SESSION-01.
It shares their existing business steps and never enables the original PRE suite
or the native identity adapter by implication. **Implementation/contract tests are
not a live preproduction PASS.**

Two Profile compositions are admitted (the execution generation): the historical
gateway Human-view Cookie generation and the separately reviewed bcs-direct
dual-header generation (driver Bot token plus Human Cookie references, no explicit
identity binding). Both demand the same operator-attested effect review, exclusive
preprod fixtures, observer binding and finite budgets.

## Current scheduling policy

The accepted preproduction policy is serial triggering plus fresh per-run
preflight, with default resource retention. Use `--serial-preflight`; supply the
exact previous retained Run directory for a scheduling handoff. Complete remote
closure is diagnostic for these control-plane slices, not the prerequisite for a
new Run. See [retained-serial-admission.md](retained-serial-admission.md).

The default command without this flag preserves the original one-shot diagnostic
behavior. Never remove its lock manually or modify an old report to enable reuse.

## Inputs

- A resolved preproduction remote Profile of one admitted generation. Generation
  `gateway-human-view`: gateway entrypoint with the reviewed
  `authorized-human-view` identity binding and exactly the Cookie reference.
  Generation `bcs-direct-dual-header`: bcs-direct entrypoint WITHOUT any explicit
  identity binding (implicit native identity discovery; the legacy business
  adapters bind their own reviewed principals; an explicit NATIVE binding stays
  blocked) with both the driver Bot `Authorization` and Human `Cookie` references.
  Either way: one Human, two owned dedicated protected Bots, and an exclusive
  single-runner pool.
- The review may declare an optional `execution_generation`
  (`gateway-human-view` or `bcs-direct-dual-header`); a declared value must equal
  the Profile's judged generation, and an undeclared review stays valid with the
  generation derived from the Profile and reported in the frozen summary.
- An available Human supervisor and a current source/effect review: test runtimes
  must be isolated, have no operational channels, and permit implicit Bot bootstrap.
  The Run's finite actions do not bound an arbitrary autonomous Bot's effects.
- A separate current observer-binding receipt for the existing exact-resource,
  environment-scoped group/session assertions. It does not authorize execution
  table queries, tables without environment columns, or database writes.
- A fresh Run directory, explicitly selected MCP executable and HTTP/MCP read plus
  write confirmations. Retained resources have a manual review deadline, not TTL.

The private retained-review JSON has this shape (placeholders are not authorization):

```json
{
  "schema_version": 1,
  "contract": "supervised-retained-control-v1",
  "profile_digest": "<complete-profile-sha256>",
  "run_id": "<exact-run-id>",
  "case_id": "RETAIN-GROUP-01",
  "reviewed_at": 0,
  "expires_at": 0,
  "supervisor": "<bound-human-id>",
  "effect_review": {
    "reference": "<actual-review-reference>",
    "sha256": "<review-evidence-sha256>",
    "deployment_commit": "<reviewed-full-commit>",
    "isolated_test_runtime": true,
    "no_operational_channels": true,
    "implicit_bot_execution_allowed": true
  },
  "retention_quota": {
    "contract": "retained-resource-quota-v1",
    "registry_id": "reviewed-inventory-id",
    "mode": "initialize",
    "expected_digest": "absent",
    "max_groups": 10,
    "max_sessions": 20,
    "baseline_groups": 0,
    "baseline_sessions": 0,
    "inventory_reference": "actual-inventory-review-reference",
    "inventory_sha256": "<reviewed-inventory-evidence-sha256>"
  }
}
```

The validity window must be current and at most one hour. Evidence and confirmation
must actually exist; do not generate approvals from this example. The review is
**OPERATOR_ATTESTED**, not a signature or independently verified deployment. A
changed deployment or fixture requires re-review, not merely changing dates. The
runner never rewrites expiry, repairs actors or auto-retries a denied route.

## Command

From the runner directory, using real private paths and the same confirmed Run ID:

```bash
python3 runner.py retained-run \
  --profile /private/path/profile.json \
  --state-dir /private/path/new-run \
  --run-id retained-run-001 --case RETAIN-GROUP-01 \
  --retained-review /private/path/retained-review.json \
  --observer-binding /private/path/observer-binding.json \
  --mcporter-binary /absolute/path/to/mcporter \
  --allow-read --allow-observer-read --allow-write \
  --serial-preflight \
  --confirm-run-id retained-run-001
```

For the next fresh Run, keep the same test-pool Profile and add
`--previous-state-dir /private/path/previous-run`. Use a new Run ID, state directory
and Run-bound retained review. Do not repeat the previous Run or its write request.
Quota accounting uses an explicitly reviewed baseline when the registry is absent;
subsequent reviews reference the current registry digest, retaining all earlier
charges. An older Run without a quota receipt is not a zero baseline: inspect its
exact retained inventory before initializing the accounting. A failed fresh check
does not transfer the existing reservation.

Supply secrets through the Profile's environment references, not command-line
Cookie values. Use the existing approved CA configuration where needed; never
disable TLS verification. The command supports the gateway Human-view contract,
not a guessed `/openapi/v1/auth/user` endpoint or fallback to native `/auth/user`.
The dual-header bcs-direct generation likewise fixes both routes through the
resolved Profile: implicit native discovery for identity and the reviewed
legacy public-V1 business adapters; no guessed endpoints either.

## Admission and limits

Input validation precedes HTTP access. HTTP preflight checks identity, environment,
owned/online/protected actors before MCP metadata. The public-v1 Bot response
exposes `visibility` at the top level (inside the wire envelope's `data`), not in
`capabilities`. Missing/invalid/non-protected visibility is rejected; nested
metadata cannot substitute for that contract field. Only the selected slice's
resource projections are admitted; file metadata is not required for these Cases.
Both reviews are frozen/bound to the Run. Preconditions are rechecked after taking
the pool and before creation; profile, review integrity and expiry are revalidated
for subsequent operations. MCP remains read-only and exact current-Case scoped.

Per slice: one Group create, at most one explicit Session create (plus the Group's
implicit Session), one title update, finite reads/token/WS calls, no create retries.
Each HTTP preflight client has a 12-request bound; each MCP phase has 32 calls at
most, using the Profile's per-call deadline/output limit. The remote adapter exposes
no delete/file/stop operations. Bootstrap and Session payloads must match the shared
control Story and its exact actors; no arbitrary prompt/participants/callbacks.
Client budgets are not server-side fencing or a hard Bot runtime timeout.

Observer-review expiry blocks subsequent business calls, not only DB access.
Owned socket closure remains available after expiry. Ambiguous create/connection
outcomes are retained; missing response IDs do not grant resource adoption.

## Results under the serial policy

The adapter verifies functional APIs, actual owned WS behavior where selected,
and exact resource persistence. With `--serial-preflight`, the selected Case can
return PASS and the canonical summary `RETAINED_CASE_PASS` / exit 0 when its
business assertions, required evidence and local connection finalization pass.
Complete remote execution may still be UNCONFIRMED; resources remain RETAINED.
The next Run must perform fresh dependency checks under exclusive trigger authority.

This qualifies only the selected retained Case, not full PRE-01/02/03, omitted
deletes, the complete release inventory or production readiness. Old runs retain
their original BLOCKED/QUARANTINED reports. Without the serial flag, the original
one-shot result is still BLOCKED/exit 2 when functional=PASS. Cancellation remains
exit 130. Neither policy issues automatic delete or cancel operations.

API 401/403, missing required service/actor/evidence dependencies, actual concurrent
triggers and quota exhaustion block new writes. Update the referenced private
Cookie when authentication fails, then run fresh checks; do not fall back to an
alternate route or identity. Database metadata compatibility only establishes
column presence/access, not current-Case row assertions or deployment identity.

A scheduling handoff preserves the previous Run byte-for-byte. Preparation
failures with a durable, bound NOT_STARTED / NOT_RUN receipt may hand off to a
fresh Run after dependencies are corrected, without replay or quota refund.
Incomplete transfer/publication and uncertain local trigger ownership require
inspection; complete historical remote termination is not substituted for that
local check. A pending/completion publication fence prevents partial PASS evidence
from becoming successful offline qualification.

The existing resource observers cannot establish execution completion merely by
returning Group/Session rows. Optional execution observations remain useful
diagnostics, not a universal new-run prerequisite. See `execution-observation-audit.md`.

## Optional exact delivery-state observation

A newly reviewed private retained receipt may include this additional top-level
field:

```json
"execution_observation": "bcs-session-deliveries-v1"
```

This is explicit authorization for the fixed Session-scoped read, not implicit
permission inherited from an older resource-only observer receipt. The existing
mapping receipt still binds the data source and environment. Both reviews must
remain valid; this delivery-only permission admits no arbitrary table, field,
SQL, no-env table or Bot-wide scan. The separately reviewed downstream contract
below has a narrower, explicit no-env result-table exception. Without the new permission, execution observation is NOT_CONFIGURED and
no delivery row query occurs.

Preflight and execution admission describe the required delivery columns before
business creation. After successful business steps, the runner reads each owned
Session (including the implicit initial Session), **before closing its held WS**.
The query boundary itself requires the active Run/Case ledger, its exact database
file, a held matching pool lease, live-created resource ownership and the Group
parent chain. A caller cannot substitute another Session merely because it is in
the same environment. These checks repeat after each query.

The fixed query selects 16 non-content fields from `bcs_message_deliveries` with
`env + group_id + session_id` plus the two exactly bound target Bot IDs, ordered by delivery ID and limited to 100 rows. It
reads at most two Sessions once each. A full 100-row page is incomplete rather
than silently treated as all work; no auto-pagination or retries broaden the
budget. The two reads share one observation deadline; the actual MCP process gets
only the remaining time (capped by its configured timeout). Child shutdown retains
the existing bounded cleanup allowance. This is not a remote engine-stop deadline.

The report records exact task states and correlation IDs. A managed Send with a
known run ID, terminal timestamp and `completed` is a **known completed task**, not
proof of complete scope quiescence. Queued/running/unknown work, Inject disposition,
empty results and source failures remain explicit. Effective policy, non-managed
work, complete successor/producer coverage and downstream/callback completion
remain declared gaps; overall execution status is still UNCONFIRMED and the pool
is retained. No local execution IDs or artificial terminal transitions are created.

JSON/JUnit/Markdown expose observation status, observed Session count and known
completed Send count separately from Case qualification. An observation-phase
cancellation preserves already-completed functional assertions and prior Session
evidence, closes only the owned WS, records overall CANCELLED, and retains the
pool. Failure/timeout does not overwrite a successful API functional result.

The reviewed schema snapshot has an `(env, target_bot_id, session_id,
source_session_seq)` index, but no comparable Session-leading scope index. The
fixed recipient filter exposes that indexed prefix rather than relying on an
environment-wide scan. This is an access-cost design check, **not a live EXPLAIN
or measured latency claim**. Other recipients are explicitly outside this
snapshot and remain a scope-coverage gap, never silently certified as absent.

## Optional bounded natural-completion wait

For a newly reviewed run, `execution_observation` may instead select
`bcs-session-deliveries-wait-v1`. This is a separate finite query budget; an
existing `bcs-session-deliveries-v1` receipt still permits the original single
snapshot and is not automatically upgraded.

The wait observer takes at most **three rounds**, each reading the one or two
exact owned Sessions: at most six delivery queries, no create/stop/delete retries.
Rounds are separated by at most one second, sharing the same original observation
deadline and remaining-time transport limits. No additional table or field is read.

It stops when all currently observed Sends have known successful completion and
observed Inject rows have a terminal passive disposition (`consumed`,
`discarded_context`, `cancelled`, `expired` or `failed`) with
`may_have_been_sent=false/0`, or on timeout, query limit, cancellation, error or
inconsistent history. An expired **Send** remains uncertain, never settled.
This stop condition is named
`KNOWN_DELIVERIES_SETTLED`, **not scope completion**: unobserved recipients,
non-managed paths, future producers and downstream callbacks remain gaps.
`scope_status` stays UNCONFIRMED; no automatic PASS or pool release is introduced.
Each Session still needs at least one known completed Send. Empty rows,
context-only snapshots, pending/bound context, malformed possible-send Inject
and unsuccessful Sends do not satisfy the stop condition. These rules also
apply to wait-v2; they do not add reads to the single-snapshot contract.

A vanished previously observed delivery, decreasing version/attempt, changed
source/recipient/kind/run identity, same-version content changes, or reversal of
an observed terminal state (including `expired` and `rejected_capacity`) ends
observation with `INCONSISTENT_HISTORY`, not a fresh green snapshot. New successor rows are included in subsequent observations.
The report stores bounded round summaries plus status/version/attempt history,
and the last valid row projection **per Session** with `observed_round`. A later
round failure preserves those projections and its `attempted_round`; retained
older rows are not represented as newly observed or used to declare settlement.
JSON, JUnit and Markdown expose the round count and stop reason independently
from functional results and full release qualification.

This is useful waiting for natural state progression, not a new product execution
barrier. A completed polling window is not proof that every asynchronous effect
has stopped. Live preproduction validation remains separate from synthetic tests.


## Optional correlated BaaS queue/result observation

A newly reviewed retained receipt may additionally contain:

```json
"downstream_observation": {
  "contract": "baas-correlated-run-v1",
  "run_id_source": "bcn-canonical",
  "queue_enabled": true,
  "session_binding": "bcs-session-id",
  "bot_bindings": {
    "<exact-BCS-driver-id>": "<reviewed-BaaS-logical-driver-id>",
    "<exact-BCS-member-id>": "<reviewed-BaaS-logical-member-id>"
  }
}
```

This requires an explicit delivery observation permission as well. It is an
operator-reviewed runtime mapping, not a discovery shortcut. Confirm the deployed
route, queue dispatcher and Session propagation from actual configuration/evidence
before authorizing it. Do not fill the values by assuming BCS Bot IDs, BaaS logical
Bot IDs and device IDs are interchangeable. Both exact actors must be mapped to
two distinct BaaS logical IDs.

`bcn-canonical` selects the observed delivery's canonical `run_id` for the reviewed
HTTP Provider/BCN route. `accepted-alias` instead selects its observed
`downstream_run_id`, only when the accepted alias is independently confirmed to
identify that BaaS run. There is no fallback between the two. A WS accepted alias
is not inherently a BaaS ID. Missing IDs or an incompatible/unverified route do
not authorize guessing or a broad lookup.

After the final valid delivery snapshot, the query boundary requires the same
active owned scope and a Send row actually observed by the reviewed client. A
new failed delivery read invalidates the old provenance. Each candidate permits:

1. `baas_bot_run_queue`: exactly `env + run_id + bot_id`; only
   `run_id,bot_id,session_id,env,status`, limit 2. The returned Session must equal
   the owned BCS Session; duplicate/mismatched/truncated results are rejected.
2. Only after that queue binding passes, `baas_bot_run`: exactly `run_id + bot_id`;
   only `run_id,bot_id,status`, limit 2. This table has no environment column, so
   the just-verified queue correlation is required before the read.

An absent queue row means UNOBSERVED, not no work or functional failure: queue
rows can disappear, and some deployments use a non-queue dispatcher. This
contract only supports explicitly reviewed queue-enabled routes. No queue row
means **no result-table lookup**. Metadata for both tables is checked before
business creation; metadata compatibility is not authorization or live execution
proof. The mapping/source review and owned lease are rechecked around each query.

At most four run attempts and eight extra business queries are allowed, under
the same absolute monotonic delivery/downstream observation deadline and 32-call
MCP phase budget. Authorization validation time consumes that deadline too.
More than four candidates marks observation incomplete instead of selecting an
arbitrary prefix. Each attempt reserves two MCP calls even if the queue is empty
or a read fails. Queries use exact run identity; no payload, metadata JSON,
credentials, Bot-wide scans, joins, repair writes or automatic retries are added.
This is a bounded access-cost contract, not a measured production latency claim.

Reports retain per-run queue/result states and missing bindings. An accepted
queue snapshot survives a later result-read failure/cancellation as partial
evidence. Cancellation, lost authority or a late return at the outer boundary
preserves the verified queue but does not credit the final result. DONE plus
COMPLETED sets only `known_run_result_completed`; callback
and whole-scope status remain UNCONFIRMED. JSON/JUnit/Markdown expose downstream
counts separately, with no change to overall BLOCKED, pool quarantine or the
original suite denominator. Real preproduction validation is still required.


## Independent observation window (explicit wait-v2)

A newly reviewed retained receipt can select a separate finite polling schedule:

```json
"execution_observation": "bcs-session-deliveries-wait-v2",
"execution_observation_budget": {
  "timeout_seconds": 120,
  "max_rounds": 6,
  "poll_interval_seconds": 10
}
```

All three fields are required, strict integers (not booleans), with limits of
1–300 seconds total, 2–6 rounds and 1–30 seconds between rounds. These are upper
bounds, not promises to complete all rounds. A short window can expire during a
query or backoff. The example is not an approval or a changed default.

The v2 schedule is frozen in the Run/Case/Profile review, not read dynamically
from a mutable environment variable or injected into transport configuration.
The Profile's existing `policy.timeout_seconds` still caps **each HTTP/MCP call**;
the reviewed observation window covers all delivery queries, validation, backoff
and any separately authorized downstream observation. One monotonic deadline is
created on observation entry and passed through query authorization validation.
Remaining time decreases; neither another round nor another table resets it.
Backoff is clipped to the remaining deadline. A longer observation window does
not renew an expired Cookie, effect review, database receipt or pool lease.

Compatibility is explicit: snapshot-v1 still performs one round and wait-v1
still performs at most three, inheriting the original Profile-derived window.
They reject `execution_observation_budget`, including null. V2 without its budget
is rejected before HTTP/preparation. No old frozen Run, review, timeout result or
quarantine is migrated or extended automatically.

At most two owned Sessions × six rounds = **12 delivery queries** are allowed.
The existing MCP execution-phase cap remains **32 calls**, with no retry or
pagination. Worst-case admission/observation totals (including repeated schema
checks during persistence assertions) are:

| Slice | Without downstream | With four downstream runs / eight extra queries |
| --- | --- | --- |
| RETAIN-GROUP-01 | 12 | 22 |
| RETAIN-SESSION-01 | 21 | 31 |

Preflight metadata is a separate phase (Group 4/6 and Session 5/7 calls without/
with downstream). Downstream is observed once after delivery polling, not every
round. Failed attempts still consume budgets. The 31-call Session path has only
one spare slot; adding another lookup requires updating this accounting and its
maximum-path contract test, not resetting or increasing the client limit.

JSON/JUnit/Markdown expose total observation seconds, round limit and poll
interval separately from outcome. Natural state progression beyond the HTTP
per-call timeout can now be observed; completed Sends still do not establish
callback/producer coverage, release the pool or turn the Case green. Cancellation,
late results, lost authority and inconsistent history retain their fail-closed
semantics and last valid partial evidence. No new remote run is authorized merely
by implementing this schedule.

## Recheck a quarantined retained Run without replaying its Story

`retained-recheck` is a **database-read-only** evidence command for an existing
`QUARANTINED` Run. It is not recovery execution, a new Case, an identity probe or
an automatic release decision. It does not construct an HTTP/product adapter,
resolve a Cookie, send messages, create/delete resources or stop a Bot.

First inspect the source and generate a non-authorizing template offline:

```bash
python3 runner.py retained-recheck \
  --profile /private/path/profile.json \
  --state-dir /private/path/existing-run \
  --run-id retained-run-001 --case RETAIN-GROUP-01 --dry-run
```

The output's `review_template` requires a new explicit read approval: set its
unique safe `observation_id`, actual reviewer, current validity window (at most
one hour), reviewed wait-v2 budget and the SHA256 fingerprint of the **new**
observer-binding JSON. The fingerprint is the runner's canonical sorted compact
JSON digest, not a hash of whitespace-sensitive file bytes. The offline zero
timestamps and binding placeholder intentionally fail execution validation.
The exact `source` digests must remain unchanged. Do not manufacture a renewal
of the old effect or DB authorization. Optional downstream reads require their
own mapping in this new review; none is inherited from the original Run.

With that fresh review and current scoped observer binding:

```bash
python3 runner.py retained-recheck \
  --profile /private/path/profile.json \
  --state-dir /private/path/existing-run \
  --run-id retained-run-001 --case RETAIN-GROUP-01 \
  --review /private/path/recheck-review.json \
  --observer-binding /private/path/fresh-observer-binding.json \
  --mcporter-binary /absolute/path/to/mcporter \
  --allow-observer-read --confirm-run-id retained-run-001
```

There is deliberately no `--allow-write` or arbitrary output directory. Evidence
is stored in `observations/<observation_id>/` inside that Run, separately from the
original Case report, state, receipts and resource ledger. The directory is
exclusively created before the first MCP call: the same observation ID cannot be
replayed after success, failure or interruption, nor can its budget be silently
restarted. A further observation needs a newly reviewed unique ID. Files are
private; publication failure returns `persistence=FAILED`, never a saved claim.

Admission requires a frozen supported retained Case version, exactly one prior
attempt, a complete known-owned Group/Session inventory, and exact agreement
between retention manifest and read-only resource journal. Unknown create IDs,
borrowed resources, additional resource kinds, changed artifacts and missing
original reports are blocked before remote reads. The original executor digest
is preserved and a separate observer executor digest is recorded, allowing a
new read-only implementation to inspect an older Run without globally weakening
normal execution's frozen-code checks. Local digests are integrity checks, not
signatures or protection against a malicious writer to the entire private Run.

The command obtains the **existing** matching reservation's command guard without
creating a guard, stealing a held lock, updating PID/expiry, recreating a missing
reservation or rewriting Run status. Source file identities and semantic digests,
lease and both fresh authorizations are rechecked, including around metadata
calls. Read-only SQLite mode never adds tables or records effects/correlation.
An original receipt may have expired: it remains frozen historical evidence,
not current authority. Fresh receipts cannot be changed or renewed mid-command.

The v2 query, field, row, round and deadline limits apply. The client retains its
32-call cap; this recheck skips product/resource preflight and persistence
assertions, so two Sessions/six rounds/four separately authorized downstream Runs
use at most 23 calls (three schema descriptions + twelve delivery + eight
downstream queries). No hidden polling, schema fallback or retry is added.
Cancellation saves available partial observations when possible; SIGTERM during
publication is reported as cancellation even if disk writing fails. Unconfirmed
shutdown of a query's own MCP child remains explicit. No product stop is issued.

A successful read is nested `execution_observation.status=OBSERVED`; the outer
result remains **BLOCKED / exit 2**, execution UNCONFIRMED and pool QUARANTINED.
Cancellation exits 130. This command does not overwrite an earlier TIMED_OUT,
upgrade functional results, assert cross-recheck monotonic history, or release
fixtures even when all observed Sends are completed. Full scope/producer/callback
coverage and quota-aware reuse still require a separate acceptance contract.

## Reading retained context diagnostics

Each raw Session observation (including preserved partial snapshots and recheck
reports) now contains a `snapshot_activity` projection:

- `ACTIVE_SEND_OBSERVED`: queued, dispatching, running or cancelling Send work
  exists. “Active” includes queued/runnable work, not only an executing engine.
- `UNRESOLVED_ACTIVITY`: an uncertain/expired Send, bound context or unclassified
  state shape needs reconciliation.
- `NO_ACTIVE_SEND_IN_SNAPSHOT`: no active/uncertain Send is present in this finite
  snapshot; pending Inject is counted separately as retained context.
- `NO_EVIDENCE`: the snapshot is empty, which is not proof of absence of work.

Counts distinguish pending, bound and terminal contexts from Send states. An
Inject `pending_context` is not itself an engine invocation; it can be carried
by future Send input in the same lane. The projection does not grant pool reuse,
change the wait stopping condition or reinterpret an old timeout. Reports retain
per-Session round markers, so a saved earlier snapshot must not be described as
a fresh global state. See `execution-observation-audit.md` for source anchors and
the remaining producer/retention acceptance requirements.

## Optional Group/Session facts in a read-only recheck

A new `retained-execution-recheck-v1` receipt may explicitly add:

```json
"scope_observation": "retained-group-session-facts-v1"
```

This permission is **recheck-only**. An original retained-run review rejects the
field; old recheck receipts and dry-run templates do not acquire it implicitly.
It does not authorize scanning other Sessions, reading callback credentials,
changing group strategy or completing a Session.

The observer first checks the two schemas, then queries the already registered
Group once and each owned Session once, before delivery polling:

| Table | Fixed projection | Exact filters |
| --- | --- | --- |
| `bcs_groups` | `group_id,env,group_kind,group_strategy,status,originator,driver_bot` | env + owned group_id |
| `bcs_group_sessions` | `session_id,group_id,env,session_kind,status,activation_count,callback_status,completed_at` | env + owned group_id + owned session_id |

Each query has limit 2 and must return exactly one complete matching row. Missing,
duplicate, truncated, foreign or malformed results fail closed. Group owner and
driver must match the reviewed fixtures. No input/output/meta, participants,
lease token or arbitrary field is selected. Empty callback status is recorded as
null, not interpreted as completed or not applicable.

Rows are saved in `execution_observation.scope_observation`. This is a sequence
of observations, **not an atomic snapshot**, and it precedes the delivery polling
window's later rounds. It exposes Session type and activation/callback facts for
subsequent applicability analysis; it never declares producers closed or rules
out Group-level callbacks. `producer_coverage=NOT_VERIFIED`, scope UNCONFIRMED and
pool reuse false remain explicit even when the Group/Session status is completed.

Facts share the original wait-v2 deadline with delivery/downstream reads. Both
query-boundary checks and the outer post-authority check must finish before that
deadline; late rows are not accepted. A later Session failure/cancellation keeps
previously accepted Group facts as partial evidence, not a complete observation.
Two extra schema descriptions plus at most three row reads increase the maximum
recheck invocation to **28 MCP calls** (5 descriptions + 3 facts + 12 delivery +
8 separately authorized downstream reads), within the unchanged 32-call cap.
There are no retries and no new observation window per phase.

Schema support is checked at execution time. Implementing this projection does
not authorize a live recheck or prove the current deployment's producer coverage.


## Mandatory retained-resource quota for new remote runs

New remote retained execution requires the `retention_quota` review above.
The example's baseline and limits are illustrative, not a verified inventory or
permission. The supervisor must account for existing retained resources, including
older diagnostic runs and uncertain creates. A zero baseline is not inferred from
an empty local directory, an expired receipt or the absence of a resource ID.
Historical results and database-only rechecks do not acquire this requirement;
they are not rewritten, migrated or granted new write authority.

Before any resource intent or product write, the runner holds the existing pool
command guard and reserves the Case's **maximum**, not its eventual result:

| Case | Groups | Sessions (including the implicit initial Session) |
| --- | ---: | ---: |
| RETAIN-GROUP-01 | 1 | 1 |
| RETAIN-SESSION-01 | 1 | 2 |

A private `.retention-quota.json` journal lives beside the fixed pool registry's
`.lock` and persistent `.guard`, shared across checkouts and Run directories for
this OS account. It remains independent of lease release. Initialization requires
explicit `mode=initialize`, `expected_digest=absent` and a genuinely absent journal.
An existing, corrupt or symlink target cannot be initialized over. Later reviews
must use `mode=existing` with the current canonical JSON fingerprint (not the raw
file-byte hash). The exact baseline, limits, inventory review and target/actors
stay frozen; implicit rebasing or resetting is refused. A changed timeout or
credential reference does not create another accounting bucket. Changing pool
identity or moving to another machine is **not** a supported way to reuse the same
Bots: this remains an operator-enforced single-machine, exclusive-fixture contract,
not a server-enforced or distributed quota.

Limits are strict integers 1–10000 for each resource, with reviewed baselines
between zero and the corresponding limit. The journal holds at most 1000
reservations. Under the same held guard the runner verifies the expected digest,
recomputes baseline plus all reservations, checks both limits and writes the new
reservation atomically. Duplicate Run IDs, stale reviews, inconsistent costs or
changed targets block admission. This adds **zero database calls** and does not
change the separate HTTP/MCP call budgets. It does not measure physical storage,
messages, Bot compute or downstream autonomous effects.

Initialization and the first reservation are one journal write. The Run first
records quota status UNCONFIRMED, then the journal is persisted, then its successful
receipt is copied into the Run. These files are not one atomic transaction:
an exception after journal replacement may leave consumed quota without a saved
Run receipt. Such a Run remains quarantined and reports uncertainty; never replay
business work or infer a refund from NOT_RUN or a disk error. The journal is the
accounting authority; Run/retention/Case receipts are projections, not reset points.
Receipts bind the Run/Case/Profile/review and prior/new journal digests.

All reserved maximums remain consumed after an API failure, lost response,
authorization expiry, incomplete snapshot, unused reservation or known completion.
There is no automatic refund, expiry, reconciliation, deletion or quota reset
command. Reconciliation and safe fixture reuse still need their own implementation
and explicit review; having quota available does not clear an unresolved pool.
The current real quarantined Run is not modified by this implementation.

Quota increment validation (2026-10-12): 20 quota conformance tests, 20 retained
recheck tests (including a synthetic historical Run without quota), and the final
896-test runner regression passed with owned BCS/CLI runtime tests enabled.
Case guard, strict OpenSpec and diff checks passed. Initial focused testing caught
and corrected a retention-receipt field-name mismatch before the final runs;
independent read-only review confirmed the correction. No live preproduction
calls or shared legacy singlebox coverage gate were performed for this increment.

## Optional producer inventory in a read-only recheck

A fresh recheck review may select this **alternative** `scope_observation` contract:

```json
"scope_observation": "retained-producer-inventory-v1"
```

It includes the existing Group/Session facts, adds the Group's `service_mode`, and
reads `bcs_state_machine_runs` once for each exact owned Session. It is recheck-only;
old Group/Session facts receipts retain exactly their former fields and queries.
The original retained-run review still rejects scope permissions. No credential,
write authority or query permission is inherited from the historical Run.

The state-machine projection is:
`id,env,group_id,session_id,run_id,record_status,status,session_activation_count,root_run_id,rerun_of,updated_at_ms,completed_at_ms`.
The fixed query requires the reviewed env and exact owned Group/Session, orders
by `id asc`, and requests 21 rows. More than 20, duplicate IDs/Run IDs, unordered
rows, wrong scope, unknown status, missing fields, tool failure or truncation are
rejected. It does not filter record status, activation or Run state, so soft-deleted
records and chat one-shots with NULL activation are not silently omitted. Nullable
legacy lineage fields are preserved, not fabricated. No input/output/error content,
definition, callback token, SQL mutation, unbounded scan or cross-scope lineage
traversal is allowed.

`scope_observation.state_machine_inventories` retains each accepted per-Session
snapshot. Empty means `NO_RECORDED_RUNS_IN_SNAPSHOT`, not proof that no work exists
elsewhere, no later Run can start, or the read source is current enough. Any rows,
including completed Runs, produce `RUNS_REQUIRE_REVIEW`; child/terminal coverage
is not inferred. Group service mode describes current configuration only, not its
history. See `execution-observation-audit.md` for the source semantics and the
remaining in-memory/interaction/configuration evidence gaps.

All schema checks precede row reads. The same Run/Case/ledger, exact parent chain,
fresh reviews and held existing pool guard are checked at each query boundary.
The single wait-v2 deadline includes these reads and their postchecks. Late or
unauthorized returns are not accepted; earlier Group/Session/inventory facts survive
subsequent failure or cancellation as partial evidence. A new table permission
does not extend the deadline, permit retries, overwrite old results or release the
pool.

The worst recheck cost is **31 MCP calls**: six schema descriptions, three
Group/Session fact reads, two inventories, twelve delivery polls and eight
separately authorized downstream reads. The existing phase cap remains 32.
At most 42 state-machine rows are returned across two Sessions; only complete
pages of at most 20 each are accepted. The exact Session/env index exists in the
audited source, but deployment schema and query performance still need verification.

Producer-inventory validation (2026-10-12): 18 focused tests and 112 existing
observation tests passed; the final 914-test runner regression passed with owned
BCS/CLI runtime enabled. Tests cover NULL activation/lineage, inactive records,
wrong scope, old-permission refusal, schema gaps, complete-page limits,
authorization drift, timeout/cancellation partial evidence and the 31-call maximum.
Independent source review found no required correction. This is local validation,
not a live migration check, preproduction completion or a legacy coverage-gate pass.

## Persistent environment facts instead of hourly mapping renewal

The independent v2 readonly path now separates stable database mapping from the
one-shot source-bound read plan and explicit execution flags. See
`environment-mapping.md`. This removes the local one-hour mapping/approval timer
from that new path without removing exact ownership, runtime budgets, revocation
checks or one-use observation directories. Existing v1 receipts and their expiry
remain unchanged; no real receipt is auto-converted. A persistent mapping alone
never authorizes SQL, business execution, cleanup or fixture release.

## Cookie-authenticated WebSocket Upgrade

The gateway adapter passes the same resolved Cookie and explicitly configured
browser Origin/Referer to its fixed-origin WebSocket Upgrade. The Session token
still binds the Group/Session connection; it is not a substitute for gateway
login context. Under the dual-header bcs-direct generation the Upgrade carries
no Cookie; the legacy-face Session token still binds the Group/Session
connection. Only these three header names are accepted, values are bounded
and control characters are rejected. They are never copied to an alternate host,
redirect or storage URL, and their values never enter request evidence. The
standalone token-only WS probe remains a separate diagnostic interface.

A rejected Upgrade remains a failed Case, not a connected socket or a passing WS
assertion. New transport evidence records local socket close separately. For old
reports that lack this field, an explicit redirect/auth rejection before any
connect frame can establish that no WS connection was opened for scheduling
handoff under the held guard. Unknown outcomes, an accepted Upgrade/connect or
unconfirmed closure of an acquired connection do not get this exception. The
old failed report is preserved, not rewritten to claim socket closure or PASS.
