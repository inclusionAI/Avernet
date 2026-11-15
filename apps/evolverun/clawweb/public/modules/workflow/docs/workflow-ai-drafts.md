# Workflow Editor AI drafts: AutoHeal/BaaS

## Boundary and ownership

Workflow owns the Editor draft Service API and its durable task records. The
core service consumes the host-supplied `WorkflowAiDraftExecutionPort` Plugin
API; the host assembles the existing AutoHeal/BaaS integration, verified
identity, configuration, permissions and lifecycle. The browser consumes only
the same-origin draft API below, not the BaaS client or provider credentials.

**Editor AI uses the same server-configured Bot as AutoHeal through existing
BaaS.** The host resolves the effective `autoHeal.botId` using the existing
AutoHeal configuration resolver (including its established environment-over-YAML
precedence). It does not use the intelligent-onboarding default Bot or a
browser-selected Bot. Pass the configured ID **verbatim**, including any suffix
already present; never append the submitting user's ID or otherwise rewrite it.
The actor identifies the authorized draft owner, not a replacement Bot target.

Generation needs no AIStudio experiment, Snapshot, taskguard release, Pack
baseline, Git mirror, branch or result commit. It must not fall back to those
transports, intelligent onboarding, or a local template. This is not diagnosis
or repair selection: a natural-language editing request does not invent repair
items, and existing repair/publication contracts remain separate.

The [ordinary Editor document boundary](workflow-editor-draft-boundary.md)
continues to govern Apply/save/deploy. AI adds a candidate-generation step only:
no generation, polling, cancellation, preview or Apply operation saves, deploys
or runs the workflow. Durable AI task bookkeeping is not a workflow write.

## Browser Service API (unchanged)

All paths are relative to the current same-origin host. Every operation
requires verified identity and workflow edit permission. Drafts are bound to
the submitting actor and workflow; knowing a draft ID is not authorization.
Mutations retain the existing same-origin/CSRF checks, including
`X-Pack-Version-CSRF: 1` for draft submission and cancellation. Shared browser
authentication, gzip, no-store and abort transport remain in use.

- `GET /api/workflows/:workflowId/ai-drafts/capabilities` returns
  `{ available, reason? }` before generation. Missing/invalid effective
  `autoHeal.botId`, unavailable BaaS wiring or unavailable draft storage must be
  reported honestly; never report a missing experiment or Git baseline.
  `available: true` is readiness evidence, not proof of model quality or that a
  later dispatch will succeed. A failed HTTP check is not proof of missing
  configuration, and an unrelated 404 is not an onboarding fallback signal.
- `POST /api/workflows/:workflowId/ai-drafts` accepts only the draft request
  `{ requestId, mode, prompt, currentYaml }` and returns `{ draftId, status }`.
  `create` means extend the current workflow; `modify` means adjust it, not
  create a new workflow. The prompt is nonblank, at most 4,000 Unicode code
  points, and is preserved without trimming. YAML is complete valid UTF-8, at
  most 100,000 bytes, preserving workflow ID and version. Browser actor, Bot,
  repository, branch, path or baseline fields confer no authority.
- `GET /api/workflows/:workflowId/ai-drafts/:draftId` returns
  `{ draftId, workflowId, status, inputSpecDigest, error?, result? }`.
  States are `pending`, `running`, `dispatch_uncertain`, `completed`, `failed`
  and `cancelled`. Only `completed` carries `result: { yaml, summary }`.
- `POST /api/workflows/:workflowId/ai-drafts/:draftId/cancel` durably marks a
  nonterminal **local draft task** cancelled. Terminal tasks stay terminal.
  There is no supported BaaS stop operation: this does not promise that the
  message or remote Bot execution has stopped. Late acknowledgements/results
  cannot revive a cancelled task or be applied to the Editor.
- Candidate validation still uses the existing read-only
  `POST /api/workflows/validate` with `{ spec }`; only a valid response with a
  valid `normalizedSpec` can become an applicable preview. No save API is
  invoked by validation or Apply.

## Capability reasons and provider failures

The browser maps the following server codes to actionable AutoHeal/BaaS text,
not experiment/Git diagnostics. Keep the distinctions; a generic unavailable
banner must not obscure whether configuration, binding, credentials, dispatch,
provider execution, reply validation, local deadline or edit authorization failed.

