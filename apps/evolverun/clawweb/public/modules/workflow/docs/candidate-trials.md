# Optional reviewed-candidate runs

The review page offers **先试跑** and **发布此版本**. The parameter form and production Bot selector open in a focused modal with a scrolling body and persistent actions, with no Bot selected by default. Starting is an explicit user action and executes the candidate's real business nodes. Success does not publish. Direct publication keeps the existing confirmation, permission/baseline checks and automatic observation.

## Existing execution path

Request correlation uses the same UUID from delivery through native run registration:
the Bot posts its actual `flowId` with `run_request_id` to the existing `/api/internal/runs`
endpoint. There is no guessed/latest-run association or synthetic flow ID.

Before a run exists, an updated Bot can post startup failure feedback to
`POST /api/internal/run-requests/failures` behind the Host's existing signature middleware.
The body is `{ requestId, botId, ownerId, code, httpStatus? }`, where `code` is
`REQUEST_READ_FAILED`, `REQUEST_INVALID`, or `START_FAILED`; `httpStatus` is zero
for transport failure or an integer from 400 to 599. No raw error, response, input,
or credential is accepted for display. The target Bot identity and current execution
authorization are checked before updating the existing trial record. Repeated feedback
is idempotent; native registration wins over late startup feedback. This adds no table,
request claim, dispatch retry, or workflow publication step.

ClawMind must use its ordinary internal API transport (routing, signature and dynamic
IAM identity) for both reads and feedback, not its external web-link URL. Deploy the
updated workflow module before upgrading the plugin. Older plugins remain compatible
but cannot report startup failure. If the API itself is unreachable, feedback cannot
be persisted; the original Bot session remains the diagnostic source. No business
command is automatically resent in that case.

Workflow owns the review APIs and frozen run request. OCB supplies its existing Git store, Bot directory, permission checks and runtime delivery port. Updated ClawMind executes `/workflow run request://<UUID> --remote` through the ordinary run controller. There is no separate trial command, MCP tool, worker claim, completion receipt or local one-shot fence.

`GET /api/workflow-repairs/:taskId/trials/options?revision=N` returns the exact candidate's normalized input definition/digest and authorized production OpenClaw Bots. JSON Schema, the existing field-map schema, independent `input.requiredParams`/root `requiredParams`, and root `params` defaults share one normalization contract. Missing definitions use an ordinary key/value form; explicit empty definitions show no parameters. Loading and read/format failure have separate states and retry. JSON is an optional advanced editor. Defaults initialize once per candidate and closing/reopening preserves edits. `POST /:taskId/trials` validates revision/stateVersion, inputSchemaDigest, target and bounded typed JSON input, then freezes the server-selected commit/tree, Pack, input and Bot identity. Exact-mode parameters never enter the command, message or URL. Ajv does not coerce values, add defaults or load external schemas. History and result previews redact sensitive keys/input values and cap output; nodes/logs use the existing run page.

Explicit open-object schemas and property-count constraints keep the key/value form available even without named properties. Clearing an optional numeric field omits it (or preserves a schema-allowed null); zero, untouched defaults and required numeric validation remain intact, including nested objects and arrays. History redaction follows local JSON Pointer references, composition/conditional branches and matching property patterns. Unresolvable/cyclic references, nested schema resources and unsupported semantics hide the affected input subtree and its echoed values. No schema URLs are loaded; defaults stay out of history responses, and frozen execution inputs are unchanged.

Root dependency, conditional, enum/const and property-name constraints survive normalization without an explicit object type. Dynamic business fields use their matching property patterns or additional-property schema for typed controls, including nested objects and arrays; the original server schema validates without coercion. Required means the property must exist: schema-valid null and empty-string values are submitted, while absent fields stay blocked and invalid numeric/string constraints remain errors. Open-object defaults initialize once with their original zero/false values.

Declared and dynamic fields can coexist at every object level. Dynamic values round-trip through the advanced editor separately from declared values, so removing an extra field works and it cannot shadow a declared field. Clearing an optional string omits it when its schema rejects the empty string; valid empty strings remain values. This check uses the existing Ajv dependency without coercion or defaults. An independent requiredParams declaration preserves legacy field-map parameters named additionalProperties/not without overriding explicit root schema markers.

Saving creates `refs/heads/snapshots/<candidateCommit>` with a create-only lease. It never moves the editable saved ref, active workflow ref, deployment tag/history or Bot workspace. Only frozen-request materialization is commit-addressed so later saves cannot replace the running Pack's scripts/actions or same-Pack child workflows. Pinned requests verify both commit and tree rather than depending on a saved-commit message format.

The runtime reads frozen metadata once through signed `POST /api/internal/reads/run-requests` with `{requestId, botId, ownerId}`. This is a repeatable pure read, mounted only with configured signature verification. Actor execution permission and Bot-owner access are rechecked. The Host prepares only the selected Bot using read-only installed-plugin metadata and command-parser negotiation, freezes its artifact identity, and rechecks permission and identity immediately before the single dispatch. Browser `mode` never selects execution. The request selector is rejected by old workflow/path resolution instead of executing another workflow. The updated runtime rejects an unconfirmed metadata read or registration before execution. Capability discovery never runs a workflow or imports the plugin entry. An in-memory gateway that has not reloaded its installed plugin can still reject the selector; only signed native registration proves an execution, and there is no fallback dispatch.

## Atomic registration and results

Existing migration 142 supplies the release-record store; no new DDL is required:

