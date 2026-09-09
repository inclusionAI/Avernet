# Insight-backed Repair context

The ClawInsight admin dialog calls the Service API
`POST /api/insight/v1/admin/improvements/:improvementId/execute-once`.
Its optional `diagnosticMode` is `observe` or `deep`; omitted values keep the
existing `observe` default and invalid values are rejected. The admin dialog
visibly selects `deep` by default and submits `observe` when it is unchecked.
This does not change owner-created task defaults, persistent authorization,
fixed Bot/environment scope, or approval requirements for applying a plan.

When `RepairTaskService.createTask` resolves an Insight improvement, it freezes
one `insightSource.evidenceTaskRefs` entry per evidence item, including separate
tasks in the same Session. In addition to `sessionId`, `taskIndex`, and `ordinal`,
new tasks contain `taskDescription` (the evidence task title), `failureClass`,
and nullable `reasoningSummary` (the Judge verdict). These hints come from the
server-side improvement detail, not client-supplied evidence. Descriptions,
verdicts and failure classes pass through the existing persistable redaction
helper, with byte limits of 1024, 2048 and 128 respectively and explicit
`[TRUNCATED]` markers for oversized text. No evidence entry is dropped.

The persisted hints are returned in task detail and in Repair bootstrap for
both planning and application, including later executions. They remain usable
if the optional full `insightPlanSource` cannot be loaded or safely projected.
They are diagnostic evidence, not authorization or a replacement for runtime
verification. The complete frozen PlanSource remains the source for full traces.

This is an additive contract change affecting the admin HTTP client, Repair
runtime/bootstrap and task-detail UI. Historical tasks may lack the three new
fields; readers must tolerate this. No database migration, new provider or
configuration is required. Route, dialog and Repair runtime regression tests
cover the boundary.
