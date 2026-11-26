# Workflow Editor AI drafts: AutoHeal/BaaS, without a draft table

## Boundary and ownership

Workflow owns suggestion generation and validation. The host supplies verified
identity, live edit permission and the existing AutoHeal/BaaS execution adapter.
The browser only calls the same-origin Editor draft API. BaaS owns message and
execution state; ClawWeb does not create or query `workflow_ai_drafts`, store AI
tasks in configuration/workflow tables, or substitute an instance-local Map.

**Editor uses the effective server `autoHeal.botId`, verbatim, including its
configured owner suffix.** The submitting actor authorizes the request; it does
not select a different Bot or replace the configured owner. Existing AutoHeal
configuration precedence is retained. The configured Bot runtime environment, not
the browser or ClawWeb deployment environment, selects its BaaS transport.

No AIStudio experiment, Snapshot, taskguard release, Pack baseline, Git branch,
intelligent-onboarding default Bot, or local template participates in generation.
The provider is asked for a complete YAML suggestion, not a repair/run command.
This prompt restriction is not a provider-enforced tool sandbox: a configured
AutoHeal Bot's remote tool permissions remain a separate operational boundary.
ClawWeb does not call workflow save/deploy/run APIs during this flow; do not infer
from that fact that an unrestricted remote Bot cannot execute a tool.
There is no generation fallback to another execution chain.

The [ordinary Editor document boundary](workflow-editor-draft-boundary.md) still
governs Apply, Save and Deploy. Preparing, generating, polling, cancelling and
applying a preview neither saves nor deploys nor runs a workflow.

## Configured Bot runtime environment

`autoHeal.baasEnvironment` selects `dev`, `pre` or `prod` for the configured
AutoHeal Bot. `AUTO_HEAL_BAAS_ENVIRONMENT` overrides that YAML field. An absent
field retains the legacy `prod` selection; an explicitly invalid or empty
selection fails closed. Administrators must configure the actual environment
that owns the Bot and binding, not infer it from the environment of the Editor.

The adapter and receipt signer resolve the **same complete endpoint and API-key
pair** from `baas.environments[autoHeal.baasEnvironment]`. They do not combine a
non-production endpoint with production credentials, use an incomplete target,
or fall back to another environment. The existing shared IAM transport remains
server-only. Each environment entry must point to the provider deployment whose
Backend can resolve that Bot's environment-scoped identity and binding; naming a
configuration slot `pre` while pointing it to production does not establish that
property.

The provider's Bot lifecycle stage is a separate concept. In particular, an
ACTIVE personal Bot in a non-production environment does not become reachable
by adding `bot_options.lifecycle_stage`. Editor drafting does not add that field,
rewrite the Bot owner, publish the Bot or edit a provider binding.

The signing namespace and execution drift fence include both the ClawWeb control
environment and the selected Bot runtime environment, endpoint and credential.
Changing the target invalidates old receipts before provider access. This
namespace upgrade also invalidates receipts issued by versions that did not bind
the selected Bot environment. It never converts an old request into a new
submission; coordinated rollout must not recreate an uncertain legacy request. Previously failed provider
messages remain failed; after an administrator corrects configuration, a new
explicit user attempt is required for generation.

## Prepare before dispatch

A server-signed **receipt** binds an authorized actor, workflow, request UUID,
exact input digest, source YAML digest, frozen Bot identity, BaaS message ID and
validity window. It contains no prompt, YAML, provider credentials or result.
The browser obtains this receipt **before** the first BaaS submission, keeping
it in the current Editor attempt, not browser persistent storage or a URL.

The host derives the receipt signer from its existing server-only BaaS API key
with a separate cryptographic purpose and environment/provider namespace. No
new browser key or database table is required. Replicas with the same configured
key and namespace can validate the same receipt; a fresh process does not need an old process's memory. Credential rotation can
invalidate receipts, and must never authorize a replacement dispatch. The
namespace is re-evaluated on issue/verify; the execution adapter also rejects
live provider/credential/environment drift before I/O. Separate services sharing
an environment, provider and API credential share this signing trust domain;
untrusted tenants must not share those credentials. Environment labels are not
a unique deployment identity, and gray/prod may intentionally share a domain.

The prepared `draftId` is the caller-assigned BaaS `message_id`. It is derived
from the exact principal/workflow/request/input/target identity, independently
of the issuing instance and timestamp. BaaS's existing message endpoint accepts
that ID and owns its message-ID deduplication and persistence. The adapter sends
it explicitly and accepts only a matching acknowledgement. This depends on the
deployed BaaS supporting that contract; a mismatched acknowledgement is unknown,
not success and not permission to resend with a new ID.