- `trial` stores the immutable request and nullable winning native `flowId`.
- `trial_run` marks each actual root/child flow for purpose filtering.

The existing `/api/internal/runs` INSERT validates `run_request_id`, Bot/owner, full commit/tree, input digest, workflow identity, `workflow_version = -1` and null deployment number. A single transaction binds the first native root, adds its purpose marker and inserts the run. Failure rolls back all three. Replaying the same flow is idempotent; a different root is rejected. Same-Pack children carry the root ID and get their own markers without rebinding it.

Registration gates existing preflight hooks and node execution. A failed or ambiguous registration leaves the native skeleton unable to execute on recovery. Registered recovery verifies the exact existing run/binding; interrupted side effects are not automatically repeated. No extra lifecycle endpoints or local persistence mechanism are added.

History derives waiting/running/terminal state and output from the exact registered run. Message delivery is only **等待运行登记**. Lost responses preserve the original request and binding; there is no automatic redispatch, Bot substitution or latest-run/chat-based inference. Candidate revisions and publication state are unchanged by execution.

For a pinned request awaiting registration (including old **结果未知** records), history offers
**结束未启动请求**. The edit-authorized `POST /api/workflow-repairs/:taskId/trials/:trialId/stop`
ends only that unregistered request, retaining its history and parameters. It uses the
same workflow transaction lock as native run registration: if a run registered first,
ending is rejected; if ending wins, a delayed old command cannot register or execute.
This does not stop a running workflow or dispatch a replacement. The user may then
explicitly start a new trial with the preserved form. Lost close responses are safe to
repeat, and legacy saved-mode requests are not covered by this operation.

## Statistics isolation

`@avernet/clawweb-shared/server/services/run-purpose` classifies exact `trial`/`trial_run` IDs. Use `productionRunSql` for formal health, success rates, issue frequency, version comparison and optimization scans, and `isTrialRun` for terminal analysis/notifications. Legacy runs with null/-1 versions remain formal. The workflow run page defaults to production with a separate trial filter. Automatic effect verification and manual formal sample selection exclude these rows.

Exact execution requires matching Avernet/OCB/ClawMind revisions; ordinary remote execution remains unchanged. Deployment comparison consumes the same predicate for both version samples and unknown-deployment counts.

## Local acceptance boundary

Tests use temporary SQLite, bare Git, signed requests and a controlled Bot delivery port. They cover immutable snapshots despite later saved-ref changes, typed/redacted input, pure metadata reads, competing registrations, lost-response replay, waiting/results, formal exclusion, exact-candidate publication and direct publication. Runtime controller tests execute only benign local Pack scripts, including pinned child/actions and recovery. Browser acceptance uses the real review component and local HTTP services. No real Bot, external AIS/MCP, business workflow, deployment or merge is part of acceptance.

## Automatic selection and legacy limitation

There is no compatibility checkbox. The existing Host runtime reads the selected production Bot's configured plugin installation/load path, enabled policy and actual installed parser. Package version `0.1.0` alone proves nothing. Missing/ambiguous metadata, disabled plugins, unreadable artifacts and `DIST_UNVERIFIED` remain unavailable. The Host returns only a bounded capability kind and artifact/config hash, never config or credentials. It uses the existing runtime shell port for the two read-only checks, not a new tool, model agent, workflow or fallback execution chain.

An old parser is automatically identified. Ordinary old `run <workflowId> --remote` reads a mutable saved ref on the Bot and cannot be fenced by a server-side preflight comparison. Even a full-tree match immediately before delivery can be replaced by a concurrent save. Therefore the review page does not dispatch a new legacy saved run: it explains that this Bot cannot lock the reviewed candidate and asks for a plugin upgrade and Bot restart. Unknown capability also fails before creating an execution request or sending a command. This deliberately narrows the previous best-effort compatibility behavior rather than silently running another saved candidate. Normal `/workflow run --remote` is unchanged.

Previously persisted saved-mode requests can still be read/reconciled by the same request ID without dispatch. Their native root/child records remain isolated from formal statistics and history labels them **未固定候选、仅供参考**; they cannot satisfy a candidate-success claim. Exact requests continue validating full commit/tree/input and Bot identities atomically. Sensitive structured parameters never enter the chat command. No retry changes Bot, input, candidate, or execution mode; unknown delivery preserves the original request and never auto-resends.

## Regression evidence (2026-10-16)

- Workflow suite after integrating current dev (including #2831/#2847): 777 tests; paired OCB internal suite: 784 tests, zero failures. Public/dependency build and internal type checks passed using Node 24.13.0.
- Local composition with task-workspace PR #2847 merged without source conflicts; 51 trial/release/workspace tests passed. Its tabs and inline actions were preserved; this change does not rewrite those components.
- Real components in a local HTTP fixture at 1365×900 and 390×600: field-map form/defaults/enum, recursive object/array widgets without JSON, missing/empty definitions, loading/read failure/retry, modal focus/Tab/Esc/restore, internal scrolling, persistent modal actions, preserved Bot/input, old capability refusal and explicit Bot change were inspected.
- A controlled lost response kept the original request/input/Bot across close/reopen; the two explicit HTTP attempts had identical bodies. Candidate change cleared input and labelled the old result; the background publication action stayed disabled. Fixture logs contained no publication or unexpected API writes.
- Host tests use temporary configured plugin artifacts (including an entry that throws if imported), bare Git/SQLite and mocked runtime responses. No real Bot, AIS, workflow, publication, configuration change, merge or deployment was invoked. These are local regression results, not deployed behavior or completed remote CI.
