# Flow run lineage: trusted engine registration

Owner: `@avernet/workflow`; schema owner: `@avernet/clawweb-shared`.
The engine-facing Service API is mounted under the host's existing trusted
engine authentication at `/api/internal/runs`. These fields are descriptive
creation metadata, not permission grants, execution controls, or proof of
ancestry. Existing parent-related registration and ordinary-request
association retain their own authority checks.

## Stored fields and wire format

| `flow_runs` / HTTP field | Repository insert field | Meaning |
| --- | --- | --- |
| `parent_flow_id` | `parentFlowId` | Direct parent run's actual flow ID, or `null` when absent/unknown |
| `root_flow_id` | `rootFlowId` | Root ancestor run's actual flow ID, or `null` when absent/unknown |

`POST /api/internal/runs` accepts both as optional nullable strings, each at
most 255 Unicode code points. Non-null values must not be blank. Invalid types,
blank values, overlong values, and malformed Unicode (unpaired UTF-16
surrogates) are rejected with HTTP 400 before insertion;
values are not trimmed, truncated, or normalized. Omitted values become SQL
`NULL`, preserving old engine callers.

```json
{
  "flow_id": "child-run",
  "workflow_id": "child-workflow",
  "status": "running",
  "parent_flow_id": "direct-parent-run",
  "root_flow_id": "root-run"
}
```

ClawMind supplies trusted runtime lineage when creating a run:

- A top-level run has both fields `null`; it does not name itself as a root.
- A first-level child has parent and root equal to the top-level run ID.
- A deeper child has its direct parent ID and its known root ancestor ID.
- A historical parent may have unknown root ancestry. A known parent with a
  `null` root stays unknown; neither storage nor callbacks invent a root.

The server stores the explicitly supplied values. It does not fill them from
`params_json`, `input_json`, `state_json.subworkflowMeta`, a trial request's
`run_request_parent_flow_id`, `ordinary_parent_flow_id`, or `flow_run_links`.
Those other protocols are not substitutes for these columns.

## Persistence, reads, and replay

The two values are inserted in the same transaction as the run. Internal run
creation readback, detail, and the legacy internal run list expose the stored
snake-case fields, including explicit `null` for an unknown value. Completion,
state/status updates, cancellation, recovery, and retry do not rewrite them.
The fields are not an instruction to mutate or traverse the parent.

An idempotent registration must not silently accept conflicting lineage or
replace the original values. Exact replays remain valid after mutable child
execution state has advanced. These guarantees do not turn the legacy plain
run-creation endpoint into a general upsert API.

## Public reads — read behavior v2 (2026-11-26)

The public [detail contract](run-detail-contract.md) adds lineage only to
reader-backed authorized ordinary detail: an inspection must exist and must
not be managed, restricted, or a sensitive fanout child. The base safe
projection still redacts both columns. The additional domain projection reads
candidates only from that inspection's stored columns, then independently
calls the existing reader's `inspectRun(actor, id)` for each valid non-null
ancestor ID. Its returned `run.flow_id` must exactly equal the requested ID.
Equal parent/root IDs share one lookup; ancestors are not recursively traversed.

Access to a child does not establish ancestor access, even within the same
workflow: each ancestor's workflow and Bot/owner rules still apply. Only
forbidden/not-found ancestors are omitted independently. All other read or
authorization failures reach the existing HTTP error handler, not a false
successful response. Explicit nulls stay null; absent/invalid fields are
omitted. Valid strings are returned unchanged, never taken from client
parameters, state, or launch input.

Readerless/default detail, fanout managed/restricted/sensitive views, and
[run lists](run-list-contract.md) retain redaction. There is no client opt-in,
credential selection, or engine/internal fetch bypass. This is an additive
read-contract revision, not a new endpoint, wire-version field, registration
change, or permission grant. A displayed parent/root link must undergo the
destination's authorization again and is not proof of ancestry. Optional-column
compatibility permits legacy public reads without fabricating values; it does
not relax engine registration or schema-readiness requirements below.

## HTTP callbacks

The matching ClawMind change reads these stored columns into
`ext_info.flow_runs.parent_flow_id` and `ext_info.flow_runs.root_flow_id`.
They are not duplicated at callback top level. Unknown values serialize as
`null`; a missing run row remains `ext_info.flow_runs: null`. Node-output
inclusion does not alter lineage. Avernet does not synthesize this engine
callback payload from user parameters.

## Migration and deployment

Shared schema migration **v157** adds two nullable `VARCHAR(255)` columns with
`DEFAULT NULL`. Versions 155 and 156 remain retired no-DDL AIStudio ledger
slots. No historical migration is rewritten, and no existing lineage is
backfilled from JSON or relationship tables. There are no new indexes.

- Local SQLite uses the normal migration runner. The migration handles each
  column independently, including databases where one or both columns were
  provisioned earlier, without replacing existing values.
- Managed MySQL/OceanBase must be provisioned through the database release
  process when runtime DDL is denied. The manual DDL is
  [`scripts/sql/flow-run-lineage-v157.mysql.sql`](../../../../scripts/sql/flow-run-lineage-v157.mysql.sql).
  If the two columns have already been added, do not rerun their `ADD COLUMN`
  statements blindly. Verify the actual column definitions; the required
  width is 255, both must allow `NULL`, and their default must be `NULL`.
  Do not move the migration ledger cursor past earlier unapplied migrations.
- External-schema startup and managed/deferred migration paths verify lineage
  compatibility without issuing lineage DDL. Missing or incompatible columns
  fail readiness rather than returning a database that cannot serve old callers.

Deploy schema and the Avernet workflow service first, rebuild the consuming
host against that Avernet revision, then update ClawMind. DDL alone does not
provide API support, and a source change alone does not prove deployment.

ClawMind confirms non-null lineage by comparing the creation response with the
requested values. An old service that ignores them fails this check. A failed
readback/confirmation happens after a possible insertion: inspect the same
`flow_id` rather than assuming no row exists or starting another run blindly.

Rollback is application-first: old engines may omit the new fields; retain the
nullable columns and their stored values. Do not drop columns or rewrite the
migration ledger as part of a code rollback. Verification on temporary SQLite
and SQL/dialect doubles does not replace target MySQL/OceanBase or deployed
host validation.

## Local verification

From `apps/evolverun/clawweb` with a supported Node.js runtime:

```sh
npm run ci:build
npm run ci:check
npm test --workspace @avernet/clawweb-shared -- --maxWorkers=2 --minWorkers=1
npm test --workspace @avernet/workflow -- --maxWorkers=2 --minWorkers=1
```

Build first so package self-imports resolve current artifacts rather than stale
`dist-server` output. The focused suites are `shared/server/__tests__/flow-run-lineage-migration.test.ts`,
`modules/workflow/server/repositories/__tests__/flow-run-lineage.test.ts`, and
`modules/workflow/server/routes/internal/__tests__/flow-run-lineage*.test.ts`
(relative to `public/`). They cover pre-applied schema, null/exact-value
round trips, invalid input, lifecycle immutability, and registration replay.
Public list and readerless/fanout detail tests pin omission of ancestor IDs;
`server/routes/__tests__/run-detail-launch-input.test.ts` adds real SQLite and
reader-backed HTTP coverage for authorized ordinary detail, exact values,
legacy optional columns, ancestor denials, and failure propagation. Focused
helper/repository tests pin deduplication and missing-column error handling.
The host's existing authentication and authorization boundaries remain necessary.
