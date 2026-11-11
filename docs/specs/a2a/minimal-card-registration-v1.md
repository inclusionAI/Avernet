# Minimal AgentCard registration and storage v1

Updated: 2026-11-06. Internal BCS registration/management contract; not an A2A
extension. This replaces the earlier eight-column Card-management draft.

## Sources, names and authorization

`POST /providers/{provider_id}/bots` accepts exactly one of:

- `agent_card_url`: nonempty URL; BCS retrieves the public Card without Provider
  credentials, then validates the complete declaration.
- `agent_card`: complete JSON object; the same SDK validator runs directly.
  There is no Card GET and no probe Task. Selected HTTP interface policy/DNS/TLS
  validation is still applied; inline submission does not bypass egress rules.

Both fields absent/null selects the existing legacy Provider registration.
Both supplied, malformed/empty URL, nonobject Card, A2A plugin mode or mixed
webhook_url are rejected. Provider/ref, a nonempty `owners` initialization snapshot
and existing admission rules remain required. The first user becomes owner; the
remaining distinct users become initial managers (1–101 raw input entries, trimmed
and validated before Card fetch). This extends the Provider ID registration
contract; the separate public v0.1 registration retains its single-owner policy.
Identity comparison is byte-exact, including case/accent distinctions. MySQL
requires corrected migration 043 and additive 044 (NO PAD utf8mb4_0900_bin) for actor-key uniqueness before accepting these snapshots;
SQLite and Memory already have the required semantics. Provider Admin Bearer is authenticated before external
access. Core repeats Provider/credential/admission checks before atomic creation.

A2A Bot names always equal Card.name, including the legacy registration entry.
Names contain 2..64 Unicode characters, without surrounding whitespace or control
characters; comparisons are exact and case-sensitive. There is no independent
local display alias. Summary defaults from Card.description when omitted.
Explicit updates and URL refreshes atomically synchronize the Bot name, Card,
source and revision. Skills/descriptions/unknown fields remain in the full Card.
Card provider/owners/name never grant BCS ownership, visibility or permissions.

Text search matches the synchronized BCS name or Bot ID as well as Card
name/description/skill text. Both candidate count/pagination and final discovery
filtering apply this rule. Explicit skill selectors always use Card skills for
Card-backed Bots; a name match does not enable legacy capability/summary
fallback. Existing BCS visibility, environment and caller filters still apply.

Provider-scoped v2 `/register` also accepts the URL query or JSON body
`{"agent_card":{...}}`; bot-name can be omitted for A2A (an explicitly blank
scoped bot-name is still rejected). Query/body duplicates
are rejected. A v1 register token cannot select either A2A source. Legacy
non-A2A registrations still require their existing name.

Duplicate Provider/ref returns 409; no credential replay or implicit update.
All Bot/binding/owner/initial-manager-Human/manager-edge/profile/audit/Card writes
stay in one transaction. No
transaction spans network validation, and failed writes propagate as errors.

## Three persisted Bot columns

Only `agent_card`, nullable `agent_card_url` and `agent_card_revision` remain in
bcs_bots. Existing id/gmt_create/gmt_modified remain generic Bot audit fields.
No separate A2A config table, selected-interface JSON, credential-reference JSON,
last-validated/next-refresh/error columns or extra Card cache is introduced.
The Bot gmt_modified can change for unrelated operations, so it is not a Card
validation timestamp and is not used for scheduling.

On each new delivery, the saved Card's supportedInterfaces are considered in
preference order with current HTTP egress rules and configured gRPC/MOSN targets.
A pure domain selector uses no SDK/networking. Full new-Card declaration validation
remains in the outbound adapter using the official SDK. Credentials are resolved
from the trusted Provider binding when the selected Card requirement is Bearer.
Admitted anonymous Cards transmit no Provider Authorization; runtime delivery no longer requires the legacy anonymous opt-in flag. The public v0.1 entry rejects credential-required Cards; legacy ID admission retains Bearer support. Unsupported authentication requirements are rejected.
Their actual values remain in the existing Provider credential store. Availability
batches project interfaces and security declarations from JSON, not full skills payloads.
Existing in-flight runs keep their frozen interface, extension negotiation and
route identity; a refreshed Card never retargets an accepted task.