| Code | Meaning and user action |
| --- | --- |
| `AUTO_HEAL_BAAS_NOT_CONFIGURED` | Effective server AutoHeal/BaaS configuration is missing or invalid; an administrator must check `autoHeal.botId` and the existing BaaS configuration. The browser cannot supply a replacement Bot. |
| `AUTO_HEAL_BOT_UNAVAILABLE` | The configured AutoHeal Bot has no valid BaaS binding. Check that exact server-owned Bot identity and its BaaS binding; do not switch to onboarding or append a user suffix. |
| `BAAS_AUTH_FAILED` | BaaS authentication/authorization failed; check server credentials and permissions. This may be discovered during dispatch **or polling** and does not prove that no remote task exists or that it stopped. |
| `BAAS_REQUEST_REJECTED` | BaaS explicitly rejected dispatch. This is known rejection, not an unacknowledged submission. |
| `BAAS_GENERATION_FAILED` | Polling reports that BaaS generation failed; no candidate may be applied. |
| `BAAS_PROVIDER_REJECTED` | Polling reports rejection by the BaaS provider; check upstream provider configuration/permissions, not Git artifacts. |
| `INVALID_GENERATION_RESULT` | The provider reply could not be parsed or failed the workflow schema/identity/version checks; the core rejects it rather than exposing or applying invented YAML. |
| `EXECUTION_TIMEOUT` | The local server task deadline expired while waiting for a known message. Check that original message before any new dispatch; this does not prove the remote task stopped. |
| `DISPATCH_UNCERTAIN_TIMEOUT` | The local deadline expired without a reliable dispatch acknowledgement. BaaS may have accepted the message; do not blindly redispatch or promise remote result recovery. |
| `EDIT_PERMISSION_REVOKED` | The submitting actor no longer has workflow edit permission. Local processing ends; an administrator must check access. This is not a remote-stop acknowledgement. |

Capabilities use `AUTO_HEAL_BAAS_NOT_CONFIGURED` or
`AUTO_HEAL_BOT_UNAVAILABLE` for their respective unavailable reasons. Unknown
helpful server errors remain visible with a safe generic context; an absent
reason is explicitly unspecified, not evidence of a particular missing field.
Failed/timeout capability HTTP checks remain **unconfirmed**. None of these
messages changes draft state, recovery policy or the local-only cancel contract.

## Host execution Plugin API

The declarations in [the server contract](../server/contracts/workflow-ai-draft.ts)
are authoritative. The call direction is Workflow core to the host adapter:

```ts
resolveTarget(): Promise<{ botId: string }>
dispatch(request: { actorId, envelope }): Promise<{ jobId: string }>
poll(request: { actorId, envelope, jobId }): Promise<
  | { status: 'running' }
  | { status: 'failed'; error: string }
  | { status: 'completed'; result: { yaml: string; summary: string } }
>
```

`resolveTarget` validates the effective server AutoHeal binding **and existing
BaaS transport readiness**; it is not a browser selection or onboarding lookup.
It returns only `{ botId }`, never transport credentials. That validated target
is frozen in the persisted envelope. `dispatch` sends the generation request
through the existing BaaS transport once. Its `jobId` is the acknowledged
**BaaS message ID**, not an AIStudio execution ID, Git ref or browser request ID.
`poll` follows the acknowledged, frozen BaaS message ID to translate BaaS
status/result into the union above. Polling checks BaaS transport readiness,
not the current AutoHeal Bot setting: a changed or missing current `botId` must
neither retarget an existing message nor block its polling. Transport/credential
availability can still affect that read. Transport uncertainty is not a terminal
model failure. The port has no remote `stop` method or Git result-reader contract.

```text
schemaVersion = workflow-yaml-draft/v1
taskType = workflow_yaml_draft
taskId = server-created draft ID
target = { botId: <effective server autoHeal.botId, verbatim> }
input = {
  schemaVersion: workflow-yaml-draft-input/v1,
  workflowId, mode, prompt,
  workingCopy: { yaml: <complete submitted YAML>, sha256: <exact UTF-8 digest> }
}
```

The provider returns complete YAML and a bounded summary as data, not a script,
patch, filesystem path or Git artifact. The server validates the candidate,
workflow identity and unchanged version (value and type) against the frozen
working copy before exposing a completed result. A lifecycle success alone is
not successful YAML generation; missing or invalid output fails closed.

## Identity, dispatch uncertainty and recovery

Request identity is durable and scoped to actor/workflow/`requestId`. Reusing it
with changed mode, prompt or YAML is a conflict. The server persists the task
and a dispatch-uncertain fence before calling BaaS; it must not blindly resend
an uncertain submission. A positively rejected submission can be terminal
(`AUTO_HEAL_BOT_UNAVAILABLE`, `BAAS_AUTH_FAILED`, `BAAS_REQUEST_REJECTED`). A lost
acknowledgement is different: BaaS may already have accepted the message and
the remote Bot may still be working. Without an acknowledged message ID there
is nothing reliable to poll remotely. Keep `dispatch_uncertain` recoverable for
local status/cancellation/deadline handling, not as a promise to discover the
remote result or automatically retry delivery.

Browser recovery reuses the same `requestId` if the draft acknowledgement was
lost, or polls the original `draftId` when known. A submitted/possibly submitted
attempt remains recoverable across subsequent operational checks: a failed or
unavailable capability check while recovering must not discard its request ID.
The next explicit retry still uses that same request and complete input, not a
fresh generation. Readiness failure is not evidence that a prior BaaS message
was never accepted.

