# Retire conversation-file automatic knowledge ingestion

## Approved scope
Stop turning TC conversation attachments into ECB knowledge automatically.
Preserve ordinary upload/materialization/content/reference behavior, explicit
knowledge uploads and Yuque imports, existing knowledge and history. Do not fix
bot_uuid compatibility, migrate data, cancel existing generic Jobs, or deploy.

## Acceptance
- Legacy upload-complete, materialize-status, and materialized callback do not
  resolve/invoke the knowledge observer.
- Formal OpenAPI session-file complete/status do not resolve/invoke it either.
- Auth, trusted binding resolution, primary resource service calls, and response
  contracts are preserved.
- In the paired ECB change (0731_ecb_dev), authenticated resource-ready events
  return 410 TC_RESOURCE_READY_DISABLED without persistence or downstream I/O;
  runtime no longer constructs the automatic adapter/inbox/worker/poller.
- Context and token APIs stay available; no existing inbox/knowledge deletion.

## Delivery
Avernet and OCB target dev; ECB targets 0731_ecb_dev, rebased first.
The initial implementation was kept local for review. On 2026-11-09, delivery
of the Avernet-only PR to dev was approved. ECB and OCB gitlink delivery remain
separate; no deployment or production data mutation is included. Update the OCB
gitlink only after a real merged commit is available in the internal mirror.
