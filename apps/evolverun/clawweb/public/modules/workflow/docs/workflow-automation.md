# Workflow automation v1

Daily analysis and optional draft generation extend the existing repair workbench.
Publication remains an explicit, authorized review action.

## Configuration and ownership

The management page uses the existing `task_guard_auto_analyze_<workflow hash>`
row in `cm_app_config`. Its `enabled` column is the single switch for failed-run
events and daily scanning. The versioned JSON/YAML policy records `autoDraft`,
`dailyTime`, `Asia/Shanghai`, and an update timestamp. Writes use optimistic
concurrency. Defaults are off; existing environment/legacy failure-event settings
remain compatible, but do not acquire a daily schedule until explicitly saved.
There are no new environment variables or tables.

Avernet owns policy, state, selection, recovery, contracts, and UI. The OCB
composition adapter resolves the current Owner, checks edit permission, invokes
the existing run-analysis starter and Git baseline service, and sends through the
existing notification provider. All automatic generation dispatches recheck the
switch, policy version, and current Owner; analysis additionally checks its lease
token immediately before dispatch.

## Service API and Host Plugin API

`WorkflowAutomationService` exposes trusted `tick` and `onRunTerminal` entries,
status/retry operations, and the final draft authorization guard.
`WorkflowAutomationPorts` defines baseline, Owner, permission, analysis lookup/
start, and notification operations. Analysis results distinguish running,
completed, failed, absent, and uncertain. Notification results distinguish sent,
confirmed rejection, and uncertain. These APIs are separate from HTTP transport.

The existing settings GET/PUT endpoint remains canonical. Full writes include
`enabled`, `autoDraft`, `dailyTime`, and `expectedVersion`; invalid policies return
400 and concurrent writes return 409. `automation-status` requires view access;
`automation-retry` requires edit access. Neither endpoint publishes a candidate.
OCB supplies a verified principal to these routes.

## Durable processing and bounds

The Host's existing reconciliation monitor runs the service. Repair lifecycle
records hold `auto_cycle`, `auto_run`, `auto_draft`, and `auto_notice` journals,
isolated by kind and a workflow task-scope hash. Database locks, CAS versions,
five-minute leases, and dispatch tokens fence competing Hosts and late updates.
This requires the existing repair lifecycle migration, not a new schema.

Missed days coalesce into the latest due Shanghai date. Terminal-run scans page
20 records by modification timestamp and ID, retain a cutoff/cursor, and overlap
the next window by one minute for late arrivals. Run identities deduplicate
analysis. A checkpoint means the scan completed, not that all analysis succeeded;
status separately reports pending, failed, partial, and uncertain coverage.
Initial daily scanning covers the preceding day; retained pending repair issues
remain selectable regardless of that scan window.

Each tick visits at most ten workflows; each workflow processes up to three jobs
per kind. External analysis/generation/notification waits are bounded to 20
seconds. Known analysis failures retry at most three times, the same generation
request at most twice, and confirmed notification rejection at most three times.
Unknown analysis/generation outcomes look up the original identity first. The
legacy notification provider has no idempotency/lookup API: unknown delivery
requires manual verification and is never blindly resent.

Policies without `minimumImpact` retain the legacy first-20/100/five selection.
A switch toggle or old API client does not expand their scope. The management
page labels this compatibility state. Saving execution scope explicitly opts
into `minimumImpact: medium | high` (new form default: medium), stored in the
same configuration row; no environment variable or schema change is needed.

For opted-in policies, the scope is the previous Asia/Shanghai natural day
[00:00,24:00) plus unresolved older items. Run `started_at` determines the
occurrence date; analysis completion only gates evidence availability at the
frozen cutoff. Runs crossing midnight become eligible after completion; late
analyses trigger reevaluation by analysis revision. Each scan also rereads the
whole preceding natural day so a previously excluded today's run is not lost
behind the modification watermark. Historical unhandled evidence is retained.
Only each candidate's own dated diagnoses establish severity, never its issue's
all-time maximum. Unknown impact, unavailable evidence, stale baseline,
conflicts and oversized evidence remain individually recorded with reasons in
the round journal; they do not count as optimized.

