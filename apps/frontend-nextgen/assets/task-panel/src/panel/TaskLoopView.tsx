import React, { useEffect, useSyncExternalStore, useMemo, useRef, useState } from 'react';
import { TaskPanel, type TaskPanelProps } from './TaskPanel';
import { TaskSwitcherControl } from './v2/TaskSwitcherControl';
import { TaskPanelHostContext } from './hostContext';
import { normalizeTaskStatus } from '../data/taskStatus';
import { createTaskClient, recordTitle, unwrapResponse, type TaskListRecord } from '../data/taskClient';
import { filterTasksForCurrentConversation, firstTaskNodeTarget, toSwitcherItem } from '../data/taskSwitcher';
import type { TaskDashboardResponse } from './contract';
import type { TaskPanelController } from '../controller/createTaskPanelController';
import type { TaskPanelHost } from '../contracts';
export interface TaskLoopViewProps extends TaskPanelProps {
  host: TaskPanelHost; controller?: TaskPanelController;
  /** Drawer host owns selection presentation, never the task rules. */
  onSelectTask?: (taskId: string) => void;
}
export function TaskLoopView({ host, controller, onSelectTask, ...props }: TaskLoopViewProps): React.ReactElement {
  const snapshot = useSyncExternalStore(controller?.subscribe ?? noopSubscribe, controller?.getSnapshot ?? emptySnapshot, controller?.getSnapshot ?? emptySnapshot);
  const selectedNav = useMemo(() => controller ? controller.nav(props.taskId) : props.nav, [controller, snapshot?.navByTask, props.taskId, props.nav]);
  const [records, setRecords] = useState<readonly TaskListRecord[]>(snapshot?.records ?? []);
  const [attention, setAttention] = useState<Record<string, true>>({});
  const selectionAbort = useRef<AbortController | null>(null);
  const userId = host.getViewer()?.userId;
  useEffect(() => () => { selectionAbort.current?.abort(); }, [controller, host, userId]);
  const conversationId = snapshot?.conversation?.sessionId;
  const associatedTaskIds = snapshot?.associatedTaskIds;
  const associatedIdsRef = useRef(associatedTaskIds);
  associatedIdsRef.current = associatedTaskIds;
  const owner = conversationId?.match(/:user:([^:]+)$/)?.[1] || userId;
  useEffect(() => {
    setAttention({});
    setRecords(userId ? snapshot?.records ?? [] : []);
    if (!controller || props.switcher || !userId || !owner || !props.taskId) return;
    const abort = new AbortController(); let running = false; let previous: readonly TaskListRecord[] | null = null;
    const refresh = async () => {
      if (running || document.visibilityState === 'hidden') return;
      running = true;
      try {
        const rows = await createTaskClient(host).list(owner, abort.signal);
        if (abort.signal.aborted) return;
        const scoped = filterTasksForCurrentConversation(rows, props.taskId, associatedIdsRef.current);
        if (previous) {
          const updates = scoped.filter(row => row.task_id !== props.taskId && previous?.some(old => old.task_id === row.task_id &&
            (old.gmt_modified !== row.gmt_modified || normalizeTaskStatus(old.status) !== normalizeTaskStatus(row.status))));
          setAttention(value => ({ ...value, ...Object.fromEntries(updates.map(row => [row.task_id, true as const])) }));
        }
        previous = scoped; setRecords(rows);
      } catch { if (!abort.signal.aborted) setRecords([]); /* Supplemental failure never breaks the displayed task. */ }
      finally { running = false; }
    };
    void refresh(); const timer = setInterval(() => { void refresh(); }, 60_000);
    const visible = () => { void refresh(); }; document.addEventListener('visibilitychange', visible);
    return () => { abort.abort(); clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [controller, host, userId, owner, conversationId, props.switcher, props.taskId]);
  const current = records.find(r => r.task_id === props.taskId);
  const items = filterTasksForCurrentConversation(records, props.taskId, associatedTaskIds)
    .map(record => ({ ...toSwitcherItem(record), hasUnhandledUpdate: Boolean(attention[record.task_id]) }));
  useEffect(() => {
    if (!controller) return;
    if (snapshot?.activeTaskId !== props.taskId) controller.adoptPresentedTask({ id: `task-${props.taskId}`, type: 'taskPanel.TaskLoopView', title: current ? recordTitle(current) : '任务', closable: true, params: { taskId: props.taskId } });
  }, [controller, props.taskId]);
  const selectTask = (id: string) => {
    if (!controller || id === props.taskId) return;
    setAttention(value => { const next = { ...value }; delete next[id]; return next; });
    const tab = { id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: items.find(i => i.taskId === id)?.title ?? '任务', closable: true, params: { taskId: id } };
    if (onSelectTask) { controller.adoptPresentedTask(tab); onSelectTask(id); }
    else controller.openTask(tab, 'explicit');
    selectionAbort.current?.abort();
    const abort = new AbortController(); selectionAbort.current = abort;
    const conversation = controller.getSnapshot().conversation;
    void host.transport.requestJson(`${host.taskApiBase ?? '/openapi/v1/collaboration/tasks'}/dashboard?task_id=${encodeURIComponent(id)}&include_action_log=true`, abort.signal).then(raw => {
      if (abort.signal.aborted || host.getViewer()?.userId !== userId ||
        controller.getSnapshot().conversation !== conversation || controller.getSnapshot().activeTaskId !== id) return;
      const first = firstTaskNodeTarget(unwrapResponse(raw) as TaskDashboardResponse, records.find(record => record.task_id === id));
      if (first) controller.nav(id).selectNode(first.nodeId, first.target);
    }).catch(() => { /* Selection succeeds even if first-node location is unavailable. */ });
  };
  // Single-task viewing is intentional; do not remove the floating entry when only one item remains.
  const switcher = props.switcher ?? (controller && items.length > 0 ? <TaskSwitcherControl key={props.taskId} items={items} activeTaskId={props.taskId} onSelectTask={selectTask} /> : null);
  return <TaskPanelHostContext.Provider value={host}>
    {snapshot?.navigationError && <div role="alert">{snapshot.navigationError}</div>}
    <TaskPanel {...props} taskApiBase={host.taskApiBase ?? props.taskApiBase} userId={host.getViewer()?.userId} nav={selectedNav} switcher={switcher} />
  </TaskPanelHostContext.Provider>;
}
const noopSubscribe = () => () => {};
const emptySnapshot = () => null;