## Browser Service API

Every endpoint requires a verified principal and live workflow edit permission.
The host's exact Origin allowlist and existing CSRF contract remain mandatory.
Every POST, including polling, sends `X-Pack-Version-CSRF: 1`. Responses are
`no-store`; shared authentication, gzip, bounded bodies and abort handling apply.
Requests, receipts and result bodies must not enter generic request/payload logs.
The namespace must be mounted before generic body/audit loggers in enterprise
hosts; malformed, oversized and denied requests need the same protection.

1. `GET /api/workflows/:workflowId/ai-drafts/capabilities`
   returns `{ available, reason? }`. This checks execution/receipt readiness,
   not a draft table or model quality. Failure of the capability transport does
   not establish that Bot configuration is missing.
2. `POST /api/workflows/:workflowId/ai-drafts/prepare`
   accepts `{ requestId, mode, prompt, currentYaml }` and returns
   `{ draftId, receipt, inputSpecDigest }`. This validates and signs the exact
   input and server-selected target, but **does not send a BaaS message**.
3. `POST /api/workflows/:workflowId/ai-drafts`
   accepts the exact same input plus `receipt`, verifies the binding and sends
   the prepared message ID. It returns `{ draftId, status, error? }` with 202.
   A positive provider rejection returns `failed` and a sanitized error code;
   uncertain transport returns `dispatch_uncertain` with the prepared ID.
4. `POST /api/workflows/:workflowId/ai-drafts/:draftId/poll`
   accepts `{ receipt, currentYaml }`, verifies ownership and exact YAML bytes,
   and queries only the signed BaaS message ID. The response is
   `{ draftId, workflowId, status, inputSpecDigest, error?, result? }`.
   Only `completed` carries `{ yaml, summary }`. Polling never dispatches.
5. `POST /api/workflows/:workflowId/ai-drafts/:draftId/cancel`
   accepts `{ receipt }` and acknowledges cancellation of this **local attempt**.
   It does not persist a cancellation, revoke all observers' receipts or stop
   BaaS. Another authorized observer can still query the provider message.
6. Legacy `GET /api/workflows/:workflowId/ai-drafts/:draftId` returns 410
   `AI_DRAFT_RECEIPT_REQUIRED`, never message data based on a bare ID. A legacy
   request without a signed context is not silently recreated.

`create` means extending the current workflow; `modify` adjusts it. Both preserve
workflow ID and the exact value/type of version. Requirements are nonblank and
at most 4,000 Unicode code points, without silent trimming. YAML is complete,
valid UTF-8 and at most 100,000 bytes. Browser actor/Bot/repository/branch fields
confer no authority. Changing input requires a new user attempt; modifying input
under an existing receipt is rejected before provider access.

The receipt uses a bounded, versioned metadata payload and constant-time HMAC
verification. Exact source digest, workflow and draft binding are checked at the
server. Expiry is bounded to 30 minutes. Authorization and deadlines are checked
again after asynchronous provider work, before returning a result.

## Diagnosing provider failure without disclosing diagnostics

A verified provider message in a terminal failure state is not a transport or
YAML validation failure. Decode terminal status before inspecting reply content.
`TIME_OUT` maps to `BAAS_GENERATION_TIMEOUT`; cancelled, stopped or aborted
messages map to `BAAS_GENERATION_CANCELLED`. These are provider terminal states,
not the local deadline or a local-cancel acknowledgement. They take precedence
over conflicting diagnostics and never authorize automatic redispatch.

Only native enveloped `data.error` may select a diagnostic classification, after
the adapter validates the exact message and frozen Bot identity. Strings longer
than 4,096 characters and non-string errors do not select a diagnostic code.
Recognized `BotBindingNotFoundError`, `Bot binding not found:` and worker
`binding not found:` signatures map to `AUTO_HEAL_BOT_UNAVAILABLE`. Source-backed
authentication, session initialization and result-persistence signatures map to
`BAAS_AUTH_FAILED`, `BAAS_SESSION_UNAVAILABLE` and
`BAAS_RESULT_PERSISTENCE_FAILED`; unknown diagnostics retain
`BAAS_GENERATION_FAILED`. Only known worker wrappers are removed; arbitrary
prose, nested errors and assistant text are not searched for signatures.

Raw errors, credentials, URLs, stack traces and failed-message content are not
returned as draft YAML or copied into browser error text or adapter logs.
Terminal failure content is never a candidate, even when it contains valid YAML.
Completed assistant content and outer API-error envelopes are not used to infer
terminal diagnostic codes. Outer poll errors retain their existing operational
recovery semantics; they are not native execution failures.

