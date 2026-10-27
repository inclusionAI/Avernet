import { scrollToMessageTimestampWindow } from '@/assets/TaskPanel/messageWindowLocate';
import { getCapabilities } from '@/capabilities';
import {
  getCurrentRelativeUrl,
  isTaskNavigationAtCurrentRoute,
  resolveCurrentTaskConversationOrigin,
  resolveTaskNavigationPathname,
} from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useEffect } from 'react';

/** 路由跳转/消息列表首帧完成后再探测节点执行时间窗。 */
const DOM_PROBE_DELAY_MS = 900;

/**
 * 消费任务节点导航：记录原宿主 → 建立 TaskPanel 保活信号 → 走真实会话路由 → 定位消息时间窗。
 * 路由 hydration 负责同步 URL、左侧 Bot/群与 Session 选中态；保活信号只豁免本次任务导航的
 * closePanelForce，普通目录切换仍按原行为清空副屏。
 */
export function useTaskSessionNavigationObserver(): void {
  const target = useTaskSidePanelStore((s) => s.pendingNavigation);

  useEffect(() => {
    if (!target) return;
    const taskId = target.taskId ?? '';
    if (taskId && !target.routeUrl) {
      const origin = resolveCurrentTaskConversationOrigin();
      if (origin) useTaskConversationNavigationStore.getState().rememberOrigin(taskId, origin);
    }
    const navigationId = taskId
      ? useTaskConversationNavigationStore
          .getState()
          .beginNavigation(taskId, resolveTaskNavigationPathname(target), target.sessionId)
      : null;
    if (navigationId !== null) {
      // history.push may synchronously tear down the source route. Authorize the handoff first so every
      // closePanelForce/slot cleanup in that stack sees an active preservation lease.
      useTaskConversationNavigationStore.getState().markRouteCommitted(navigationId);
    }
    const routeUrlBeforeNavigation = getCurrentRelativeUrl();

    // 先消费再导航：pending 只触发一次；导航失败也不会重复撞 capability。
    useTaskSidePanelStore.getState().consumePendingNavigation();
    const result = getCapabilities().navigateToConversation({
      sessionId: target.sessionId,
      routeUrl: target.routeUrl,
      kind: target.kind,
      groupId: target.groupId ?? null,
      botId: target.botId ?? null,
    });
    if (result.status !== 'available') {
      if (navigationId !== null) useTaskConversationNavigationStore.getState().completeNavigation(navigationId);
      console.info('[task-panel-v2] 会话跳转不可用,已放弃:', result.reason);
      return;
    }

    if (navigationId !== null && routeUrlBeforeNavigation === getCurrentRelativeUrl()) {
      // Capability intentionally no-ops when the requested conversation is already current. No route
      // lifecycle will fire in that case, so finish hydration here and avoid leaving an endless lease.
      const navigation = useTaskConversationNavigationStore.getState().navigation;
      if (navigation?.id === navigationId && isTaskNavigationAtCurrentRoute(navigation)) {
        useTaskConversationNavigationStore.getState().markRouteHydrated(navigationId);
      }
    }
    if ((target.startTime ?? null) === null && (target.endTime ?? null) === null) return;
    window.setTimeout(() => {
      const hit = scrollToMessageTimestampWindow(document, target.startTime ?? null, target.endTime ?? null);
      if (!hit) console.info('[task-panel-v2] 时间窗消息未在会话 DOM 命中,已降级为仅跳转会话');
    }, DOM_PROBE_DELAY_MS);
  }, [target]);
}
