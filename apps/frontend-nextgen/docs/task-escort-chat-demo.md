# Task Escort chat demo

## Scope and entry points

This is a presentation-only chat demo, separate from the real Task Escort app at
`/evolution-factory/task-escort`. It does not read real workflows, send chat
messages, run tasks or deploy workflows. Its runs, permissions, metrics and node
states are explicitly simulated. Demo edits and saves affect only local React
state; refreshing or unmounting the demo clears them.

- `/demo/task-escort` is a public top-level route outside `AppLayout`. Its
  [standalone host](../src/pages/Demo/TaskEscort/index.tsx) has read-only sample
  chat content and a disabled sender; it does not initialize chat or auth services.
- The standard conversation header puts **任务护航** after **资源管理** and before
  **副屏**, when those existing controls are visible. Existing
  `HISTORY_SEARCH_ENABLED`, `showResourceManager` and `showSidePane` flags still
  govern their own controls.
- The real Task Escort routes, services and fixtures are unchanged. The demo is
  not a fallback for real API failures.

## State and integration boundaries

[ChatHeaderActions](../src/pages/Workspace/Chat/components/ChatHeaderActions.tsx)
forwards the `escort` panel selection to
[useChatHeaderPanels](../src/pages/Workspace/Chat/hooks/useChatHeaderPanels.ts).
Escort shares the current dev header-panel mutex: opening it closes resources
and displaces an open SDK sidepane without clearing its tabs; closing all header
panels restores the displaced sidepane. SDK open-state events close header
panels, including Escort. Existing task-panel recovery and history-readiness
wiring remain in place.

The current standard runtime is assembled in
[StandardConversationStageContainer](../src/pages/Workspace/Chat/StandardConversationStageContainer.tsx),
not `ConversationPage`. Its only integration change is to delegate header-panel
rendering to
[ConversationHeaderPanels](../src/pages/Workspace/Chat/components/ConversationHeaderPanels.tsx).
That component preserves the always-mounted detail/history sidebar and its
remembered closing-transition content. Escort does not open an empty inline
sidebar. Its drawer remains mounted while closed, preserving local drafts when
reopened in the same conversation. A Bot/session key change remounts the demo
and resets header selection, so drafts cannot leak to another conversation.
Within a drawer, each accessible simulated workflow owns its own draft.

## Verification and limits

From this app directory, using already available dependencies:

```bash
npm run check:open-core
npm run ci:guardrails
npm test -- --runInBand \
  test/components/Workspace/TaskEscortDemo.test.tsx \
  test/components/Workspace/TaskEscortWorkflowSwitch.test.tsx \
  test/pages/Workspace/Chat/TaskEscortEntry.test.tsx
```

The three targeted suites cover the real demo drawer/editor/workflow switching,
header order and accessibility state, display flags, panel mutual exclusion and
SDK state callbacks, displaced-sidepane restoration, Bot/session resets, draft
mounting lifetime, and separation of public demo and real Task Escort routes.
The tests adapt the accepted demo's older panel assumptions to current dev:
detail/history already participate in the SDK sidepane mutex, and Monitor's
optimistic state is followed by the SDK's real open-state event.

These checks do not establish full-app typecheck/build success, browser visual
parity, OCB overlay/route composition, deployment or real-backend behavior.
Host assembly and independent review remain separate integration evidence.
