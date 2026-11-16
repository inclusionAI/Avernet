import { getCapabilities } from '@/capabilities';
import { scrollToMessageTimestampWindow } from '@/services/tasks/panel/messageWindowLocate';
import { resolveCurrentTaskConversationOrigin, resolveTaskNavigationPathname, isTaskNavigationAtCurrentRoute, isCurrentTaskConversationOrigin } from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { ConversationTarget, NavigationOutcome } from '@avernet-assets/task-panel';
export const captureTaskPanelOrigin = resolveCurrentTaskConversationOrigin;
/** Delivery-only wait: cancellation also clears timers/listeners, including during DOM hydration. */
function waitForHost(milliseconds: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    let timer: number;
    const cancel = () => { window.clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(signal.reason); };
    timer = window.setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, milliseconds);
    signal.addEventListener('abort', cancel, { once: true });
  });
}
export async function navigateTaskPanelConversation(target: ConversationTarget & { routeUrl?: string }, signal: AbortSignal): Promise<NavigationOutcome> {
  signal.throwIfAborted();
  const taskId = useTaskSidePanelStore.getState().activeTaskId ?? '';
  const store = useTaskConversationNavigationStore.getState();
  const id = store.beginNavigation(taskId, resolveTaskNavigationPathname(target), target.sessionId);
  store.markRouteCommitted(id);
  const sameConversation = resolveCurrentTaskConversationOrigin()?.sessionId === target.sessionId;
  try {
    const result = getCapabilities().navigateToConversation(target);
    if (result.status !== 'available') { store.completeNavigation(id); return { status: 'unavailable', reason: result.reason ?? '会话导航不可用。' }; }
    for (let attempt = 0; attempt < 100; attempt++) {
      signal.throwIfAborted();
      const state = useTaskConversationNavigationStore.getState().navigation;
      if (state?.id === id && isTaskNavigationAtCurrentRoute(state) && state.routeHydrated) {
        if ((target.startTime !== null && target.startTime !== undefined) || (target.endTime !== null && target.endTime !== undefined)) {
          // Visible messages may mount after route hydration. Never keep scrolling after a newer lease/abort.
          for (let probe = 0; probe < 10; probe++) {
            signal.throwIfAborted();
            const current = useTaskConversationNavigationStore.getState().navigation;
            if (current?.id !== id || !isTaskNavigationAtCurrentRoute(current)) return { status: 'cancelled', reason: '会话导航已更新。' };
            if (scrollToMessageTimestampWindow(document, target.startTime ?? null, target.endTime ?? null)) break;
            if (probe < 9) await waitForHost(500, signal);
          }
        }
        return { status: 'ready' };
      }
      // Same-conversation no-op has no route hydration effect to wait for.
      if (sameConversation && attempt === 0) store.markRouteHydrated(id);
      await waitForHost(100, signal);
    }
    store.completeNavigation(id); return { status: 'unavailable', reason: '会话加载超时，请重试。' };
  } catch (error) { store.completeNavigation(id); throw error; }
}


/** The host interprets its opaque origin; semantic same-session restore must not rewrite normalized URLs. */
export async function restoreTaskPanelOrigin(origin: unknown, signal: AbortSignal): Promise<NavigationOutcome> {
  signal.throwIfAborted();
  if (!origin || typeof origin !== 'object' || !('sessionId' in origin)) return { status: 'unavailable', reason: '原会话尚未就绪。' };
  const target = origin as ReturnType<typeof resolveCurrentTaskConversationOrigin> & ConversationTarget;
  if (isCurrentTaskConversationOrigin(target)) return { status: 'ready' };
  return navigateTaskPanelConversation(target, signal);
}
