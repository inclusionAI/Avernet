# Workflow issue aggregation v1

## Ownership and compatibility

ClawEvolve owns snapshots, selection and validation; Workflow owns presentation;
ClawMind invokes the existing analysis Bot in a separate synthesis phase after
single-run completion. No OCB-specific dependency or new model credential is used.
Deploy Avernet before ClawMind: `/analysis-runs/:id/input` advertises the optional
`issueAggregationSupported: true` capability. Older Bot versions ignore it;
new Bots skip synthesis against servers without it.

## Input selection

Only completed run analyses participate. Select the latest completed analysis
per flow (completion time, then analysis ID tie-break), including empty results,
before grouping by workflow ID and canonical failure signature. Every diagnosis
in that selected analysis is retained, including multiple diagnoses with the same
signature. Run-set scopes include their declared flow IDs even for empty results.
Legacy diagnoses are fallback only for runs without completed managed analyses.
History is scanned with database pagination, not a frontend 100-record sample.

A source binds an analysis ID, diagnosis ID, flow ID, diagnosis text, proposal and
evidence event IDs. Its deterministic source ID is a SHA-256 identity. Group input
digests include the full ordered source snapshot. Affected-run counts are distinct
flow counts, never model-produced or analysis-attempt counts.

## Service API (browser → ClawEvolve)

`GET /api/evolve/issue-groups?workflowId=...` requires view permission using the
same workflow access boundary as diagnoses. Missing workflow is 400; missing
identity is 401, forbidden workflow is 403. A load failure is 500, not empty data.
The response is `{groups: [...]}`. Each group has `workflowId`, `signature`,
`inputDigest`, `flowIds`, `sources`, `summarySources`, `summary`, `stale`,
`aggregationStatus` and `aggregationId`.

`summary` is either null or `{summary, causes, unknowns}`; each cause has `title`,
`conclusion`, `certainty` (`supported|hypothesis|unknown`) and `sourceIds`.
Every source must be covered; only IDs in the frozen input are accepted. Counts,
source run mappings and evidence references are server-derived, not model claims.
Model prose is a diagnosis, not proof of root cause or successful remediation.

## Signed Bot API (ClawMind → ClawEvolve)

These relative routes live under the existing signed internal Evolve router,
not the browser router. Existing linked-task Bot validation applies to both.

- `POST /analysis-runs/:id/aggregations`, body `{botId}`: requires a completed
  non-aggregation parent. Returns `{jobs:[{id,input}]}` for changed affected groups.
- `POST /analysis-runs/:id/aggregations/:aggregationId`, body `{botId,result}`
  or `{botId,failed:true}`: validates parent binding, references, coverage and CAS.
  Returns `{ok:true}`. Identical completed writes are idempotent; invalid writes
  are rejected. Bot mismatch is 403; missing parent is 404; rejected input is 400.

## Persistence and recovery

Snapshots reuse `workflow_evolution_analysis_runs` under the separate scope
`issue_aggregate` and analysis version `workflow-issue-summary/v1`. They have no
flow/task binding of their own and are excluded from run diagnosis/history
projection. No database schema change is required.

Input is frozen in `scope_json`; model results are separately stored in
`result_json`. Unique request keys deduplicate concurrent preparation. Completed
inputs are reused. An old completion cannot replace a newer snapshot: the browser
compares input digests and explicitly labels retained old results stale.
Abandoned jobs expire after ten minutes and can be retried on subsequent analysis.
Each Bot model call is bounded to 120 seconds; new group calls stop after a
180-second batch budget (an in-flight call can extend the batch up to 300 seconds).
Failure to synthesize never rolls back an already saved single-run diagnosis.

Groups exceeding 500 source diagnoses or 180,000 UTF-8 input bytes report
`too_large`; they are never silently sampled. Hierarchical synthesis is not in v1.
Historical groups are not automatically backfilled merely by opening a page.
Run analysis triggers affected groups; reanalyzing an associated run refreshes
them. There is no separate browser model-dispatch or bulk backfill endpoint in v1.
Refresh selection includes every declared `scope.flowIds` entry, even when a
completed run-set result contains no diagnosis for that flow. Existing summaries
that reference removed flows are regenerated if other runs keep the group alive.

## Suggestions

Aggregation never creates, applies or verifies a suggestion. Source proposals
remain linked to their source diagnoses. The existing executable-suggestion table
has one entry per workflow/signature; if a single result contains distinct
proposals for one signature, preserve them in the immutable diagnosis result but
do not overwrite an executable suggestion with either one. UI calls them candidate
suggestions, not executable group actions. Existing suggestion lifecycle remains
independent and does not mark all aggregate causes resolved. Supporting multiple
independently executable suggestions for a signature requires a separate schema
and lifecycle migration; this change does not perform that migration.
If a signature no longer has a current diagnosis group, its independently stored
suggestion remains visible in a separate lifecycle section with the existing
apply/verify controls and task progress. It is not counted as a current issue or
treated as verified merely because the latest analysis removed its diagnosis.

## Verification

`issue-aggregation.test.ts`: latest replacement (including empty run sets),
distinct causes, stable inputs and invalid/missing references.
`evolve-knowledge.test.ts`: real SQLite persistence, HTTP permission isolation,
Bot binding, cached results, stale snapshots and expired-job recovery.
Workflow `IssueSummary` and `EvolutionIssueFlow` tests: cause/source display,
failure states and existing run navigation/actions.
ClawMind routing/helper tests: two model phases and single-analysis preservation.