Candidate enumeration pages through the entire historical pending inbox. A
round freezes baseline, source digest, item evidence digests, cutoff and all
batch item IDs in the existing auto_draft journal. Every AIS request remains
bounded by MAX_ITEMS=100 and the existing model-input byte contract (64 KiB
reserved for envelope overhead). Batches advance serially as revisions of one
task, with the exact previous validated candidate as parent and the same
original baseline. Prior item reservations persist. The Host checks that later
candidates preserve prior changed operations and runs full Pack checks.
Intermediate candidates cannot publish. One final review covers the full
baseline-to-final diff and all actually changed items; incomplete/failed chains
cannot fall back to an earlier candidate for publication. Review history is
paged and byte bounded independently of the trusted internal complete chain.

The round identity deduplicates the same scope/evidence/baseline within the
saved policy version. Competing Hosts share workflow locks and leases; no new
round is planned while one is progressing. A persisted automatic task awaiting
review, blocked, failed, generating or publishing also pauses new automatic
drafts for that workflow, even after its generation journal is done. A final
no-change batch still pauses new drafts if earlier changes remain reserved for
review. Manual tasks do not trigger this pause. Daily scans and analysis continue;
new evidence stays in the inbox, while the frozen draft is unchanged. Paused
planning does not checkpoint the evaluated scope, so after publication or
cancellation the next tick can reassess accumulated evidence against the active
baseline. Identical scope still follows the existing round deduplication. Disabled or changed policies stop
new dispatch. Requests of unknown outcome reconcile their stable key without
blind replay. A manual retry of confirmed generation failure persists a new
retry key and preserves the exact failed batch; unresolved conflicts and user
cancellation require review rather than automatic retry. Source/baseline drift
stops the round with an explicit reason and retains its frozen record.

Disabling automation stops new dispatches while preserving lookup of dispatched
work. Current Owner permissions and candidate revision are checked again before
notification. Draft notices require a reviewable successful revision and passed
static checks. Notice identities deduplicate by issue evidence or task/revision;
notification failure does not regenerate the draft. Links lead to the existing
workbench, which still authorizes review and publication independently.

Applied changes continue using the existing verification/recurrence lifecycle.
Unselected, not-needed, not-applied and explicitly failed changes remain pending.
Snoozes retain reason, deadline and evidence identity; expiry or materially new
high-impact evidence derives a new episode while preserving the original audit.

## Validation boundary

Service regressions use real SQLite journals and the existing repair/publication
services with controlled external ports. They cover duplicate Hosts, restart,
missed days, late runs, disable-before-dispatch, manual coexistence, conflicts,
partial analysis, notification recovery, and next-round selection after partial
publication. They do not prove production MySQL/OceanBase locking, external AIS
quality, provider delivery, or deployed behavior. Local browser acceptance covers the version comparison card against controlled
API fixtures; real AIS and OceanBase remain unexecuted integration boundaries. No production automation is
enabled by adding this code.

## Version provenance and comparison

See [workflow-version-provenance.md](workflow-version-provenance.md). The existing
success-trend card offers a version comparison tab, backed by
`GET /api/workflows/:id/version-comparison?baseline=N&current=M&days=7`.
The workflow view permission check runs before reading records. The injected
Host DB defines the same environment for both sides. Only actual releases are
selectable; drafts/edits are excluded. Default baseline comes from the trusted
publication record, otherwise the user chooses. Each exact deploy number is a
separate group, including repeated spec versions and rollbacks. Observation
windows have the same elapsed duration after each release, clipped to available
time (up to seven days by default). Success rate uses terminal samples only;
failed includes failed/aborted/cancelled/canceled, matching the existing overview.
Unknown deployment samples are counted separately. Zero samples produce null,
not 0%; differences are percentage points, and observations do not establish
causation. The task summary links the exact two deployment numbers.


After the complete automatic batch chain is validated, an authorized user can
submit human feedback as the next revision of the same task through the existing
manual revision API. The automatic origin remains a task presentation attribute;
subsequent human dispatches use the existing manual execution contract. A pending
human revision of an automatically created task still pauses new automatic tasks.
An identical, checked no-change feedback result retains the prior candidate and
its pending ownership. Incomplete automatic batches cannot be bypassed this way.
