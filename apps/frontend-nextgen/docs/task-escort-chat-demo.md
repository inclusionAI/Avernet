# Task Escort chat workflow selector and standalone demo

## Separate live and demo entry points

The standard conversation header's **任务护航** drawer uses the authenticated
workflow-task list, not a fixture catalogue. The independent
`/demo/task-escort` route remains a presentation-only sandbox outside
`AppLayout`. Its [standalone host](../src/pages/Demo/TaskEscort/index.tsx) uses
local workflows, simulated visibility, metrics, runs and editable React drafts.
It does not initialize a real chat connection or call workflow APIs.
Neither entry replaces the management workspace at
`/evolution-factory/task-escort`.

The shared header positions Escort after **资源管理** and before **副屏** when
those controls are enabled. Existing history/resource/sidepane display flags
still apply. [useChatHeaderPanels](../src/pages/Workspace/Chat/hooks/useChatHeaderPanels.ts)
retains panel mutual exclusion, resource dismissal, SDK sidepane displacement
and restoration without clearing SDK tabs.

## Live workflow request and state ownership

[StandardConversationStageContainer](../src/pages/Workspace/Chat/StandardConversationStageContainer.tsx)
passes the existing task-composer context through
[ConversationHeaderPanels](../src/pages/Workspace/Chat/components/ConversationHeaderPanels.tsx)
to [TaskEscortPanel](../src/components/Workspace/TaskEscortPanel/index.tsx).
The drawer and the composer both use
[useWorkflowList](../src/hooks/useWorkflowList.ts),
[taskService.fetchWorkflows](../src/services/tasks/taskService.ts) and
[workflowController](../src/services/backendApi/workflows/workflowController.ts).
No separate client-side permission algorithm is introduced.

The executable-list request is a same-origin authenticated GET:

```http
GET /api/workflows?permission=execute&botOwnerId=<asset-owner>&botId=<bare-bot-id>
```

The Bot ID's composite owner suffix supplies the asset owner when present;
the separate context owner is the fallback for a bare Bot ID. Asset scope does
not authenticate the actor. Authentication stays in the existing HTTP client
and trusted backend ingress. The backend, not a local `canView` flag, decides
which workflow IDs the caller may execute. The view-list helper remains
available to existing management/read consumers with unchanged semantics.

No request is made without a complete Bot/user context. The drawer loads only
while open, refreshes on reopening, and clears scope-dependent state when its
Bot, context user or session changes. The shared hook deduplicates same-scope
requests, masks old-scope rows immediately and rejects stale responses,
including A-to-B-to-A and context-removal races. Loading, empty, missing-context
and request-failure states are distinct; failures offer retry, never a view-list
or fixture fallback.

## List-only boundary

A selected real workflow displays its server-provided title and ID. The chat
drawer does not attach simulated runs, metrics, nodes or editable drafts to
that real identity. Those reads are not connected here and are explicitly
marked unavailable; execute permission is not assumed to grant view or edit
permission. The selector issues no workflow detail, run, save or deployment
request. The standalone demo retains its existing local run-detail navigation
and editor for design review.

## Backend prerequisite and release order

The executable-list backend contract is introduced by
[Avernet PR #4275](https://github.com/inclusionAI/Avernet/pull/4275).
Frontend implementation or a successful HTTP response does not establish that
the running service implements it: older servers ignore the `permission`
query and can return a view-filtered list with HTTP 200. There is no negotiated
capability marker in that response. **Do not enable/deploy this frontend against
that older backend.** Integrate and deploy a compatible workflow API first,
then verify actual execute-only filtering under a non-admin authenticated user.
The proposed HTML design is not evidence of deployment. Server-admin policy and
runtime execution checks remain owned by the backend contract, not this UI.

The list response does not establish command-resolution or task-launch success
for execute-only users; those actions still require their own backend checks.
No production deployment, permissions-table mutation or real workflow execution
is part of the selector's local validation.

## Verification

From this app directory:

```bash
npm run check:open-core
npm run ci:guardrails
npm test -- --runInBand \
  test/hooks/useWorkflowList.test.tsx \
  test/services/backendApi/workflows/workflowController.test.ts \
  test/services/tasks/taskService.test.ts \
  test/components/Workspace/TaskEscortPanel.test.tsx \
  test/components/Workspace/TaskEscortPanel.boundary.test.ts \
  test/pages/Workspace/Chat/TaskEscortContext.test.tsx \
  test/components/Workspace/TaskEscortDemo.test.tsx \
  test/components/Workspace/TaskEscortWorkflowSwitch.test.tsx \
  test/pages/Workspace/Chat/TaskEscortEntry.test.tsx
```

These suites cover request semantics, context races, list-only rendering,
errors/retry, chat wiring and isolation from the standalone demo. Full-app
checks, host assembly, independent review and deployed authorization validation
are separate evidence; record only checks actually executed.
