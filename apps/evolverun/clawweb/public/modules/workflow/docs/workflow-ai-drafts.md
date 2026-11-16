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
configuration precedence and BaaS credentials/transport are retained.

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
| `BAAS_GENERATION_FAILED` / `BAAS_PROVIDER_REJECTED` | Provider execution failed or was rejected; no candidate may be applied. |
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
