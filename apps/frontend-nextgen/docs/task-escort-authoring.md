# Task escort authoring

Scope: the shared TaskEscort workspace at `/evolution-factory/task-escort`.
This guide describes source behavior, not evidence of a deployed service.

## Same-origin API

The authoring controller uses **`/openapi/v1/harnessflow/api`**, relative to the
frontend origin. The host gateway owns upstream routing; do not replace it with
a private origin or assume the separate `/api/workflows` menu proxy is this API.
Paths below are relative to that prefix; workflow and job IDs are URL-encoded.

| Operation | Contract |
| --- | --- |
| Read spec / permissions | `GET /workflows/:id`, `GET /workflows/:id/access` |
| Validate YAML or spec | `POST /workflows/validate` |
| Clone an existing workflow | `GET /workflows/clone-candidates`, `POST /workflows/clone/preview`, then explicit `POST /workflows/clone` |
| Generate a new candidate | Select an editable source workflow, then use its receipt-bound AI-draft protocol; review the generated document under the new target ID before creating it |
| Create blank / reviewed AI workflow | Explicit `POST /auto-heal/save` with `createOnly: true`; not an overwrite of an existing workflow |
| Modify a draft with AI | `GET /workflows/:id/ai-drafts/capabilities`; `POST` to `/prepare`, the draft collection, `/:draftId/poll` and `/:draftId/cancel` beneath `/workflows/:id/ai-drafts` |
| Save without publishing | Read `/workflows/:id/editor-draft` and `/pack-version`; explicitly `POST /workflows/:id/unified-save` with the observed draft token and Git baseline, then verify both stored snapshots |
| Publish | Read `/auto-heal/editor-deploy/snapshot-v2/state`; review the complete saved-tree diff, then explicitly `POST /auto-heal/editor-deploy/snapshot-v2` with the reviewed candidate commit, draft token, active commit, and `autoRun: false` |

The wire adapter is
[workflowAuthoringController](../src/services/backendApi/harnessFlow/workflowAuthoringController.ts).
[workflowAuthoringService](../src/services/taskEscortParity/workflowAuthoringService.ts)
owns validation, request-bound jobs and save/publish outcome checks.
Authoring binds an explicit nonempty `user_id`, sends `X-User-Id`, and disables
implicit store-identity injection and transient retries. AI-draft routes take
no query string; their receipt belongs in the request body. Client checks do
not grant permissions: the server must authorize the bound identity.

## Create, review, apply, save, publish

- An empty workflow list does not block creation when identity/space readiness
  is established. Validate the target ID and title; blank creation and cloning
  are explicit persistence actions, not previews. Clone from server-provided
  candidates and preview, not from a local node-only copy.
- AI generation/modification produces a **candidate**, not a saved workflow.
  Review the preview and validation errors/warnings, then explicitly apply.
  Invalid or stale candidates cannot be applied. Existing-workflow candidates
  must retain the workflow ID and version; preserve extension fields and layout.
- Applying an editor candidate updates the local canvas/YAML only. Applying a
  new-workflow candidate only enables the separate create action. Neither
  implicitly saves, publishes or executes a workflow.
- Save validates the submitted snapshot and requires both database and Git
  success. A partial save is an error, not success or permission to publish.
  Publication is a separate confirmation; if save succeeds but publication
  fails, report the saved state and publication failure separately.
- Edit permission gates YAML/AI/save/publish. Node properties and script files
  remain read-only in this authoring surface. Warn before discarding dirty work.

## Shared workflow dashboard and repair

The dashboard stays available to signed-in users and reads the same ClawWeb
aggregates as the existing dashboard. Its scope comes from server authorization:
all workflows for administrators, explicit `can_view` workflows for other users.
The workflow selector consumes the authorized catalogue and does not grant
workflow access. Admin-only, non-workflow aggregates are not used by Task Escort.

In the repair workspace, an existing candidates response must explicitly report
`canOptimize: true` before current-version suggestions may produce a manual
candidate via `POST /workflow-repairs`. Generation does not apply, release, or
run the workflow. For reviewed candidates, the existing deployment-target and
publish APIs verify the exact candidate, workflow target and state; the UI
requires complete baseline diff review and explicit confirmation. A lost
publish response reuses the same immutable request on retry. Neither action
changes Editor authorization. Direct suggestion application remains unwired in
Task Escort; trial history remains readable, but Task Escort does not yet submit
trial starts. ClawWeb trial start still requires `can_execute` and an eligible Bot.

## Isolation and cancellation

Identity, space, workflow and request/snapshot changes must not reuse old
results. Abort outstanding reads/writes and ignore late responses; do not refresh
or navigate the new identity using an old completion. Prevent duplicate submits
while a request is active. Errors such as 403/409 retain user input for retry.

AI generation and modification use receipt-bound cancellation. Cancelling stops
local acceptance immediately; a cancellation acknowledgement does not prove an
upstream provider stopped. An aborted write is not proof of rollback. Unknown
write outcomes block blind replay and require reconciliation instead of a new
request that could overwrite or publish a different snapshot.

## Shared source and verification

Avernet owns shared pages, hooks, services and public tests. OCB consumes them
from its **`submodules/avernet`** frontend at its recorded public baseline, plus
OCB's enterprise shell/overlay. A separate older Avernet checkout is not a
replacement for that complete baseline. The integration owner transfers only
reviewed task deltas, preserving unrelated public and enterprise changes; never
replace whole source/test directories to make versions match.

Run from the public frontend with its own dependencies:

```bash
npm run typecheck
npm test -- --runInBand test/taskEscortParity
npm run check:open-core
```

OCB separately checks its public-test manifest and runs the relevant tests and
typecheck in the assembled tree. Add only existing, public-owned literal test
paths after comparing same-path internal assertions; retain retired entries
for stale-copy deletion. Assembly checks are not functional or live-API tests.

Delivery order: **merge the public changes into Avernet dev; merge the OCB
integration into OCB dev with the matching public input; verify the merged
pair; only then use `pr-internal.sh` to propose a TeamClaw PR and have that PR
reviewed/merged**. Do not publish directly from an old dirty development tree.
Historical deployment approval does not authorize a new delivery or runtime write.
This documentation/manifest work performed no online/PROD writes. Local fixtures,
unit tests and assembly checks do not establish live connectivity, production
permissions, deployment or successful production writes.