Known failure guidance includes the **original draft ID**, which is already the
prepared BaaS `message_id`. An authorized operator can correlate that ID directly
with the original provider message's `status` and `error`; there is no ClawWeb
task table or persisted `job_id` lookup. Do not start a new generation just to
obtain diagnostic evidence. Operational recovery keeps the same signed receipt
and polls the same message only. Terminal failure permits a new attempt only
through an explicit user action.

For `AUTO_HEAL_BOT_UNAVAILABLE`, check the configured full Bot identity and owner,
the selected endpoint/key pair, and the Bot and binding in the provider's actual
Backend environment. A Bot marked ACTIVE in one environment does not prove that
another environment can find it. A failed run row with no provider session can
also exist: the provider may persist the message before resolving its binding.
New-session dispatch may omit `metadata.session_id`; authenticated BaaS context
supplies tenant/application identity. The configured Bot runtime environment
continues to select the complete transport pair. This change neither assumes a
default-online Bot nor adds a lifecycle-stage selector, changes a binding or
introduces a task table.

Source evidence: BaaS `MessageResultResponseData` and `get_message_result` expose
status/error; `bot_run/_executor.py`, `_task_message_dispatcher.py`,
`_baas_service.py`, `api/bot_runtime/_exceptions.py` and `api/api_gateway/_jwt.py`
produce the recognized signatures. Synthetic adapter/core/browser regressions
cover safe classification, failed YAML rejection, original-message correlation,
receipt-only recovery, no extra dispatch and no workflow/deploy writes. They
establish diagnostic preservation, not the cause or resolution of a deployed
incident.

## Uncertainty, recovery and cancellation

The browser records the prepared receipt and message ID before starting create,
and marks the attempt submitted before sending the POST. A lost/5xx create
response does not prove either rejection or acceptance. Its safe HTTP diagnostic
is preserved. Recovery uses **only the original poll operation**, not another
create call, new receipt or new message ID. A prepared request with no signed
context cannot be treated as a fresh submission to work around a failure.

An uncertain BaaS acknowledgement can be queried using the already-prepared ID.
A BaaS 404 is not proof of rejection; it remains running within the deadline.
Without a provider result, recovery cannot promise eventual success or redispatch.
No background ClawWeb task monitor or database reconciliation is needed.
`reconcile()` remains a no-op compatibility entry for older host composition.

Browser polling is three seconds with a 15-minute local wait bound; each provider
read is bounded. A valid completed draft whose read-only validation transport
fails can be queried and validated again without regeneration. HTTP 401/403/429
or network errors do not prove that candidate content is invalid. Provider poll
401/403 and outer error envelopes (including HTTP 200 with an error code and
no message data) are reported as `running` with a safe operational error. The
browser offers recovery with the existing receipt, not a fresh generation.

Cancellation, closing, changing context or local expiry aborts local waiting and
prevents late Apply. BaaS has no supported stop API here. A local acknowledgement
is not a durable/global cancelled state, and must never be described as one.
There is no global ClawWeb cancellation record to synchronize across instances.
Provider results remain read-only; late responses cannot bypass Editor context
checks. Neither local nor receipt deadlines prove that the remote Bot stopped.

## Host Plugin API and provider errors

[The TypeScript declarations](../server/contracts/workflow-ai-draft.ts) own the
execution and receipt contracts. Workflow calls the host execution port:

```ts
resolveTarget(): Promise<{ botId: string }>
dispatch({ actorId, envelope }): Promise<{ jobId: string }>
poll({ actorId, envelope, jobId }): Promise<
  | { status: 'running' }
  | { status: 'failed'; error: string }
  | { status: 'completed'; result: { yaml: string; summary: string } }
>
```

The immutable envelope carries the prepared task ID, frozen Bot target, exact
requirements and exact source YAML. Dispatch uses the existing `sendIntervention`
with retries disabled and a top-level `message_id`. Poll uses the existing BaaS
message result API. A changed current AutoHeal setting cannot retarget a signed
request. Poll verifies the returned message ID and frozen Bot identity before
accepting output. Transport changes invalidate old receipts/adapter contexts
instead of sending them to another provider. Other than proven pre-acceptance
rejection, provider HTTP/business errors remain uncertain until the fixed ID is
queried; an HTTP 400 alone is not proof of non-acceptance.

