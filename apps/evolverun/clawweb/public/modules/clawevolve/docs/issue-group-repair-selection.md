# Issue-group repair selection

## Approved product direction

Replace the fixed suggestion as the primary group action with a selected repair
scope. The overview is for human understanding, not an executable merged patch.
Default to all deduplicated candidate proposals from the latest completed analysis
of each run. Users can exclude candidates, select multiple candidates, and add
instructions. Switching the inspected run must not change selection. No separate
model-based plan-generation workflow is introduced.

## Existing boundary requiring change

`suggestions/apply-batch` consumes persisted suggestion IDs, not diagnosis source
IDs. Suggestions are unique per workflow/signature, while issue-group sources can
contain several different proposals for the same signature. The existing callback
settles those persisted suggestions together. Substituting candidate source IDs or
silently overwriting the one suggestion would lose provenance and misreport partial
repairs. Keep the existing single/batch suggestion APIs and lifecycle unchanged.

## Design

### Candidate selection and overview

- Rename the human-facing aggregate section to 建议总览, retaining cause, evidence,
  uncertainty, stale-state and last-valid-summary behavior. It is a diagnosis and
  direction overview; do not imply it is a synthesized executable proposal.
- Derive candidates from current issue-group sources with valid nonempty proposals.
  Deduplicate structurally equal proposals using canonical data, not fuzzy summary
  text; retain all source references for each candidate.
- No proposal means no selectable repair item. Show such diagnoses as not covered
  rather than pretending they have an executable recommendation.
- Select all initial candidates by default. Selection is keyed to group and
  candidate identity, independently of the selected run. On refreshed source data,
  keep surviving selections, remove expired ones, and require acknowledgement of
  new candidates instead of silently expanding the confirmed scope.
- Show 已选 N / 共 M and a final preview of selected items, exclusions and user
  instructions. Disable submit for zero selections. Move existing suggestion
  lifecycle actions to a clearly separate historical area; retain active tasks.

### Dispatch and authorization

Add a group-repair entrypoint in the ClawEvolve owner module accepting workflowId,
signature, inputDigest, candidate source references, Bot selection and instructions.
Resolve the complete proposal/diagnosis/evidence data on the server; do not trust
browser-supplied proposal bodies or summaries. Verify caller edit permissions,
actual Bot edit permissions/runtime, workflow/group membership, current latest-run
sources and the input digest. Return a conflict if the group changed after selection.
Reject unknown, duplicate-invalid or cross-group references, excessive selection or
payload size explicitly. Never truncate selected repair scope silently.

Freeze selected candidates, source provenance, exclusions and group digest in the
existing task config. Use a distinguishable group-repair mode with no fabricated
persisted suggestion IDs. Reuse dispatch, claim-token/Bot binding and task progress
infrastructure, but isolate finalization from legacy suggestion status updates.
Guard concurrent group applications and make completion retries idempotent.

### Bot application

Extend the existing ClawMind application orchestrator with selected-candidate input.
The same application Agent reads current YAML and handles selected intentions as a
whole. Original patches are reference candidates, not instructions to concatenate
or execute sequentially. Restrict changes to the approved selection and user scope.
If alternatives conflict and cannot be resolved from facts, report the conflict and
pause without deploying an arbitrary choice. Do not force uncertain advice to pass.

Return exactly one result per selected candidate: applied, not_needed, or unresolved,
with an explanation. Reject missing/unknown/duplicate result identifiers. Include
workflow revision and existing change/deployment proof. Global success or a prose
claim is not evidence that every selected item was processed.

### Persistence and verification

Keep per-item application outcomes in the group task output. Mixed outcomes remain
visible as partial handling, not whole-group repair success. Application evidence
does not establish effectiveness. Keep later effect verification separate; neither
successful deployment nor group-task completion auto-verifies any issue or marks
unselected/legacy suggestions as applied. Preserve the last valid aggregate when
aggregation fails; this operation must not overwrite analysis history or proposals.

### Rollout

Expose group-repair capability explicitly. New UI must not fall back to applying the
one old suggestion when the group endpoint or Bot capability is missing. Existing
single suggestion workflows stay available under their explicit legacy actions.
Update Avernet server/UI and ClawMind together; OCB composition only needs release
revision changes if required by deployment, not copied business source.