A transient state conflict, read failure or validation transport failure must
not create a new job. A completed draft whose read-only validation failed can
be queried and validated again without regenerating. HTTP authentication,
authorization and rate-limit failures (including 401, 403 and 429) are
operational; a bare HTTP 4xx response is not proof of invalid candidate YAML.
Keep the acknowledged draft identity and retry its read-only validation instead
of submitting a replacement generation. Deterministic local parse/identity/
version/schema rejection, an authoritative validation result with `valid: false`
or an invalid normalized candidate remain blocked and are distinct from an
HTTP failure. Explicit cancellation or a changed prompt/mode/editor context
still invalidates the old attempt. No substitute template is produced on any
failure, and no recovery failure auto-applies or saves a candidate.

`inputSpecDigest` is the lowercase hex SHA-256 of the **exact UTF-8 YAML bytes**
submitted in `currentYaml`, not normalized YAML and not the semantic signature
used to detect stale Editor previews. Every poll checks workflow/draft identity
and a nonempty 64-hex digest. Where SubtleCrypto is available the browser also
compares the locally computed exact digest; otherwise it relies on the server's
immutable request-to-draft/input binding, not a guessed digest. Preserve the
complete applied document and never silently truncate it to fit the limit.

Browser polling remains three seconds with a 15-minute local wait bound.
Server reconciliation admits provider polls through a durable due-time slot
(15 seconds), bounds CAS contention and never reopens dispatch. The server's
30-minute task deadline is local bookkeeping; neither deadline proves the
remote Bot has stopped. Expired pending records fail before any new dispatch, including idempotent
replay and background recovery. Deadlines are rechecked after asynchronous
provider work. Live edit permission is rechecked after polling, before result
acceptance, and immediately before returning draft details. Cancellation and
terminal transitions fence late work.
Closing/changing context aborts local waits and attempts the local cancellation
endpoint with its own bounded signal when a draft ID is known. If submission
or cancellation acknowledgement is lost, local Editor safety still holds,
but server cancellation and remote termination cannot be claimed.

## Editor validation and Apply guards

Generation captures an immutable copy of the current applied WorkflowSpec,
serializes its full YAML without canvas coordinates, and preserves raw user
requirements. An unapplied YAML/configuration buffer or in-progress save blocks
generation/Apply instead of being silently included or overwritten.

Both server and browser validate untrusted YAML as bounded JSON-compatible
YAML 1.2 data. Numeric versions and non-boolean `on`/`off` IDs remain supported;
recursive/excessively expanded aliases and invalid structures are rejected.
The browser checks identity/version, duplicate node IDs, dependency references,
known array-form result targets and dependency cycles. Runtime object-form
`onResult`/extensions remain subject to authoritative server validation rather
than a narrower invented browser protocol. Existing node coordinates survive;
model-supplied coordinates are not accepted as layout authority.

Preview diffs come from the final normalized candidate, not model assertions.
Node removal or top-level metadata changes require explicit confirmation.
Request/context guards reject late responses; workflow/document/layout changes
mark results stale. Failed Editor imports are not successful Apply. Successful
Apply updates memory, marks dirty and opens comparison only; the existing
explicit Save and Deploy actions remain separate. Provider errors cannot block
ordinary manual editing or database-only save.

## Assembly, compatibility and verification

Hosts inject the existing BaaS adapter using their verified principal resolver
and current AutoHeal configuration. Credentials/private endpoints stay outside
the public repository. Missing provider configuration disables AI with an
explanation, not fake success. Draft storage must be available (the existing
`workflow_ai_drafts` migration); a noop database cannot provide durable recovery.
Hosts own bounded reconciliation, logging hygiene and shutdown. Authorization
must resolve consistently across instances; a process-local login alias cache
must not establish either a grant or revocation for another host's tasks.
The internal mount supports canonical actor-ID administrator entries and
explicit workflow edit grants, checked against live storage. A username-only
admin entry does not elevate this draft service without one of those grants. Ingress origin
validation uses a validated server-owned `publicOrigin`, not arbitrary forwarded
headers; complete YAML and credentials must not leak into ordinary logs.

This change replaces the server execution provider and related UI/docs, not the
browser endpoints, task states, exact-byte digest or Apply/save/deploy contract.
Propagation includes both host adapters/configuration and their port conformance
tests; browser consumers retain the same async wire format. The retired
experiment/Git protocol and deleted fixture files are not release prerequisites.
Existing unrelated repair or Pack publication paths are not changed by this
provider selection.

- [Original UI intent and transport correction](specs/2026-11-13-workflow-ai-draft-editor.md)
- [Editor document boundary](workflow-editor-draft-boundary.md)
- Server source/tests: `server/contracts/workflow-ai-draft.ts`,
  `server/services/workflow-ai-draft-service.ts` and its validation/service tests;
  host adapter/runtime tests verify AutoHeal selection and BaaS message behavior.
- Browser coverage: API/CSRF integration, `web/services/workflow-ai-*`, assistant
  hooks/components and Editor integration regressions. Acceptance cases live in
  executable tests, not a removed shared-fixture path.

Synthetic tests establish local contract behavior only. Historical validation
reports do not prove the new provider, live Bot configuration, BaaS credentials,
remote model quality, host integration or deployment; report those gaps separately.
