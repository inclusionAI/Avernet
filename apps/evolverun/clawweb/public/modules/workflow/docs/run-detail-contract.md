# Run detail HTTP contract

Owner: `@avernet/workflow`. `GET /runs/:flowId` relative to the host API mount
(normally `GET /api/runs/:flowId`) is a read-only Service API for the workbench
and other authorized HTTP clients, not an engine Plugin API.

A successful response keeps the existing `{ run, nodes, availableInterventions }`
envelope. For launch-input display semantics, optional lineage presentation,
and authorized lineage projection, see [run summary](run-summary-contract.md).

## Authorized lineage — read behavior v2 (2026-11-26)

This additive revision supersedes blanket lineage omission **only** for
reader-backed, authorized ordinary detail. It adds no endpoint, opt-in flag,
header, query parameter, or wire-version field. The safe base projection still
strips `parent_flow_id` and `root_flow_id`; the detail service may add them back
only when an inspection exists and is neither managed, restricted, nor a
sensitive fanout child.

Each candidate comes exclusively from that inspected run's stored column.
For each valid non-null value, the service independently calls the existing
`RunSubworkflowsReader.inspectRun` with the same actor and requested ancestor
ID. It returns the ID only if the resulting `run.flow_id` exactly matches that
ID, including case and Unicode. The ancestor's own workflow and Bot/owner
permissions apply, even when it shares the child's workflow. Equal parent/root
IDs share one lookup per response; ancestor lineage is not recursively traversed.

- Explicit SQL `NULL` remains `null` on eligible detail. Absent columns or
  invalid stored IDs are omitted, not inferred or repaired. Valid IDs follow
  the [stored lineage contract](flow-run-lineage.md) and are returned unchanged.
- Only `FanoutReadError` with `forbidden` or `not_found` omits the corresponding
  ancestor field. Other authorization, invalid-state, unavailable, and storage
  failures propagate to the existing HTTP error handler; they must not produce
  a successful detail response with silently missing lineage.
- Readerless/default detail, managed or restricted fanout detail, sensitive
  children, and all list projections retain lineage redaction. A reader-backed
  public mount is eligible without the separate runtime-metadata option.
- The reader selects no credentials, performs no engine/internal HTTP fetch,
  and never uses client-provided parameters as ancestor IDs. Existing
  `Cache-Control: private, no-store` applies to reader-backed responses.

A returned ID or rendered link is descriptive metadata, **not a permission
grant or proof of ancestry**. Destination navigation must authorize the actor
again; access can be revoked between reads.

`server/routes/__tests__/run-detail-launch-input.test.ts` exercises real
`RunSubworkflowsService`/repository HTTP reads against migrated SQLite and
legacy column layouts, including independent ancestor denials, exact identity,
credential exclusion, and error propagation. Focused service and repository
suites are `run-launch-lineage.test.ts` and `run-subworkflows-lineage.test.ts`.

## Runtime metadata

Runtime metadata is off by default and all three fields are omitted
on the standalone public mount. A host may set `exposeRuntimeMetadata: true`
in the 12th and final `createRunsRouter` argument, after the existing 11th
`runSubviews` argument (`null` when no reader is supplied). A host may opt in
only when it mounts verified run-view authorization before the router. That
guard must verify the caller's server-side identity and enforce the requested
run's per-workflow Bot/owner access. The option itself does not authorize requests.

For ordinary run detail on an opted-in mount, the router adds these flat,
required `RunRuntimeMetadata` fields to `run`:

| Response field | Stored run field | Type |
| --- | --- | --- |
| `device_id` | `CLIENT_ID` | `string \| null` |
| `bot_type` | `BOT_TYPE` | `string \| null` |
| `stage` | `STAGE` | `string \| null` |

Values come only from the requested run's stored credentials JSON object. Each
lookup uses an exact, case-sensitive, own top-level key. Nonblank strings are
returned as stored. Missing, null, blank, whitespace-only, or non-string values
return `null` independently. Missing, malformed, or non-object JSON (including
arrays) returns `null` for all three fields. No values are inferred or defaulted.
The raw `credentials_json` and all other values from it are not returned or
logged by this projection. The subworkflow reader does not select credentials;
its ordinary run details therefore receive `null` values if opted in, with no
additional credential read. Managed fanout parents and sensitive fanout
children keep their existing reader-backed redaction, including omission of
all three fields even on an opted-in mount.

Without the reader, only the new metadata is withheld when the stored state
object has either exact own top-level marker (`subworkflowFanouts` or
`fanoutOrigin`), regardless of the marker value. Malformed or non-object
state also withholds the metadata; a SQL `NULL` or empty state column counts as
absent. The existing readerless response fields and status are unchanged.

## Compatibility

Runtime metadata fields are additive and detail-only on opted-in mounts.
Authorized lineage v2 is separately additive and ordinary-reader-detail-only;
it does not require the runtime-metadata opt-in. Readerless/default detail and
the [run list](run-list-contract.md) are unchanged.
Where already present, `run.hasCredentials` still reports whether the stored
column is neither null nor the empty string; it does not indicate valid JSON
or the presence of metadata. No storage or deployment migration is required.
Clients should treat absent metadata fields the same as `null`.

`server/routes/__tests__/run-detail-runtime-metadata.test.ts` exercises both
mount modes through Express and the current migrated SQLite schema.

## Run scope and session updates

A guarded host may pass its authorized workflow and originating Bot snapshot
to the session PATCH handler via request-local server state. The write is then
conditional on both values still matching. Requests whose scope no longer
matches receive a denial; clients cannot supply the expected snapshot. Mounts
without a server-provided snapshot keep their existing session behavior.

## Workbench approval-node details

