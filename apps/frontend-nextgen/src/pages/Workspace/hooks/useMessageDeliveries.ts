import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { isActiveDelivery } from '@/domain/collaboration/types';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { cancelQueuedDelivery } from '@/services/workspace/messageDeliveryService';
import {
  selectActiveDeliveries,
  selectProcessingDeliveries,
  selectQueuedDeliveries,
  useMessageDeliveryStore,
} from '@/stores/messageDeliveryStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** 终态投递在 store 中保留的毫秒数（让 UI 短暂展示最终状态）。 */
const TERMINAL_RETENTION_MS = 3_000;

/**
 * 消息投递状态 Hook——订阅 WS delivery 事件，写入 store，提供取消能力。
 *
 * 数据流：
 *   WS message.delivery.updated → provider.subscribeToDeliveryUpdates → upsertDelivery → store
 *   用户点击取消 → cancelDeliveryApi → WS 确认状态变更 → store 更新 → UI 刷新
 */
export function useMessageDeliveries(provider: GroupChatProvider | null, sessionId: string | null) {
  const [cancellingDeliveryIds, setCancellingDeliveryIds] = useState<Set<string>>(() => new Set());
  const cancellingRef = useRef(new Set<string>());
  const terminalTimersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  // 订阅 WS delivery 事件
  useEffect(() => {
    if (!provider || !sessionId) return;
    // 防御：mock provider 或旧版 SDK 可能未实现 subscribeToDeliveryUpdates
    if (typeof provider.subscribeToDeliveryUpdates !== 'function') return;
    const unsubscribe = provider.subscribeToDeliveryUpdates((delivery: DeliveryStatusView) => {
      const store = useMessageDeliveryStore.getState();
      store.upsertDelivery(sessionId, delivery);

      // 终态投递延迟移除
      if (!isActiveDelivery(delivery.status)) {
        const existingTimer = terminalTimersRef.current.get(delivery.delivery_id);
        if (existingTimer) clearTimeout(existingTimer);
        const timer = setTimeout(() => {
          useMessageDeliveryStore.getState().removeDelivery(sessionId, delivery.delivery_id);
          terminalTimersRef.current.delete(delivery.delivery_id);
        }, TERMINAL_RETENTION_MS);
        terminalTimersRef.current.set(delivery.delivery_id, timer);
      }
    });
    return unsubscribe;
  }, [provider, sessionId]);

  // 会话切换时清空 store 与定时器
  useEffect(() => {
    return () => {
      if (sessionId) {
        useMessageDeliveryStore.getState().clearSession(sessionId);
      }
      terminalTimersRef.current.forEach((timer) => clearTimeout(timer));
      terminalTimersRef.current.clear();
      cancellingRef.current.clear();
      setCancellingDeliveryIds(new Set());
    };
  }, [sessionId]);

  // 取消单个投递
  const cancelDelivery = useCallback(
    async (messageId: string, deliveryId: string) => {
      if (!sessionId || cancellingRef.current.has(deliveryId)) return;
      cancellingRef.current.add(deliveryId);
      setCancellingDeliveryIds(new Set(cancellingRef.current));
      try {
        // 取消经 messageDeliveryService（分层：Hook 不直连 API Controller）；
        // 接口裸返回结果数组，HTTP 失败抛错，单项失败经 firstError 返回。
        const outcome = await cancelQueuedDelivery(messageId, deliveryId, sessionId);
        if (outcome.firstError) {
          toast.error(outcome.firstError);
        }
      } catch {
        toast.error('取消失败，请重试');
      } finally {
        cancellingRef.current.delete(deliveryId);
        setCancellingDeliveryIds(new Set(cancellingRef.current));
      }
    },
    [sessionId],
  );

  // 从 store 读取当前会话的投递状态
  const storeState = useMessageDeliveryStore();
  const activeDeliveries = sessionId ? selectActiveDeliveries(storeState, sessionId) : [];
  const queuedDeliveries = sessionId ? selectQueuedDeliveries(storeState, sessionId) : [];
  const processingDeliveries = sessionId ? selectProcessingDeliveries(storeState, sessionId) : [];

  return {
    activeDeliveries,
    queuedDeliveries,
    processingDeliveries,
    cancelDelivery,
    cancellingDeliveryIds,
  };
}
