# Auto-heal rerun target

The Bot used to diagnose a failed workflow is not the Bot that should execute
its repaired workflow. Diagnosis may use a dedicated assistant or the LLM
fallback. A rerun must use the selected source run's persisted Bot identity and
lifecycle stage, not `autoHeal.botId`.

## Service API

`POST /api/auto-heal/run` accepts:

```json
{
  "workflowId": "example-workflow",
  "sourceFlowId": "original-flow-id",
  "params": { "example": "explicit override" }
}
```

- `workflowId` and `sourceFlowId` are required. Requests without a source run
  fail before any message is sent; there is no fallback to the diagnosis Bot.
- The server loads the source run, checks its workflow and the actor's
  authorization, and derives `origin_bot_id` and the lifecycle stage from
  persisted data. Client-supplied Bot, owner, or stage values are not routing
  authority. A non-admin must have execute/edit permission and be the persisted
  Bot owner; merely being able to view a run does not authorize owner-message
  impersonation. Authenticated host administrators retain explicit override.
- If `params` is omitted, the source run's stored workflow parameters are used.
  An explicitly supplied object replaces them. Non-empty malformed stored
  parameters fail closed rather than silently starting with empty parameters.
- Only `draft`, `verify`, and `online` lifecycle values are accepted. Missing
  stage metadata retains the BaaS `online` default; malformed stage metadata
  is rejected rather than silently rerouting a draft Bot.
- A fresh `auto-heal-run` session is used. The source cron/chat session is not
  reused, and the source run does not need an `input_json.command`.
- The message retains the structured `run_workflow` action. Successful delivery
  means BaaS accepted a message, not that the workflow completed successfully.
- BaaS failures remain failures; HTTP status, available business error code,
  and sanitized diagnostic text are preserved without echoing request bodies,
  validation inputs, or credentials.

## Workbench orchestration

The AutoHeal panel performs save, deploy, and rerun as separate operations:

1. Save the repaired specification.
2. Deploy with `autoRun: false` so the legacy deployment callback does not send
   a second message to the diagnosis Bot.
3. If the user enabled automatic rerun, invoke the source-aware `/run` endpoint
   once, only after deployment succeeds. Manual rerun uses the same endpoint.
4. If delivery fails after deployment, preserve the deployed state and offer
   rerun retry; do not ask the user to save/deploy again.

The legacy shared client request types and deploy callback remain compatibility
surfaces. New workbench callers use the source-aware request type/hook and the
explicit sequence above. Clients calling `/run` directly must supply
`sourceFlowId`; deploy-only callers can keep using `autoRun: false`.

## Composition and rollout

The public ClawWeb host and the OCB archive host mount the same workflow rerun
router. The reusable service receives its run repository, permission checks,
and message sender through composition; it does not own BaaS credentials or
choose private endpoints.

Deploy the matching Avernet public package/UI and OCB host change together.
Older `/run` requests without a source identifier receive an explicit client
error rather than dispatching to an unrelated Bot. This change does not
reactivate, replace, or grant privileges to the configured diagnosis Bot.
