# Internal workflow version contract

These routes are mounted below `/api/internal` and use the host's internal API authentication. They are consumed by ClawMind; they do not introduce a separate publishing model.

## Deployment snapshots

`GET /deploy-history/:workflowId/active` and `GET /deploy-history/:workflowId/versions/:version/snapshot` return a flat camelCase object, not a nested `record`:

```json
{
  "found": true,
  "workflowId": "example",
  "packId": "example-pack",
  "version": 2,
  "deployNumber": 5,
  "tagName": "deploy/example/#5",
  "action": "deploy",
  "specJson": "{\"id\":\"example\"}",
  "gmtCreate": 1700000000
}
```

The example shows the response envelope only; `specJson` contains the complete workflow spec. `version` is the deployment version, independently of any version declared inside the spec. `packId` identifies the historical snapshot's pack and allows callers to check an explicit pack pin. Both routes return `{ "found": false }` for absent snapshots. Non-success HTTP responses represent request/service failures, not absence.

## Fanout snapshot metadata is not a version selector

YAML top-level `version` (`childWorkflow.version` and `childResolved.spec.version`)
is business metadata, not an execution or deployment selector. Fanout persistence
imposes no type/value constraint on this field beyond the existing JSON snapshot
boundary: strings such as `'1'` or `'1.2.3'`, numbers, omission and `null` are
accepted without coercion. The existing `normalizeWorkflowSpec` producer converts
numeric versions to strings and defaults an omitted version to `'1'`; fanout must
accept that output without running normalization again or changing its spec/digest.

Keep the following version domains separate:

- `executor.version` selects a child workflow under the runtime's existing
  contract. For the default case with an already published deployment, an omitted
  selector uses the normal run's **latest** default: the currently active deployment
  in `workflow_deploy_history` with `is_active = 1`. Resolution takes that record's
  `version`, `deployNumber` and `tagName` (DB columns `version`, `deploy_number` and
  `tag_name`) and reuses normal tag materialization. It does **not** select
  `MAX(version)`, parse YAML metadata or copy a parent's version. This describes
  the existing published-deployment default, not a new fanout-specific `activeOnly`
  mode or fallback rule; behavior when no published deployment is available stays
  under the normal runtime's existing contract. Explicit selectors retain their
  existing validation and resolution rules.
- `deployVersion` / deployment-history `version` identifies a platform deployment,
  independently of YAML metadata. Resolved `workflowVersion` and `deployNumber`
  retain their existing metadata contracts; fanout storage does not resolve latest.
- Group `revision` is the durable CAS counter: creation uses revision `0` with
  `expectedRevision: null`; updates advance exactly one and stale writes conflict.
- Legacy `childPublishedVersion`, when supplied, still must be a positive safe
  integer. It is not derived from `childWorkflow.version`; release identity and
  digest checks remain unchanged.

The complete `childResolved.spec` must still match `childWorkflow`, including the
original metadata type and presence. After creation both snapshots remain frozen:
changing `'1'` to `1`, deleting `version`, or changing any other snapshot field is
not permitted even with a valid next revision. ID/executor structure, credential
safety, snapshot consistency and revision CAS checks are unchanged. This storage
compatibility rule does not change ClawMind parsing or ordinary explicit-version
contracts, nor relax legacy pinned dependency-lock validation.

## Run records

