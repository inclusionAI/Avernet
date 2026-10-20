import { scrollToMessageTimestampWindow } from '@/assets/TaskPanel/messageWindowLocate';
import { getCapabilities } from '@/capabilities';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useEffect } from 'react';

/**
 * 时间窗 DOM 探测延迟:会话路由跳转/消息列表首帧渲染完成后再探。
 * 早于消息挂载会稳定 miss,故意取偏保守值;探测失败仅降级为"已跳会话"。
 */
const DOM_PROBE_DELAY_MS = 900;

/**
 * 任务副屏 V2 会话联动观察者(P4,spec §3.2):
 * 消费 taskSidePanelStore.pendingNavigation → capability 跳会话 → 时间窗消息定位 best-effort。
 * - 消费即清(无论跳转成败,pending 不堆积);
 * - capability 非 available / sessionId 异常:log 后放弃,不跳错页(Review Focus #2);
 * - 时间窗未在 DOM 命中:console.info 降级,不重试不抛错(Ruling 4)。
 * 挂载于 app rootContainer(全局无 UI 观察者模式,同 ErrorNotifyObserver)。
 */
export function useTaskSessionNavigationObserver(): void {
  const target = useTaskSidePanelStore((s) => s.pendingNavigation);

  useEffect(() => {
    if (!target) return;
    // 先消费再导航:消费作幂等保护,导航副作用只发生一次
    useTaskSidePanelStore.getState().consumePendingNavigation();

    const result = getCapabilities().navigateToConversation({
      sessionId: target.sessionId,
      kind: target.kind,
      groupId: target.groupId ?? null,
      botId: target.botId ?? null,
    });
    if (result.status !== 'available') {
      console.info('[task-panel-v2] 会话跳转不可用,已放弃:', result.reason);
      return;
    }

    if (target.startTime == null && target.endTime == null) return;
    // 引出即脱离(fire-and-forget):消费 pendingNavigation 会让 target 变 null 触发 effect 重跑,
    // 带清理函数的 probe 会在导航刚发生时被自取消;一次性 setTimeout 只读 DOM,无需清理。
    window.setTimeout(() => {
      const hit = scrollToMessageTimestampWindow(document, target.startTime ?? null, target.endTime ?? null);
      if (!hit) {
        console.info('[task-panel-v2] 时间窗消息未在会话 DOM 命中,已降级为仅跳转会话');
      }
    }, DOM_PROBE_DELAY_MS);
  }, [target]);
}