## Implementation sequence and verification

1. Pure candidate normalization/deduplication plus UI selection tests: all selected,
   exclusions, no-proposal diagnostics, run switching, refresh and zero selection.
2. Route/task contract tests: authentication, Bot permissions, source membership,
   stale digest, frozen selected scope, no silent truncation and concurrent submit.
3. ClawMind orchestrator tests: selected-only inputs, conflict handling, exact
   per-candidate outcomes, partial handling and no deployment on unresolved conflict.
4. Callback tests: Bot/claim binding, idempotency, missing outcomes, late callbacks,
   no legacy or unselected suggestion status mutation, no automatic verification.
5. Page tests for task progress/partial outcome and retained historical controls;
   affected owner-package checks/builds plus a live selected-subset validation before
   claiming deployed behavior. Do not submit configs/application.yaml.

## Review status

Product direction and JSON-only persistence confirmed in conversation. Execute
inline, without subagents. Scope excludes configs/application.yaml.

## Execution checklist

## HTTP contract and compatibility

- `GET /api/evolve/group-repairs?workflowId=...&signature=...` requires login and
  workflow edit permission. Returns capability `issue-group-repair/v1`, current
  `inputDigest`, deduplicated `candidates`, uncovered diagnosis count, eligible
  explicitly authorized Bots, and the group's tasks among the latest 50 workflow
  group-repair tasks. Task responses whitelist selection, progress and outcomes;
  claim credentials are never included.
- `POST /api/evolve/group-repairs` accepts `workflowId`, `signature`, `inputDigest`,
  `candidateIds` (1–20 unique IDs), `botId`, `botEnv`, optional `applicationSpec`.
  The server resolves candidates from each run's latest completed analysis; client
  proposals are never trusted. Missing/changed groups, unknown IDs and active group
  repair tasks return 409. Permission failures return 401/403. The frozen task JSON
  is limited to 60 KB to fit existing TEXT columns without a migration; oversize
  selections are rejected, never silently truncated. Reduce scope/instructions.
- The existing signed claim requires capability `issue-group-repair/v1` only for
  these new tasks. Old plugins receive 409. Legacy single/batch applications keep
  their contract. Generic retry is rejected for group repairs: reconfirm a new scope.
- Signed reports require exactly one `{id,status,reason}` per selected candidate,
  with status `applied`, `not_needed` or `unresolved`. Missing/foreign/duplicate
  outcomes and unresolved results claiming deployment return 400. Structured
  unresolved outcomes settle the task as failed while retaining item results.
  Outputs over 50 KB are rejected to preserve TEXT storage and progress headroom.
  Transport/model failures may report failure without outcomes (no success inferred).
- Historical suggestion IDs, lifecycle rows, aggregation summaries and effectiveness
  records are not mutated by a group repair. Bot application is not verification.

### Implementation checks

- [x] Add candidate selection and outcome validators in `server/services/evolution/group-repair.ts`,
  with pure tests for digest, membership, deduplication and complete outcome coverage.
- [x] Extend dispatch with an internal group scope argument (not arbitrary browser
  input), persist no legacy suggestion IDs, enforce one active group task inside the
  task-creation transaction and validate group results at the callback boundary.
- [x] Extend ClawMind `suggestion-apply-orchestrator.ts` with `repairSelection`,
  selected-only prompt input and per-item result validation; include item outcomes in
  the existing authenticated report. Test mixed, missing and conflicting outcomes.
- [x] Add `GroupRepairSelection.tsx`, independently keyed by problem group, plus
  selection/refresh tests; show original proposals, Bot choice, instructions, and
  durable group task progress/results. Preserve explicit legacy lifecycle controls.
- [x] Run affected route, orchestrator, UI tests and package type checks. Record
  dependency blockers separately from code regressions. No deployment claim.

Validation: 11 server, 9 UI and 10 plugin focused tests passed. Owner-package
TypeScript checks passed after building shared/clawevolve dependencies. Real Bot
execution and deployed UI acceptance remain deployment-time checks; no online
success or effectiveness is inferred from local tests.