## Management and fixed refresh rounds

GET `/providers/{provider_id}/bots/{bot_uuid}/a2a` returns the minimal snapshot,
nullable source URL and revision. PUT at the same path accepts exactly one source
and optional expected_revision. Omission uses last-successful-commit-wins; a
supplied legacy revision still uses CAS. Updating URL→inline clears the saved URL atomically.
The old management interface/credential/timestamp/error output fields are removed.

POST `.../a2a/card:refresh` only reads a saved URL. Inline Cards return a clear
error and must be updated by PUT. CAS prevents an earlier URL request from
replacing a newer inline Card. Failure preserves Card, URL and revision; there
is no failure write. New sends may use the last good snapshot, subject to current
Provider/Bot/credential and egress policy checks. In-flight cancellation remains
based on its frozen task/run binding. A validated refresh with identical raw
Card and source URL rechecks the current binding/revision and returns the saved
snapshot without a write or revision increment. A changed Card still uses CAS;
explicit PUT without a revision performs an atomic replacement.

The trusted background loop ticks every 10 seconds and keeps one in-process round
cursor. The first round starts after 300 seconds. At round end, the next due
threshold is the final page's supplied tick timestamp plus 300 seconds; network
processing time is included, rather than promising 300 seconds of idle time
after fetch completion. Each tick reads up to 16 URL-backed Cards with bot_uuid keyset
pagination, processes that batch, and advances past failures. A round can span
multiple ticks and is not a promise that every Bot refreshes at exactly 300s.
Restart resets the round; no durable per-Bot schedule/error/backoff state exists.
Failures are logged without URLs/bodies/credentials; durable write errors are
returned, never hidden behind a successful count. Inline Cards are never scanned.

Explicit registration may persist a declaration returned with no-cache/no-store: the saved Card is managed configuration, not a reusable HTTP response cache. Refresh performs a new fetch. Refresh rejection keeps the prior snapshot under the managed configuration stale-on-error policy. Operators can disable a
Bot/Provider using the existing controls when execution must stop immediately.

## Access cost and limits

Counts refer to application data SQL statements generated by these stores.
Connection-pool/session initialization and driver reconnect work are excluded;
statement counts are not network round-trip measurements:

- Card get: one SELECT returning at most one Card. A changed Card write uses
  one short transaction with an UPDATE and
  SELECT of the committed snapshot. A stale CAS stops after the first UPDATE;
  a name preflight conflict prevents the write. Explicit updates return
  their own committed revision, not a later writer's snapshot.
- Name resolution reads active bcs_bots rows directly, returning at most two rows
  without a join and rejecting ambiguity. Registration and Card writes add one
  fresh name preflight SELECT returning at most one identifier. Without a name
  index these queries may scan Provider rows; LIMIT only bounds the result. Provider resolution uses the existing
  environment/slug index. Authorization and affiliation checks add their usual
  repository reads; resolution is performed at admission, never per token.
- Refresh pages remain bounded to 16 Cards (at most 16 MiB raw payload plus
  decoding allocations), with no separate per-ID page lookup. Each refresh
  rechecks Provider authentication policy, binding and current Card. A changed
  refresh adds the three-statement write transaction; unchanged refreshes make
  no write. Provider/credential cache hits avoid their corresponding DB reads.
  No transaction spans HTTP fetches. Previous 039-only statement totals below
  are superseded; these costs are source-traced, not load-test results.
- The `(env,is_deleted,bot_uuid)` scan can examine ordinary Bots while filtering
  nullable source URLs. Whole-round scan cost grows with registry size; avoiding
  extra persisted selector columns trades off scan work. No full-registry Card
  payload load or unbounded page is used. Large registries need measurement.
