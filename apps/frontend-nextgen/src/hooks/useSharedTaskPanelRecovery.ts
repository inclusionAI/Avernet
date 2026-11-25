import { logTaskPanelDiagnostic, taskPanelDiagnosticId } from '@/services/tasks/panel/taskPanelDiagnostics';
import { useEffect, useState, useSyncExternalStore, useRef, useCallback } from 'react';
import type { PanelHandle } from '@tc-chat/core';
import { configureTaskPanelSession, getTaskPanelController, getPresentedTaskForConversation, peekTaskPanelController, getTaskPanelRuntimeGeneration, subscribeTaskPanelRuntime, releaseTaskPanelSession } from '@/services/tasks/panel/taskPanelRuntime';
import { restoreOrderedTaskPanelTabs } from '@/services/tasks/panel/taskPanelHandleAdapter';
import type { TaskPanelDeclaration, TaskPanelController } from '@avernet-assets/task-panel';
import type { ConversationRef, HistoryReader, RecoveryState } from '@avernet-assets/task-panel';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
/** Adapter lifecycle only; source selection, paging and arbitration belong to the shared controller. */
export function useSharedTaskPanelRecovery(options: {
  conversation: ConversationRef | null; userId: string | null; history: HistoryReader | null;
  panelRef: React.RefObject<PanelHandle | null>; enabled?: boolean; historyScope?: string | null;
}) {
  const id = options.conversation?.sessionId;
  const hasSession = Boolean(id && options.userId);
  const subscribeLifecycle = useCallback((listener: () => void) => hasSession ? subscribeTaskPanelRuntime(listener) : () => {}, [hasSession]);
  const runtimeGeneration = useSyncExternalStore(subscribeLifecycle, getTaskPanelRuntimeGeneration, getTaskPanelRuntimeGeneration);
  const historyScope = useRef(options.historyScope);
  const diagnosticInputs = useRef<Record<string, unknown>>({});
  const retry = useRef<() => Promise<void>>(async () => {});
  const continueRecovery = useCallback(() => retry.current(), []);
  const [recovery, setRecovery] = useState<RecoveryState>({ status: 'idle' });
  // Session lifetime is not the history reader / historyReady lifetime.
  useEffect(() => {
    if (!id || !options.userId) return;
    const lifetimeId = taskPanelDiagnosticId('lifetime');
    const details = { lifetimeId, sessionId: id, userId: options.userId, historyScope: options.historyScope, runtimeGeneration };
    logTaskPanelDiagnostic('recovery.lifetime-setup', details);
    return () => {
      logTaskPanelDiagnostic('recovery.lifetime-cleanup', details, true);
      releaseTaskPanelSession(id, runtimeGeneration);
    };
  }, [id, options.userId, runtimeGeneration, options.historyScope]);
  useEffect(() => {
    const effectId = taskPanelDiagnosticId('effect');
    const inputs: Record<string, unknown> = { sessionId: id, userId: options.userId, historyScope: options.historyScope,
      enabled: options.enabled, history: options.history, panelRef: options.panelRef, runtimeGeneration };
    const changed = Object.keys(inputs).filter(key => inputs[key] !== diagnosticInputs.current[key]);
    diagnosticInputs.current = inputs;
    const details = { effectId, sessionId: id ?? null, userId: options.userId, historyScope: options.historyScope ?? null,
      enabled: options.enabled !== false, hasHistory: Boolean(options.history), runtimeGeneration };
    logTaskPanelDiagnostic('recovery.effect-setup', { ...details, changed });
    // A view switch is not a task navigation handoff, even within the same session.
    if (historyScope.current !== options.historyScope) {
      logTaskPanelDiagnostic('recovery.scope-change', { ...details, previousScope: historyScope.current }, true);
      const controller = peekTaskPanelController();
      if (controller && controller.getSnapshot().conversation?.sessionId === id) controller.leaveConversation();
      historyScope.current = options.historyScope;
      setRecovery({ status: 'idle' });
    }
    if (!options.conversation || !options.userId || !options.history) {
      logTaskPanelDiagnostic('recovery.skip', { ...details, reason: 'missing-session-user-or-history' }); return;
    }
    // Bind an existing task handoff even while visible history is still loading.
    if (options.enabled === false && !peekTaskPanelController()) {
      logTaskPanelDiagnostic('recovery.skip', { ...details, reason: 'history-not-ready' }); return;
    }
    let live = true; let activeController: TaskPanelController | undefined;
    const ref = options.conversation;
    const history = options.history;
    const pendingTabs = new Map<string, TaskPanelDeclaration>();
    let restoring = false;
    const bindSession = () => configureTaskPanelSession(ref, { history, present: (tab, source) => {
      if (!live) return;
      if (source === 'recovery') {
        pendingTabs.set(tab.params.taskId, tab);
        if (restoring) return;
        restoring = true;
        void (async () => {
          // Coalesce the controller's synchronous recovery batch before SDK rendering.
          await Promise.resolve();
          const isCurrent = () => live && activeController === peekTaskPanelController() &&
            activeController?.getSnapshot().conversation?.sessionId === ref.sessionId;
          while (isCurrent() && !options.panelRef.current) await new Promise(resolve => { setTimeout(resolve, 100); });
          if (!isCurrent() || !options.panelRef.current || !activeController) return;
          const snapshot = activeController.getSnapshot();
          const tabs = snapshot.orderedTaskIds.map(id => pendingTabs.get(id)).filter((value): value is TaskPanelDeclaration => Boolean(value));
          const selected = getPresentedTaskForConversation(ref);
          restoreOrderedTaskPanelTabs(options.panelRef.current, tabs, snapshot.activeTaskId ? `task-${snapshot.activeTaskId}` : null, Boolean(selected));
          pendingTabs.clear(); restoring = false;
        })();
        return;
      }
      const isCurrent = () => live && activeController === peekTaskPanelController() &&
        activeController?.getSnapshot().conversation?.sessionId === ref.sessionId &&
        activeController.getSnapshot().associatedTaskIds.includes(tab.params.taskId);
      void (async () => {
        while (isCurrent() && !options.panelRef.current) await new Promise(resolve => { setTimeout(resolve, 100); });
        if (!isCurrent() || !options.panelRef.current) return;
        const state = useTaskSidePanelStore.getState();
        if (source === 'explicit') state.markTaskPanelOpenIntent(tab.params.taskId);
        options.panelRef.current.openTab(tab);
        const activeId = activeController?.getSnapshot().activeTaskId;
        if (activeId) options.panelRef.current.focusTab(`task-${activeId}`);
        state.consumeTaskPanelOpenIntent(tab.params.taskId);
      })();
    } });
    let unsubscribe: (() => void) | undefined;
    const initialize = async () => {
      logTaskPanelDiagnostic('recovery.initialize', details);
      const controller = await getTaskPanelController();
      logTaskPanelDiagnostic('recovery.controller-ready', { ...details, live, current: peekTaskPanelController() === controller });
      if (!live || peekTaskPanelController() !== controller) return;
      activeController = controller;
      bindSession(); unsubscribe?.();
      setRecovery(controller.getSnapshot().recovery);
      unsubscribe = controller.subscribe(() => { if (live) setRecovery(controller.getSnapshot().recovery); });
      const requestedTaskId = useTaskSidePanelStore.getState().taskPanelOpenIntentTaskId;
      logTaskPanelDiagnostic('recovery.enter', { ...details, controllerSessionId: controller.getSnapshot?.()?.conversation?.sessionId }, true);
      const pendingRecovery = controller.enterConversation(ref, getPresentedTaskForConversation(ref), { recover: options.enabled !== false });
      if (requestedTaskId) controller.openTask({ id: `task-${requestedTaskId}`, type: 'taskPanel.TaskLoopView', title: '任务', closable: true, defaultWidth: 420, params: { taskId: requestedTaskId } }, 'explicit');
      await pendingRecovery;
      logTaskPanelDiagnostic('recovery.complete', { ...details, live, recoveryStatus: controller.getSnapshot?.()?.recovery?.status });
    };
    const report = (error: unknown) => { if (live) setRecovery({ status: 'error', reason: error instanceof Error ? error.message : String(error) }); };
    retry.current = async () => {
      logTaskPanelDiagnostic('recovery.retry', { ...details, live }, true);
      if (!live) return;
      try {
        if (activeController && activeController === peekTaskPanelController() && activeController.getSnapshot().conversation?.sessionId === ref.sessionId) await activeController.continueRecovery();
        else await initialize();
      } catch (error) { report(error); }
    };
    void initialize().catch(report);
    return () => { logTaskPanelDiagnostic('recovery.effect-cleanup', details, true); live = false; retry.current = async () => {}; unsubscribe?.(); };
  }, [id, options.userId, options.enabled, options.history, options.panelRef, runtimeGeneration, options.historyScope]);
  return { ...recovery, continueRecovery };
}
