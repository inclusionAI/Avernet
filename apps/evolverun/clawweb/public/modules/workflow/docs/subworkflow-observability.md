# Subworkflow run observations

`GET /api/runs/:flowId/subworkflows` returns best-effort, direct child
observations for a production run. The route requires the host to supply a
verified whole-workflow view authorizer; it independently authorizes the parent
and each child. Missing, trial, and unauthorized runs are omitted without
revealing their identifiers. An unavailable database or authorizer fails closed.
The endpoint does not accept query parameters and sets `Cache-Control: no-store`.

Each returned item includes `parentNodeId`, `parentAttempt`, `childFlowId`,
`childWorkflowId`, status, timestamps, and selected child node summaries.
`parentAttempt` is a positive integer when recorded on the chosen association
event; if the newest event omits it, earlier events for the same known parent node can supply it. It is `null` when evidence is missing or invalid. It must not be inferred from the
child attempt or from the parent's latest node state. Clients that predate this
field can ignore it; clients must handle `null` and older servers that omit it.

The result always declares `completeness: "best_effort"`: at most 200 recent
`subworkflow_linked` / `subworkflow_finished` events are examined, up to 20
distinct child candidates are checked, and each visible child includes at most
50 node summaries. A missing link is not proof that no child was created. The
route relies on the child having its own readable `flow_runs` record, and an
event may be delayed, missing, duplicated, or lost. This endpoint is not an
authoritative relationship store and does not guarantee creation idempotency,
crash recovery, pagination, or historical completeness.

The route grants access only to its own limited response. A child-run details,
node, step, log, or archive endpoint must independently enforce resource-level
authorization; a visible child ID alone does not confer permission to access
those endpoints. Hosts must wire and validate the authorizer and other routes
separately.