`POST /runs` accepts optional `workflow_version` and `workflow_deploy_number` fields alongside the existing required `flow_id`, `workflow_id`, and `status`. The route preserves both values in `flow_runs` and its response. Values must be positive safe integers within the signed 32-bit range or null, except that `workflow_version` also accepts `-1` (ClawMind's `DRAFT_VERSION`). This sentinel means this run is not bound to a published release, including Git remote snapshot runs; it does not mean the workflow has never been deployed. Do not replace it with the workflow's latest release number. `workflow_deploy_number` does not accept `-1`. Invalid values return HTTP 400 before insertion. Omitted fields remain null for older clients and unversioned runs. No schema migration is needed; deploy this API compatibility change to accept snapshot runs from current plugins.

These metadata changes are additive and require no schema migration: the two run
columns already exist. For metadata alone the backend must understand the fields
before the client uses them. **This is not permission to deploy the entire server
first:** this version also removes the former 4505 timeout guards. First pause or
isolate faulty old writers, then deploy the matching server/schema contract,
upgrade and validate Mind and legacy TaskGuard, and only then restore writes.
If isolation is impossible, do not deploy this check-removal server independently.
Nginx emergency filtering remains off by default; no built-in runtime feature
flag is introduced. Follow the [release gate](../../../../docs/workflow-timeout-write-policy.md#release-gate-and-rollback).

## Restart recovery retry compatibility

Execution generations are part of the terminal-closeout contract. Current ClawMind and
TaskGuard coordinate manual retries in their local execution state, freeze the original
attempt's next epoch, and call `PUT /runs/:flowId/retry-resume` with
`retry_epoch = original epoch + 1` before executing that attempt. The SQL admission
requires the preceding stored epoch atomically, preserves `started_at` and node history,
and clears `completed_at` only while advancing the epoch. A lost response can replay the
same running epoch without rewriting it; skipped/older epochs conflict with HTTP 409.
A read of the latest shared epoch is **not** permission to authorize a stale retry.

A state-bearing retry may include optional serialized-object `state_json`. Its
checkpoint `retryEpoch` (TaskGuard) or `recoveryEpoch` (Mind) must equal the
requested next epoch. At least one must be present; if both are present they must
agree, and any bad alias is rejected rather than falling back; a supplied
`workflowId` must match the run. The same transaction and single parent-row UPDATE
advance the epoch and persist canonical state and any existing
`params_json._clawmind_state`, preserving the embedded string/object form and
unrelated params. Invalid supplied JSON/identity/epoch is 400; contradictory saved
canonical/embedded identity, epoch or content is 409; database failures are 500
with rollback. No partial epoch advancement or unfenced `/state` fallback is
allowed. A running same-epoch replay is read-only and never rewinds a later
checkpoint. Omitting `state_json` keeps epoch-only compatibility behavior unchanged;
state-bearing Mind/TaskGuard clients include their checkpoint for atomic sync. Fanout origin and authoritative groups stay protected; managed-child
checkpoints still require revision CAS. Full [retry checkpoint semantics](../../../../docs/workflow-timeout-write-policy.md#retry-checkpoint-is-part-of-admission-not-a-later-mirror)
include saved-state compatibility and replay validation.

The default retry target is failed/blocked/waiting. Both engines' actual `handleRetry`
contracts also allow an explicit node ID to rerun a succeeded segment. Therefore an
explicit, locally authorized `retry-resume` may reopen a succeeded run **in a new
generation**; it is not a generic success-overwrite permission. Cancelled/canceled runs
cannot resume, including ordinary case/space variants. New business after cancellation
requires a new run. Ordinary `/status` cannot reopen any terminal row, including epoch
NULL/0. It is only a matching-epoch nonterminal status mirror.

State/phase mirrors also require the original generation: `/state` accepts
`{ state_json, retry_epoch? }` and `/phase` accepts `{ phase, retry_epoch? }`.
Omission means zero only. Positive state checkpoints must carry matching explicit
`retryEpoch`/`recoveryEpoch` aliases. A terminal mirror can only acknowledge the
same saved value read-only in the matching generation; changed values or wrong
epochs conflict. State mirrors synchronize any existing embedded checkpoint with
the trusted canonical fanout merge in one transaction, so progress does not make
a later retry fail canonical/embedded consistency. Do not reread the latest epoch
to authorize a stale mirror or downgrade failed retry admission to `/state`.

`retry-failed` is deprecated and read-only: an existing run returns HTTP 409 directing
the caller to coordinated `retry-resume`, a missing run returns 404, no repository
returns 503, and a database read failure returns 500. Neither nodes nor run fields are
reset. There is no epochless retry mutation fallback. `result-json` and `started-at`
are also deprecated at both routes and direct repositories: valid existing-run
requests409, missing404, noDB503, read failures500, no UPDATE/observer. Direct
methods return false after read-only lookup and propagate read errors. Only
completion owns final result, and retry-resume preserves original started_at.
Remove any engine retry dependency on resetStartedAt; do not restore the old
endpoint to reset an execution clock.

Omitted `retry_epoch` means epoch zero, not the latest epoch. Completion, watchdog and
reconciliation require an exact match; an omitted/zero epoch cannot affect a positive
attempt. Stored NULL is legacy zero for admission while its raw value remains fenced.
A genuinely epochless schema supports only epoch-zero closeout/mirrors; it cannot safely
advance a generation. Only a missing-column error enables closeout's legacy fallback;
other database failures propagate. The absent-column closeout/status fallback re-probes
inside a transaction and keeps the schema stable through the write, so a live migration
cannot remove epoch fencing between its SELECT and UPDATE. Historical `completed`
rows are protected as terminal even without `completed_at`; this does not add a new
completion wire status. No columns are added, dropped or altered here.
The existing migration 140 and stored epochs remain intact.

Keep old writers isolated throughout the server/schema deployment and both engine
upgrades; restore writes only after validating the coordinated contracts. Old
status-reopen and retry-failed callers fail closed rather than silently disabling epoch
protection. See the app's [terminal admission and timeout write policy](../../../../docs/workflow-timeout-write-policy.md)
for CAS, duplicate identity, phase and release-gate requirements.

## Run completion phase

`PUT /runs/:flowId/completion` maps `current_phase` to
`FlowRunCompletion.currentPhase`. For an admitted first settlement:

| Wire field | Phase persistence |
| --- | --- |
| Omitted (`undefined` before serialization) | Preserve the stored phase. |
| `null` | Clear the phase to SQL NULL. |
| String, including `""` or surrounding whitespace | Store verbatim. |
| Any other JSON type | HTTP 400 before repository admission or a write. |

Direct repository callers use the same undefined/null/string semantics. Phase
is not inferred from `result_json`, `input_json`, or `state_json`; accepted
status, phase and completion metadata are persisted in one fenced update.
Omitted `retry_epoch` means zero only (including stored NULL), never the latest
positive attempt. A positive attempt requires its captured, matching epoch.

A terminal replay is read-only: an omitted phase makes no new assertion, while
an explicit string or null must equal the saved phase. A different phase or
other conflicting settlement intent returns HTTP 409 without UPDATE or another
terminal observer. This also forbids backfilling a missing phase after closeout;
a failed row with no completion timestamp is not a supplement path. Identical
replays preserve the winning timestamp and final counts. This phase contract
does not change timeout/epoch implementations or deployment requirements; the
existing [terminal admission and release gate](../../../../docs/workflow-timeout-write-policy.md)
remain authoritative.