Expanding an approval node in the run's node list also displays that node's
approval records above the generic execution inputs and outputs. This is a
read-only projection of the existing approval APIs; it does not add fields to
`GET /api/runs/:flowId` or change approval permissions.

- Records come from `GET /api/approval/by-flow/:flowId`, filtered by the exact
  node ID, including pending, approved, and rejected cards. Multiple records
  are shown newest first, with the card ID breaking equal creation times.
  Cards have no execution-attempt identifier, so these are node-level records,
  not a per-attempt audit trail.
- **审批人** uses the persisted handler IDs and positionally paired names,
  falling back to an ID for a missing or blank name. Per-handler decision
  markers use `approvedBy` and `rejectedBy`.
- **审批状态** comes from the approval card, never the node execution status.
  A successful node can contain a rejected approval.
- **审批时间** uses the card's `resolvedAt` final-resolution timestamp. It is
  not a per-handler action time and must not fall back to the node completion
  or card creation time. Unresolved cards have no final approval time.
- **审批意见** comes from `GET /api/approval/:id`, since the list omits it.
  Plain comments and structured `{ note, detail }` comments are supported;
  the serialized structured envelope is not shown as prose when its note is
  empty. The stored opinion is card-level, not a history of each handler's
  comments. Loading or failure is shown explicitly rather than as an absent
  comment.
- **审批链接** opens the existing same-origin `/approval/:id` page without
  adding identity or authorization query parameters. Read-only child views
  retain all five field labels and approval detail reads, but replace actionable
  approval navigation with plain explanatory text, matching `ApprovalPanel`.
- A missing record is displayed as unavailable approval data, not inferred
  approval. Fetch failures expose a retry, and pending records refresh while
  their details are visible. Collapsed and non-approval nodes do not request
  per-card detail.

Regression coverage lives in
`web/components/__tests__/ApprovalNodeDetails.test.tsx` and
`web/components/__tests__/NodeExecutionListApproval.test.tsx`.

## Workbench run archive presentation

The archive panel is a read-only presentation of the existing run-archive
response. Every existing authorized run can request its archive, regardless of
runtime status (including active, successful, skipped, and unknown statuses).
The frontend does not use run status as an archive-availability gate; loading,
errors, partial data, and empty data retain their actual collection meaning.
It does not introduce an endpoint, analysis action, approval decision, patch
application, or deployment operation. Its five sections are overview,
process, error details, AI diagnosis, and suggested YAML. Navigation and detail
share the main row; archive metadata occupies a separate footer below it.

- Run status and archive aggregation status are independent. A `completed`
  archive is not a successful run; `partial` archives retain their collection
  errors. Execution records are counted as collected records, not inferred
  workflow nodes. Unknown or absent node status is not counted as success.
- An empty failed-node list means no failed execution was collected, not that
  the run or its business outcome succeeded. Missing analysis, skipped or failed
  analysis, and an analysis with no diagnoses are distinct unavailable/empty
  states, not proof that no repair is needed.
- Missing suggested YAML does not mean the workflow requires no changes. A
  supplied YAML document or patch proposal is only a preview. The patch view
  shows supplied operations; it does not fabricate a before/after YAML diff
  without the original document. Copy and download operate locally and report
  failure without claiming that a patch was applied.
- The run detail has one logs entry: **运行档案 → 运行过程 → 运行日志**,
  using the archive response's `runLogs`. The redundant top-level **日志** tab
  is removed, not the shared timeline component or its other consumers. The
  archive is not a distinct live stream or an immutable snapshot contract.
- Active runs (`pending`, `queued`, `running`, `waiting`, `blocked`, and
  `postActionsRunning`) explicitly describe their archive as currently collected
  evidence that may change. A manual read-only refresh uses the existing query,
  keeps the selected section, prevents repeated clicks while fetching, and
  retains previous data with an error on refresh failure. No automatic analysis
  or mutation is triggered.
- Missing or invalid timestamps and durations render as unavailable rather than
  invalid dates; a recorded zero duration remains zero. Empty collections and
  filters with no matches provide an explanation instead of a blank pane.

Regression coverage lives in `web/components/__tests__/RunArchivePanel*.test.tsx`.


## Workbench run-detail layout and node location

The main detail keeps the complete run summary, followed by an always-visible
DAG above intervention, approval records, and evolution analysis. The remaining
bottom views are **节点** and **运行档案**. Intervention/session content, approval
cards and their existing detail/actions, analysis progress/results/actions,
node details, and subworkflow views retain their original data and permission
conditions. The top approval action does not replace the middle approval cards:
records remain visible whenever the existing approval query returns them,
including processed approvals in completed runs.

A failed live workflow-definition read shows an explicit error and a read-only
retry, not a permanent loading indicator or a topology inferred from executions.

DAG activation switches to **节点**, expands the matching execution record, and
requests focus, centered scrolling, and visual emphasis scoped to that list
instance. Repeated activation of the same node locates it again without
collapsing it. Ordinary row expansion does not trigger a DAG scroll request.
Keyboard focus within the DAG must bring an off-canvas node into view without
changing the user's zoom or activating the node; Enter/Space activate it once.
A definition node without a collected execution record produces an explicit
unavailable message, not an invented record or input/output. Parent and nested
child lists with equal node IDs must never target one another.

Historical child DAGs retain the trusted run-snapshot boundary: unavailable,
denied, or revalidating snapshots do not fall back to the current mutable
workflow definition. All child read-only protections remain in force.

Layout and integration regressions live in
`web/pages/__tests__/RunDetailLayout.test.tsx`,
`web/pages/__tests__/RunDetailHistoricalDag.test.tsx`, and the corresponding
DAG/node-location component tests. No HTTP schema or storage change is required.
