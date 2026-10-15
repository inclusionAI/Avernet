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

Draft selection considers the first 20 priority groups, at most 100 proposals,
five selected items, and 100 KB of selected input. Candidates require diagnoses,
validated structured patches, and an exact current baseline digest. Ambiguous
conflicts remain manual. Each due day plans at most one frozen scope; its stable
fingerprint prevents the same scope/evidence/baseline from regenerating on later
days. Existing tasks retain their frozen items, version, and evidence cutoff.

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
quality, provider delivery, or deployed behavior. Full browser acceptance of the
second-batch journey remains a release prerequisite. No production automation is
enabled by adding this code.
