# Optional reviewed-candidate runs

The review page offers **先试跑** and **发布此版本**. Parameters and the production Bot selector expand on demand, with no Bot selected by default. Starting is an explicit user action and executes the candidate's real business nodes. Success does not publish. Direct publication keeps the existing confirmation, permission/baseline checks and automatic observation.

## Existing execution path

Workflow owns the review APIs and frozen run request. OCB supplies its existing Git store, Bot directory, permission checks and runtime delivery port. ClawMind executes `/workflow run <workflowId> --remote --request-id <UUID>` through the ordinary run controller. There is no separate trial command, MCP tool, worker claim, completion receipt or local one-shot fence.

`GET /api/workflow-repairs/:taskId/trials/options?revision=N` returns the exact candidate's schema/digest and authorized production OpenClaw Bots. `POST /:taskId/trials` validates revision/stateVersion, inputSchemaDigest, target and bounded typed JSON input, then freezes the server-selected commit/tree, Pack, input and Bot identity. Parameters never enter the command, message or URL. Ajv does not coerce values, add defaults or load external schemas. History and result previews redact sensitive keys/input values and cap output; nodes/logs use the existing run page.

Saving creates `refs/heads/snapshots/<candidateCommit>` with a create-only lease. It never moves the editable saved ref, active workflow ref, deployment tag/history or Bot workspace. Normal remote materialization is commit-addressed so later saves cannot replace the running Pack's scripts/actions or same-Pack child workflows. Pinned requests verify both commit and tree rather than depending on a saved-commit message format.

The runtime reads frozen metadata once through signed `POST /api/internal/reads/run-requests` with `{requestId, botId, ownerId}`. This is a repeatable pure read, mounted only with configured signature verification. Actor execution permission and Bot-owner access are rechecked. Upgrade every target ClawMind before enabling the Host/page: an older parser can treat `--request-id` as ordinary input and is not a safe target. The updated runtime rejects an unconfirmed metadata read or registration before execution. No capability probe is added.

## Atomic registration and results

Existing migration 142 supplies the release-record store; no new DDL is required:

- `trial` stores the immutable request and nullable winning native `flowId`.
- `trial_run` marks each actual root/child flow for purpose filtering.

The existing `/api/internal/runs` INSERT validates `run_request_id`, Bot/owner, full commit/tree, input digest, workflow identity, `workflow_version = -1` and null deployment number. A single transaction binds the first native root, adds its purpose marker and inserts the run. Failure rolls back all three. Replaying the same flow is idempotent; a different root is rejected. Same-Pack children carry the root ID and get their own markers without rebinding it.

Registration gates existing preflight hooks and node execution. A failed or ambiguous registration leaves the native skeleton unable to execute on recovery. Registered recovery verifies the exact existing run/binding; interrupted side effects are not automatically repeated. No extra lifecycle endpoints or local persistence mechanism are added.

History derives waiting/running/terminal state and output from the exact registered run. Message delivery is only **等待运行登记**. Lost responses preserve the original request and binding; there is no automatic redispatch, Bot substitution or latest-run/chat-based inference. Candidate revisions and publication state are unchanged by execution.

## Statistics isolation

`@avernet/clawweb-shared/server/services/run-purpose` classifies exact `trial`/`trial_run` IDs. Use `productionRunSql` for formal health, success rates, issue frequency, version comparison and optimization scans, and `isTrialRun` for terminal analysis/notifications. Legacy runs with null/-1 versions remain formal. The workflow run page defaults to production with a separate trial filter. Automatic effect verification and manual formal sample selection exclude these rows.

Compatible Avernet/OCB/ClawMind revisions must be delivered together. Deployment comparison consumes the same predicate for both version samples and unknown-deployment counts.

## Local acceptance boundary

Tests use temporary SQLite, bare Git, signed requests and a controlled Bot delivery port. They cover immutable snapshots despite later saved-ref changes, typed/redacted input, pure metadata reads, competing registrations, lost-response replay, waiting/results, formal exclusion, exact-candidate publication and direct publication. Runtime controller tests execute only benign local Pack scripts, including pinned child/actions and recovery. Browser acceptance uses the real review component and local HTTP services. No real Bot, external AIS/MCP, business workflow, deployment or merge is part of acceptance.
