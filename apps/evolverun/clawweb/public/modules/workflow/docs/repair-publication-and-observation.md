# Reviewed publication and automatic observation

Implemented contract for the first manual optimization batch (2026-10-15). This does not assert merge, Host deployment, or production verification.

## Ownership and propagation

Workflow owns issue selection, immutable task/revision inputs, approvals, publication records and verification events. OCB implements `RepairPublicationPort` with the existing configured Git remote/credentials and application configuration. ClawMind consumes the existing active deployment/tag and materializes a read-only complete Pack. Public packages do not import OCB or ClawMind source.

Affected Service API consumers are the Workflow browser and HTTP router; the Plugin API implementation is OCB. No new DB tables, credentials or environment variables are introduced. Existing `workflowRepair` application configuration needs only `publication.enabled: true` for this publication path. `nodeBinary`, `entryPath`, Bot online state and Bot execution channels are not prerequisites. Existing workflow permission checks remain authoritative.

## One publication decision

`POST /api/workflow-repairs/:taskId/publish` accepts the existing confirmation request: revision, expectedStateVersion, requestId, targetId and acknowledgeUncoveredChecks. The server creates an internal immutable approval and dispatches the exact reviewed candidate. The browser freezes the complete payload across uncertain retries. Internal confirmation/revoke/deployment endpoints remain compatible for historical callers; they are not separate mandatory browser steps.

A new target has `scope: workflow`, an opaque environment/workflow identity, and empty legacy botId/ownerId fields. Its scope covers subsequent new executions of that workflow in the Host environment. Historical targets without scope retain their original identity and may be reconciled or stopped; they never grant workflow-wide publication authorization or cause a replacement Bot dispatch.

The Host validates the candidate commit/tree/manifest, rechecks current authority and baseline, then atomically pushes `wf/<workflowId>` and an immutable `deploy/<workflowId>/#N` tag under exact ref leases. Activation writes history, facade bindings, the active pointer and workflow spec under the existing DB transaction and baseline CAS. Git and DB are separate stages. A Git success followed by DB failure remains uncertain; recovery reuses the frozen recipe/tag and rechecks authority/lease before activation. No business execution is triggered.

## Scope and recovery

Item ownership is transactional. Identical in-flight scopes reuse a task, independent scopes may proceed, and overlapping occupied items cannot be taken by another task. Deployment succeeds only for actual changed item results; other items return to pending. `not_needed` alone never proves a repair applied or removes it from pending generation. Published items remain excluded while observing.

`GET /api/workflow-repairs/requests/lookup?workflowId=...&requestId=...` requires workflow edit access and returns exactly the committed generation request, or null. Unknown create outcomes use this lookup before an exact-payload retry, so an unrelated task cannot be mistaken for the lost response.

Automatic recurrence appends immutable evidence and derives a fresh episode with previousItemId; it removes the old operations from the next generation input and requires reassessment. Episode traversal supports repeated recurrence. Historical verification records remain unchanged. Current-Pack reassessment compares full reviewed file identities and individual changed node fields: reverting B cannot reopen unchanged A. Unstructured/unmapped edits remain indeterminate rather than being assumed reverted. Unchanged inherited edits can be observed under the exact newer active deployment number, recorded separately in evidence.

## Automatic rule v1

`node-timeout/v1` applies only when frozen context identifies a timeout signature and a node. It observes a rolling seven-day window and requires at least five distinct completed executions of the relevant node, with successful node status and valid latest analysis containing no unknowns and no original signature. One explicit current-version recurrence is sufficient for an ineffective conclusion. Missing rules, missing analysis, unrelated/skipped nodes, unfinished executions and insufficient samples remain observing. Old or unknown deployment numbers never count toward the active version's conclusion.

Output quality, cost and arbitrary business correctness do not acquire an automatic success rule. The UI explains the rule/version/thresholds and links evidence; it requires no manual 'fixed' action. Existing manual conclusion APIs are retained for historical audit/correction compatibility.

The existing Host reconciliation monitor scans deployment records with bounded keyset pages, reconciles uncertain publication, and observes effects without requiring an open browser. It never starts business runs. Gray rollout, scheduling, automatic drafts and notifications are outside this batch.

## Issue statistics

Severity comes from identified current diagnoses; legacy unknown severity is null. Statistics deduplicate flow IDs and use runtime start time. Per-window relevant runs, analyzed runs and affected runs are distinct. Full occurrence rates require full relevant analysis coverage; otherwise the UI labels an analyzed-sample hit rate. Trends compare non-overlapping seven-day windows only when coverage is complete in both. Service-side severity/node/mode filters and priority/frequency/recent sorting run before issue pagination. Missing historical run times and the statistics history bound remain explicit rather than becoming fabricated rates.
