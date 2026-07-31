# task-panel (taskPanel UMD asset)

Open-source **任务执行 workflow 副屏** component, bundled as a UMD asset and
served by the backend. Render format mirrors
`ocb-public/src/bcs/assets/panel/src/StateMachineRunView.tsx` (bcsPanel).

## What it renders

Consumes `TaskGraphView` from `GET /api/tasks/{task_id}/graph` and draws the
task's dynamic execution DAG (SVG nodes + edges, status tone, edge states),
polling every 3s while `root_phase` is non-terminal. Empty graph (DRAFTING /
DEFINED, before `spawn_build_dag`) shows a centered "初始化任务节点". Click a
node → modal with `GET /api/tasks/{task_id}/nodes/{node_id}`.

State machine aligned values (spec §2/§3.3):

- `root_phase`: drafting / defined / executing / reviewing / done / cancelled / failed
- `node.status`: pending / running / done / failed / skipped / human_required

## Why a UMD asset under backend/assets

The frontend workbench (`ocb/src/frontend`) is owned by the frontend team and
not editable by other teams. So the task panel ships as a self-contained UMD
bundle from the backend (this directory), loaded by the frontend's generic
`UmdPanel` (`type='umd'`). **Zero frontend code changes.**

## Build

```bash
cd ocb-public/src/backend/assets/task-panel
npm install      # react/react-dom are peerDeps; @types/* + vite devDeps
npm run build    # → dist/index.umd.js (UMD, name="taskPanel", external React)
npm run typecheck
```

The backend serves `dist/index.umd.js` via a StaticFiles mount at `/assets`
(see `adapters/http/app.py`). Singlebox URL:
`http://localhost:8888/assets/task-panel/dist/index.umd.js`.

## How the副屏 opens

The task-recognition skill emits (in its chat reply after `POST /api/tasks`):

```
<AixUI
  type="panel"
  component="taskPanel.TaskWorkflowView"
  cdn="http://localhost:8888/assets/task-panel/dist/index.umd.js"
  entry="TaskWorkflowView"
  tab='{"id":"task-<task_id>","title":"任务:<title>","closable":true}'
  params='{"taskId":"<task_id>"}'
/>
```

The SDK `resolveBusinessEntry` (`businessUtils.js`) sees the `cdn` attribute
→ routes to `type='umd'` → `UmdPanel` loads the bundle → mounts
`TaskWorkflowView` with `{taskId, payload, ...}`. Works in single-bot chat
(`ChatPage`) and协作群 (`GroupChatPage`) identically — both mount
`<ChatLayout.Panel bridge={chatBridge}>` and parse the same `<AixUI>` tag.

## Props contract (from UmdPanel)

`TaskWorkflowView` receives `props.taskId` (or `payload.taskId` /
`params.taskId`), plus optional `autoRefresh`, `pollingInterval`, `onAction`,
`onInteraction`, `eventEmitter`. Data fetched via raw `fetch('/api/tasks/...')`
(relative; the frontend proxy routes `/api/*` to the backend — same-origin, no
CORS).

## Follow-ups (v1.5)

- Sub-DAG drill-down for `coop_group` nodes (`GET /nodes/{id}/sub-dag`, recursive `GraphCanvas`).
- WS `/api/tasks/{id}/graph/stream` to replace 3s polling.
- In-panel accept/amend actions for `graph_status=awaiting_human_*`.