| Code | Meaning and action |
| --- | --- |
| `AI_DRAFT_RECOVERY_UNAVAILABLE` | The host cannot sign/verify draft receipts. Check server BaaS configuration; never send provider keys from the browser. |
| `AUTO_HEAL_BAAS_NOT_CONFIGURED` | The existing AutoHeal/BaaS integration is unavailable; no fallback is dispatched. |
| `AUTO_HEAL_BOT_UNAVAILABLE` | The exact configured AutoHeal Bot/binding is unavailable. Do not append the submitting actor or switch to onboarding. |
| `BAAS_AUTH_FAILED` | Check provider credentials/permissions. A polling authorization failure does not prove that the remote execution stopped. |
| `BAAS_POLL_UNAVAILABLE` | The outer message read failed, not a native task terminal state; retain the signed context and recover the same message. |
| `BAAS_REQUEST_REJECTED` | BaaS positively rejected dispatch, distinct from an unknown acknowledgement. |
| `BAAS_GENERATION_FAILED` / `BAAS_PROVIDER_REJECTED` | Unclassified provider execution failure or rejection; correlate the original message before deciding whether to retry. No candidate may be applied. |
| `BAAS_GENERATION_TIMEOUT` | BaaS reports execution timeout, distinct from local `EXECUTION_TIMEOUT`. Check the original message and upstream timeout configuration; do not automatically redispatch. |
| `BAAS_GENERATION_CANCELLED` | BaaS reports cancellation, stop or abort. Inspect the original message; this is not a local-cancel acknowledgement. |
| `BAAS_SESSION_UNAVAILABLE` | A known execution signature indicates missing session context or materialization failure. Check Bot runtime, engine adapter and tenant/session initialization, not only the user requirement. |
| `BAAS_RESULT_PERSISTENCE_FAILED` | BaaS could not persist the result. Even generated content is not accepted as a candidate; check provider storage and the original message. |
| `INVALID_GENERATION_RESULT` | Reply parsing, workflow identity/version or output validation failed; no substitute YAML is produced. |
| `EXECUTION_TIMEOUT` | The signed local waiting window expired; the remote Bot may continue. |
| `AI_DRAFT_RECEIPT_REQUIRED` | An older client has no signed recovery context. Do not allocate and submit a replacement automatically. |

Unexpected request diagnostics use `workflow_ai_draft_request` with fixed
operation/phase metadata and allowlisted error names. Raw exceptions, stacks,
provider credentials, receipts, prompts and YAML are not logged or returned.
A generic `INTERNAL_ERROR` alone cannot identify the failed stage or establish
whether BaaS accepted a message.

## Candidate validation and Apply

The server validates complete provider YAML against the signed source identity
and version, using bounded JSON-compatible YAML parsing. Numeric versions and
non-boolean `on`/`off` IDs remain supported; aliases, depth and expansion are
bounded. Invalid output is rejected rather than applied or replaced by a template.

The browser snapshots the current applied document before preparation. Unapplied
YAML/config buffers and in-progress saves block generation/Apply. It checks
exact input digest, task/workflow identity, duplicate IDs, references and cycles,
then calls existing read-only `POST /api/workflows/validate` with `{ spec }`.
Only valid normalized output can become a preview. Model-supplied canvas
coordinates are not authority; existing layout is restored locally.

Diffs are computed from the final normalized candidate, not model assertions.
Removal and top-level metadata changes require confirmation. Context/request
checks reject stale or late results. Apply changes only the Editor working copy,
marks it dirty and opens comparison; Save and Deploy remain explicit actions.

## Migration and verification boundaries

Migration slot v153 is reserved with **no DDL**. Fresh deployments do not create
`workflow_ai_drafts`; an existing legacy table is neither read nor dropped.
No replacement schema, configuration row per task, Redis deployment or shared
filesystem journal is introduced. Existing identity/ACL storage is independent.

Server and Editor artifacts must roll out together: a new Editor talking to an
old server fails preparation before dispatch, while an old Editor's unsigned
create/GET is rejected by a new server. Old table-backed in-flight requests do
not acquire new signed contexts automatically. Do not silently retry them as
new tasks or claim that mixed-version traffic transparently recovers.

Synthetic tests cover no-table operation, preparation-before-dispatch, BaaS ID
forwarding/ack matching, replica/restart receipt verification, exact input and
principal binding, live grants, expiry, uncertain acknowledgement, read-only
recovery, local-only cancellation, validation and late-Apply guards. Migration
tests prove the reserved version creates nothing and preserves legacy rows.

These tests do not establish the deployed BaaS revision, real credentials/Bot
binding, model quality or release status. Verify live integration separately;
never infer deployment or incident closure from a local test result.
