import type { ConversationRef, ConversationTarget, HistoryMessage, OpenSource, RecoveryState, TaskPanelDeclaration, TaskPanelHost } from '../contracts';
import { parseTaskPanelDeclarations } from '../history/parseTaskPanelDeclarations';
import { createTaskClient, mainSessionId, recordTitle, type TaskListRecord } from '../data/taskClient';
import type { TaskNavRecord } from '../panel/v2/taskPanelNav';
export interface TaskPanelSnapshot {
  conversation: ConversationRef | null; activeTaskId: string | null; associatedTaskIds: readonly string[];
  navByTask: Record<string, TaskNavRecord>; recovery: RecoveryState;
  records: readonly TaskListRecord[]; navigationError: string | null;
}
const emptyNav = (): TaskNavRecord => ({ selectedNodeId: null, drillStack: [] });
export function createTaskPanelController(host: TaskPanelHost) {
  let snapshot: TaskPanelSnapshot = { conversation: null, activeTaskId: null, associatedTaskIds: [], navByTask: {},
    recovery: { status: 'idle' }, records: [], navigationError: null };
  const listeners = new Set<() => void>();
  const update = (patch: Partial<TaskPanelSnapshot>) => { snapshot = { ...snapshot, ...patch }; listeners.forEach(fn => fn()); };
  const client = createTaskClient(host);
  let epoch = 0; let focus = 0; let viewerId: string | null = null;
  let recoveryAbort = new AbortController(); let navigationAbort = new AbortController();
  let cursor: string | null = null; let pagesRead = 0; let historyDone = false;
  let scanning = false; let disposed = false; let recoveryStarted = false;
  let recoveryPhase: 'association' | 'history' = 'association';
  let navigationTargetSession: string | null = null;
  const cursors = new Set<string>(); const tabs = new Map<string, TaskPanelDeclaration>();
  const origins = new Map<string, { handle: unknown; sessionId: string | null }>();
  const valid = (generation: number, revision: number) => !disposed && generation === epoch && revision === focus && host.getViewer()?.userId === viewerId;
  const declaration = (taskId: string, title = '任务'): TaskPanelDeclaration => ({ id: `task-${taskId}`,
    type: 'taskPanel.TaskLoopView', title, closable: true, defaultWidth: 420,
    params: { taskId, userId: host.getViewer()?.userId, taskApiBase: host.taskApiBase, apiBaseUrl: '', bcsBaseUrl: '' } });
  const openTask = (tab: TaskPanelDeclaration, source: OpenSource, present = true) => {
    if (disposed || !tab.params.taskId.trim()) return;
    const id = tab.params.taskId;
    const alreadyOpen = tabs.has(id);
    if (source === 'explicit' && snapshot.activeTaskId !== id) {
      navigationAbort.abort(); navigationTargetSession = null; host.preservePanel(false);
    }
    focus++;
    if (alreadyOpen && source !== 'explicit') return;
    recoveryAbort.abort();
    if (!origins.has(id)) origins.set(id, origins.get(snapshot.activeTaskId ?? '') ?? { handle: host.captureOrigin(), sessionId: snapshot.conversation?.sessionId ?? host.getConversation()?.sessionId ?? null });
    tabs.set(id, tab);
    update({ activeTaskId: id, recovery: { status: 'opened' },
      associatedTaskIds: [...new Set([...snapshot.associatedTaskIds, id])],
      navByTask: { ...snapshot.navByTask, [id]: snapshot.navByTask[id] ?? emptyNav() } });
    if (present) host.present({ ...tab, params: { ...tab.params, userId: host.getViewer()?.userId } }, source);
  };
  const loadRecords = async (generation: number) => {
    const owner = snapshot.conversation?.sessionId.match(/:user:([^:]+)$/)?.[1] || viewerId;
    if (!owner) return [];
    try {
      const records = await client.list(owner, recoveryAbort.signal);
      if (!disposed && generation === epoch && !recoveryAbort.signal.aborted) update({ records });
      return records;
    } catch (error) {
      if (recoveryAbort.signal.aborted) return [];
      throw error;
    }
  };
  const scan = async () => {
    if (scanning || disposed || !snapshot.conversation || snapshot.activeTaskId || !viewerId) return;
    const generation = epoch; const revision = focus; const conversation = snapshot.conversation;
    const signal = recoveryAbort.signal; recoveryPhase = 'history'; scanning = true; update({ recovery: { status: 'searching' } });
    try {
      for (let count = 0; count < 20 && !historyDone; count++) {
        const page = await host.history.readPage(conversation, cursor, signal);
        if (!valid(generation, revision) || signal.aborted) return;
        pagesRead++;
        const found = [...page.messages]
          .sort((a, b) => a.timestamp - b.timestamp)
          .flatMap(message => parseTaskPanelDeclarations(message.content));
        if (found.length) {
          found.forEach((tab) => openTask(tab, 'recovery'));
          return;
        }
        if (page.nextCursor === null) { historyDone = true; break; }
        if (page.nextCursor === cursor || cursors.has(page.nextCursor)) throw new Error('历史分页游标未推进，请重试。');
        cursors.add(page.nextCursor); cursor = page.nextCursor;
      }
      if (!valid(generation, revision) || signal.aborted) return;
      if (!historyDone && cursor !== null) { update({ recovery: { status: 'incomplete', pagesRead, resumeCursor: cursor } }); return; }
      const records = await loadRecords(generation);
      if (!valid(generation, revision) || signal.aborted) return;
      const latest = records.filter(r => mainSessionId(r) === conversation.sessionId)
        .sort((a, b) => Date.parse((b.gmt_modified || b.gmt_create || '').replace(' ', 'T')) - Date.parse((a.gmt_modified || a.gmt_create || '').replace(' ', 'T')))[0];
      if (latest) openTask(declaration(latest.task_id, recordTitle(latest)), 'recovery');
      else update({ recovery: { status: 'not-found' } });
    } catch (error) {
      if (valid(generation, revision) && !signal.aborted) update({ recovery: { status: 'error', reason: error instanceof Error ? error.message : '恢复失败' } });
    } finally { if (generation === epoch) scanning = false; }
  };
  const enterConversation = async (conversation: ConversationRef, presentedTask?: TaskPanelDeclaration | null, options: { recover?: boolean } = {}) => {
    if (disposed) return;
    const sameConversation = snapshot.conversation?.sessionId === conversation.sessionId && viewerId === host.getViewer()?.userId;
    if (sameConversation && (options.recover === false || recoveryStarted)) return;
    const taskHandoff = navigationTargetSession === conversation.sessionId && !navigationAbort.signal.aborted;
    if (taskHandoff) {
      navigationTargetSession = null; recoveryStarted = true;
      update({ conversation }); return;
    }
    if (!sameConversation) {
      navigationAbort.abort(); navigationTargetSession = null; host.preservePanel(false);
      epoch++; recoveryAbort.abort(); recoveryAbort = new AbortController();
      cursor = null; pagesRead = 0; historyDone = false; scanning = false; cursors.clear(); recoveryStarted = false; recoveryPhase = 'association';
      const nextViewer = host.getViewer()?.userId ?? null;
      if (viewerId !== nextViewer) { origins.clear(); tabs.clear(); update({ navByTask: {} }); }
      viewerId = nextViewer;
      update({ conversation, activeTaskId: null, associatedTaskIds: [], records: [], recovery: { status: 'idle' } });
    }
    if (!viewerId) return;
    // Scoped SDK presentation is adopted before any lookup, without opening a duplicate tab.
    if (presentedTask && !snapshot.activeTaskId) {
      openTask(presentedTask, 'message', false); recoveryAbort = new AbortController();
    }
    // Route/SDK handoff is independent of when visible chat history becomes ready.
    if (options.recover === false) return;
    recoveryStarted = true; recoveryPhase = 'association';
    if (!snapshot.activeTaskId) update({ recovery: { status: 'searching' } });
    const generation = epoch; const revision = focus;
    try {
      const ids = await client.bySession(conversation.sessionId, recoveryAbort.signal);
      if (!valid(generation, revision)) return;
      update({ associatedTaskIds: [...new Set([...ids, ...(snapshot.activeTaskId ? [snapshot.activeTaskId] : [])])] });
      if (ids.length) {
        let records: TaskListRecord[] = [];
        try { records = await loadRecords(generation); } catch { /* task id is already authoritative; metadata is supplemental */ }
        if (!valid(generation, revision)) return;
        if (snapshot.activeTaskId) return;
        const record = records.find(r => r.task_id === ids[0]);
        openTask(declaration(ids[0], record ? recordTitle(record) : '任务'), 'recovery'); return;
      }
    } catch (error) {
      const status = (error as { status?: number })?.status;
      if (status === 401 || status === 403) {
        if (valid(generation, revision)) update({ recovery: { status: 'error', reason: '无权读取该会话的任务，或登录状态已失效。' } });
        return;
      }
      // Missing legacy index is recoverable through authenticated history.
    }
    if (valid(generation, revision)) await scan();
  };
  const navigate = async (taskId: string, target: ConversationTarget | null, restore = false) => {
    if (disposed || (!target && !restore)) return;
    navigationAbort.abort(); const ctrl = new AbortController(); navigationAbort = ctrl;
    const generation = epoch;
    navigationTargetSession = restore ? origins.get(taskId)?.sessionId ?? null : target?.sessionId ?? null;
    update({ navigationError: null }); host.preservePanel(true);
    let ready = false;
    try {
      const outcome = restore ? await host.restoreOrigin(origins.get(taskId)?.handle, ctrl.signal) : await host.navigate(target!, ctrl.signal);
      ready = outcome.status === 'ready';
      if (!ctrl.signal.aborted && generation === epoch && outcome.status !== 'ready') update({ navigationError: outcome.reason });
    } catch (error) {
      if (!ctrl.signal.aborted && generation === epoch) update({ navigationError: error instanceof Error ? error.message : '会话跳转失败' });
    } finally { if (!disposed && navigationAbort === ctrl) {
      if (!ready || snapshot.conversation?.sessionId === navigationTargetSession) navigationTargetSession = null;
      host.preservePanel(false);
    } }
  };
  const cancelNavigation = () => { if (disposed) return; navigationAbort.abort(); navigationTargetSession = null; host.preservePanel(false); };
  // Browsing/navigation is local UI state, not a Bot instruction. Some SDK hosts
  // immediately send interaction descriptions, so do not emit interactions here.
  const changeNav = (taskId: string, fn: (nav: TaskNavRecord) => TaskNavRecord) => { if (disposed) return; update({
    navByTask: { ...snapshot.navByTask, [taskId]: fn(snapshot.navByTask[taskId] ?? emptyNav()) },
  }); };
  const controller = {
    host, getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { if (disposed) return () => {}; listeners.add(listener); return () => { listeners.delete(listener); }; },
    enterConversation, openTask, cancelNavigation,
    // The SDK has already mounted this declaration; adopt focus without opening a second tab.
    adoptPresentedTask(tab: TaskPanelDeclaration) {
      if (snapshot.activeTaskId !== tab.params.taskId) openTask(tab, 'explicit', false);
    },
    closeTask(taskId?: string) {
      if (disposed) return;
      const id = taskId ?? snapshot.activeTaskId;
      if (id) {
        tabs.delete(id);
        origins.delete(id);
      }
      if (id && snapshot.activeTaskId !== id) return;
      focus++; recoveryAbort.abort(); navigationAbort.abort(); navigationTargetSession = null; host.preservePanel(false);
      const associatedTaskIds = snapshot.associatedTaskIds.filter((value) => value !== id);
      const navByTask = { ...snapshot.navByTask };
      if (id) delete navByTask[id];
      update({ activeTaskId: null, associatedTaskIds, navByTask, recovery: { status: 'idle' } });
    },
    continueRecovery: async () => {
      if (disposed) return;
      if (snapshot.recovery.status === 'error' && recoveryPhase === 'association' && snapshot.conversation) {
        recoveryStarted = false;
        await enterConversation(snapshot.conversation); return;
      }
      if (recoveryAbort.signal.aborted) recoveryAbort = new AbortController();
      await scan();
    },
    ingestMessages(messages: readonly HistoryMessage[]) {
      const tabsInMessages = [...messages]
        .sort((a, b) => a.timestamp - b.timestamp)
        .flatMap((message) => parseTaskPanelDeclarations(message.content));
      tabsInMessages.forEach((tab) => openTask(tab, 'message'));
    },
    nav(taskId: string) {
      const record = snapshot.navByTask[taskId] ?? emptyNav();
      return { ...record,
        selectDefaultNode: (id: string) => changeNav(taskId, r => ({ ...r, selectedNodeId: id })),
        selectRootNode: (id: string) => { changeNav(taskId, r => ({ ...r, selectedNodeId: id })); void navigate(taskId, null, origins.has(taskId)); },
        selectNode: (id: string, target: ConversationTarget | null) => { changeNav(taskId, r => ({ ...r, selectedNodeId: id })); void navigate(taskId, target); },
        drillInto: (id: string, target: ConversationTarget | null) => { changeNav(taskId, r => ({ ...r, drillStack: r.drillStack.includes(id) || r.drillStack.length >= 2 ? r.drillStack : [...r.drillStack, id] })); void navigate(taskId, target); },
        backToOverview: () => { changeNav(taskId, r => ({ ...r, drillStack: [] })); },
      };
    },
    leaveConversation() {
      if (disposed) return;
      recoveryStarted = false; epoch++; focus++; recoveryAbort.abort(); navigationAbort.abort(); navigationTargetSession = null; host.preservePanel(false);
      update({ conversation: null, activeTaskId: null, associatedTaskIds: [], records: [], recovery: { status: 'idle' } });
    },
    dispose() { if (disposed) return; controller.leaveConversation(); disposed = true; listeners.clear(); tabs.clear(); origins.clear(); },
  };
  return controller;
}
export type TaskPanelController = ReturnType<typeof createTaskPanelController>;