- Availability remains one query per at most 256 IDs; DB JSON extraction parses
  the stored Card but transfers only interfaces and authentication declarations. This adds JSON parse
  CPU versus a stored route projection, without adding queries or another cache.
- Fetch/validation is bounded to 1 MiB and 10 seconds. A batch has at most 16
  sequential fetches, plus DB time; no DB transaction covers these waits.

## Development schema and validation

The first published migration is MySQL/SQLite 039_bot_a2a, consolidated from
this feature's uncommitted drafts after upstream reserved versions through 038.
It adds only the three Bot Card columns and creates the normalized Task, Run
binding and context tables directly, without experimental fields/tables or DROP.
All upstream migration files and identifiers remain unchanged. Recreate disposable
local databases that applied earlier A2A drafts; this is not a production upgrade
path for those drafts. Running trial services are not upgraded automatically.

Historical validation of migration 039 completed with documented scope limits. Evidence, per-command counts
and source hashes are in `minimal-card-validation.json`. SQLite full migrations
and real MySQL 8.4 full-chain/storage checks passed, including inline NULL source
and interface text decoding. Focused registration/management/route/query-count
suites and both selected broad regressions passed for that earlier revision. No full-workspace or live
Harness rerun/deployment is claimed; the existing import-rule gate still fails
identically on HEAD and this worktree.


## Provider Bot name preflight (historical, superseded for Card registration)

The provider-ref revision below supersedes A2A name/source uniqueness and related
preflight query budgets in this document. Ordinary non-Card Bot admission is unchanged.

Registration rejects names already used by an active Bot in the same environment
and Provider, including legacy non-A2A Bots. Different Providers may reuse names.
A2A Card writes retain name preflight and atomic Card/name updates. Name resolution
rejects ambiguous existing rows rather than selecting an arbitrary Bot. Names are
case-sensitive; rename and soft deletion release the old name.

This is a server preflight, not a database uniqueness guarantee: concurrent requests
and legacy rename paths can still introduce duplicates. Database uniqueness and
column shortening are deferred until existing data is corrected. The undeployed
040 name migration is removed with user authorization; other migrations are unchanged.
Development databases that already applied an earlier 040 must use a fresh disposable
schema or an explicit reviewed repair, never silently rewrite migration history.

Public management uses GET/PUT `/a2a/agents/{bot_id}/agent-card` and POST
`/a2a/agents/{bot_id}/agent-card:refresh`. POST `/a2a/agents` returns the stable
`bot_id` and Location points to the ID resource. PUT body contains only Card
source and/or visibility; refresh body is empty or {}. Old name-management routes
are removed, not aliased. GET `/a2a/agents?provider_bot_ref=...` recovers zero or
one Provider-scoped result by exact platform reference in the existing binding. No version
history or PATCH is introduced. Existing Provider-ID management remains ID-based;
Provider-name registration also exposes bot_id. Named chat is a separate contract.
Fresh validation is required; historical evidence above does not validate these changes.

### Anonymous-policy rollout

Anonymous A2A access defaults off. Before upgrading existing Agents whose Cards
omit security requirements, explicitly enable the deployment's anonymous policy
if appropriate, or update the Card to declare the Bearer authentication the Agent
actually enforces. Having an old Provider downlink token does not by itself make
an anonymous Card authenticated. Never repair this by silently downgrading a
failed authenticated request.


## Provider-ref registration revision

Public registration requires caller-supplied provider_bot_ref and returns it with bot_id.
Same Provider/ref conflicts rather than updating; existing atomic binding uniqueness and
rollback remain authoritative. Names are display-only and may repeat. Card admission and
updates no longer perform name or source-agent uniqueness reads/locks. No new table,
index or data migration is introduced. Provider chat resolves provider_bot_ref, never name.
The optional source extension is now [v2](extensions/source-agent/v2.md), containing
mutable party classification, not agentId. Owners remain required under existing policy.
See the [public contract](../../bcn-provider-agent-card-api-v0.1.zh-CN.md) for input limits.
