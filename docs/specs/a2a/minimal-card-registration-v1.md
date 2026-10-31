# Minimal AgentCard registration and storage v1

Updated: 2026-10-31. Internal BCS registration/management contract; not an A2A
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
webhook_url are rejected. Provider/ref, exactly one owner and existing admission
rules remain required. Provider Admin Bearer is authenticated before external
access. Core repeats Provider/credential/admission checks before atomic creation.

Provider-admin A2A `name` is an optional local display alias. Missing/empty defaults to Card.name;
summary defaults to Card.description when not supplied. The existing BCS 2..64
character name rule remains; an otherwise valid Card with a longer/shorter name
requires a valid display alias, without changing/truncating the source Card.
Names/default summaries are initialized at registration, not overwritten on Card
refresh. Skills/descriptions/unknown fields remain in the full original Card.
Card provider/owners/name never grant BCS ownership, visibility or permissions.

Provider-scoped v2 `/register` also accepts the URL query or JSON body
`{"agent_card":{...}}`; bot-name can be omitted for A2A (an explicitly blank
scoped bot-name is still rejected). Query/body duplicates
are rejected. A v1 register token cannot select either A2A source. Legacy
non-A2A registrations still require their existing name.

Duplicate Provider/ref returns 409; no credential replay or implicit update.
All Bot/binding/owner/profile/audit/Card writes stay in one transaction. No
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
from the trusted Provider binding and fixed `downlink_bcs_to_provider` kind.
Their actual values remain in the existing Provider credential store. Availability
batches project only the interface array from JSON, not full skills payloads.
Existing in-flight runs keep their frozen interface, extension negotiation and
route identity; a refreshed Card never retargets an accepted task.

## Management and fixed refresh rounds

GET `/providers/{provider_id}/bots/{bot_uuid}/a2a` returns the minimal snapshot,
nullable source URL and revision. PUT at the same path accepts exactly one source
and expected_revision. Updating URL→inline clears the saved URL atomically.
The old management interface/credential/timestamp/error output fields are removed.

POST `.../a2a/card:refresh` only reads a saved URL. Inline Cards return a clear
error and must be updated by PUT. CAS prevents an earlier URL request from
replacing a newer inline Card. Failure preserves Card, URL and revision; there
is no failure write. New sends may use the last good snapshot, subject to current
Provider/Bot/credential and egress policy checks. In-flight cancellation remains
based on its frozen task/run binding.

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

URL no-cache/no-store responses remain unsupported and are not saved. Refresh
rejection keeps the prior snapshot under this application's stale-on-error
policy; it does not implement a complete HTTP cache. Operators can disable a
Bot/Provider using the existing controls when execution must stop immediately.

## Access cost and limits

Counts refer to application data SQL statements generated by these stores.
Connection-pool/session initialization and driver reconnect work are excluded;
statement counts are not network round-trip measurements:

- Card get/CAS: one query/write each; existing Bot/ref/environment/deletion/mode
  guards and revision fencing remain. Registration keeps its existing composite.
- Refresh page: one SELECT, at most 16 complete Cards (up to 16 MiB raw JSON;
  decoded allocations add overhead). No separate per-ID Card lookup. Each Bot
  still needs two uncached Bot-binding SELECTs and one CAS write on success,
  plus one fixed-kind credential lookup. The existing Provider credential cache
  has a 30-second TTL, environment/provider/kind keys and local invalidation on
  credential writes: a hit adds no SQL; a miss adds one SELECT. No new cache is
  added. Background page upper bounds derived from these implementations are
  65 SQL statements for 16 successful cold-cache items (49 with all credential
  hits), or 33 for 16 fetch failures before commit with cold credentials. Shared
  Providers can reuse cached credentials; slow fetches can cross the TTL. Manual
  configuration/refresh additionally performs its Provider-admin authorization;
  manual refresh also checks affiliation once before reading the old snapshot. These are source-traced bounds, not
  an application-wide query-count measurement or a load benchmark.
- The `(env,is_deleted,bot_uuid)` scan can examine ordinary Bots while filtering
  nullable source URLs. Whole-round scan cost grows with registry size; avoiding
  extra persisted selector columns trades off scan work. No full-registry Card
  payload load or unbounded page is used. Large registries need measurement.
- Availability remains one query per at most 256 IDs; DB JSON extraction parses
  the stored Card but transfers only its interface array. This adds JSON parse
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

Validation completed with documented scope limits. Evidence, per-command counts
and source hashes are in `minimal-card-validation.json`. SQLite full migrations
and real MySQL 8.4 full-chain/storage checks passed, including inline NULL source
and interface text decoding. Focused registration/management/route/query-count
suites and both selected broad regressions passed. No full-workspace or live
Harness rerun/deployment is claimed; the existing import-rule gate still fails
identically on HEAD and this worktree.
