# Optional reviewed-candidate runs

The review page offers **先试跑** and **发布此版本**. Parameters and the production Bot selector expand on demand, with no Bot selected by default. Starting is an explicit user action and executes the candidate's real business nodes. Success does not publish. Direct publication keeps the existing confirmation, permission/baseline checks and automatic observation.

## Existing execution path

Workflow owns the review APIs and frozen run request. OCB supplies its existing Git store, Bot directory, permission checks and runtime delivery port. Updated ClawMind executes `/workflow run request://<UUID> --remote` through the ordinary run controller. There is no separate trial command, MCP tool, worker claim, completion receipt or local one-shot fence.

`GET /api/workflow-repairs/:taskId/trials/options?revision=N` returns the exact candidate's schema/digest and authorized production OpenClaw Bots. `POST /:taskId/trials` validates revision/stateVersion, inputSchemaDigest, target and bounded typed JSON input, then freezes the server-selected commit/tree, Pack, input and Bot identity. Exact-mode parameters never enter the command, message or URL. Ajv does not coerce values, add defaults or load external schemas. History and result previews redact sensitive keys/input values and cap output; nodes/logs use the existing run page.

Saving creates `refs/heads/snapshots/<candidateCommit>` with a create-only lease. It never moves the editable saved ref, active workflow ref, deployment tag/history or Bot workspace. Only frozen-request materialization is commit-addressed so later saves cannot replace the running Pack's scripts/actions or same-Pack child workflows. Pinned requests verify both commit and tree rather than depending on a saved-commit message format.

The runtime reads frozen metadata once through signed `POST /api/internal/reads/run-requests` with `{requestId, botId, ownerId}`. This is a repeatable pure read, mounted only with configured signature verification. Actor execution permission and Bot-owner access are rechecked. Exact execution requires an updated target ClawMind. The request selector is rejected by old workflow/path resolution instead of executing the real workflow; the page provides a separate explicit saved-version compatibility choice. The updated runtime rejects an unconfirmed metadata read or registration before execution. No capability probe is added.

## Atomic registration and results

Existing migration 142 supplies the release-record store; no new DDL is required:

- `trial` stores the immutable request and nullable winning native `flowId`.
- `trial_run` marks each actual root/child flow for purpose filtering.

The existing `/api/internal/runs` INSERT validates `run_request_id`, Bot/owner, full commit/tree, input digest, workflow identity, `workflow_version = -1` and null deployment number. A single transaction binds the first native root, adds its purpose marker and inserts the run. Failure rolls back all three. Replaying the same flow is idempotent; a different root is rejected. Same-Pack children carry the root ID and get their own markers without rebinding it.

Registration gates existing preflight hooks and node execution. A failed or ambiguous registration leaves the native skeleton unable to execute on recovery. Registered recovery verifies the exact existing run/binding; interrupted side effects are not automatically repeated. No extra lifecycle endpoints or local persistence mechanism are added.

History derives waiting/running/terminal state and output from the exact registered run. Message delivery is only **等待运行登记**. Lost responses preserve the original request and binding; there is no automatic redispatch, Bot substitution or latest-run/chat-based inference. Candidate revisions and publication state are unchanged by execution.

## Statistics isolation

`@avernet/clawweb-shared/server/services/run-purpose` classifies exact `trial`/`trial_run` IDs. Use `productionRunSql` for formal health, success rates, issue frequency, version comparison and optimization scans, and `isTrialRun` for terminal analysis/notifications. Legacy runs with null/-1 versions remain formal. The workflow run page defaults to production with a separate trial filter. Automatic effect verification and manual formal sample selection exclude these rows.

Exact execution requires matching Avernet/OCB/ClawMind revisions; ordinary remote execution remains unchanged. Deployment comparison consumes the same predicate for both version samples and unknown-deployment counts.

## Local acceptance boundary

Tests use temporary SQLite, bare Git, signed requests and a controlled Bot delivery port. They cover immutable snapshots despite later saved-ref changes, typed/redacted input, pure metadata reads, competing registrations, lost-response replay, waiting/results, formal exclusion, exact-candidate publication and direct publication. Runtime controller tests execute only benign local Pack scripts, including pinned child/actions and recovery. Browser acceptance uses the real review component and local HTTP services. No real Bot, external AIS/MCP, business workflow, deployment or merge is part of acceptance.

## Explicit legacy saved-version compatibility

The page's **使用旧插件兼容模式** choice sets `mode=saved`. The Host sends the existing `run <workflowId> --remote` command with safely quoted non-sensitive parameters. It never moves saved refs or writes a reviewed candidate into the Bot workspace. No capability probe, automatic fallback, extra command/tool or execution chain is added.

Before dispatch, the existing Host Git reader checks the actual `saved/<workflowId>` input schema. Compatibility is unavailable if that definition cannot be read or differs from the reviewed form; it never sends candidate-only parameters to a different saved definition. A Host without saved-schema support also rejects compatibility mode. Ordinary legacy run registration accepts omitted/null draft versions as well as `-1`, while explicit release versions and exact-request registration retain their existing restrictions.

The ordinary signed run INSERT associates the existing isolated session key, or native parent metadata, with this request and adds purpose markers atomically. Every identifiable duplicate is retained and excluded from formal statistics; the first root is the history link. This is best-effort association, with no commit/tree proof and no promise of old-engine startup/recovery/notification isolation. Missing context stays unconfirmed rather than selecting the latest workflow run.

History always labels saved-mode output as **未固定候选、仅供参考**. Even successful saved runs do not satisfy the candidate-success check in publication confirmation. Parameters follow old CLI string semantics; sensitive input and parameter names that would override routing options are rejected before dispatch. Exact mode keeps structured input and its original startup fence. Only the request selector enables frozen execution. The `--request-id` parameter name retains its ordinary business-input meaning on both plugin versions, so no ordinary run parameter is reserved by this feature.
