import type { DeliveryStatusView } from '@/domain/collaboration/types';
import { isActiveDelivery, isProcessingDelivery, isQueuedDelivery } from '@/domain/collaboration/types';
import { create } from 'zustand';

/**
 * 消息投递状态 Store——按会话隔离的 delivery 状态管理。
 *
 * 外层 key 为 sessionId，内层 key 为 deliveryId。
 * 高频更新（每次 WS 事件），独立于 workspaceStore 避免不必要的重渲染。
 */

interface MessageDeliveryState {
  /** 按会话分组的投递状态映射：sessionId → deliveryId → DeliveryStatusView */
  deliveriesBySessionId: Record<string, Record<string, DeliveryStatusView>>;
  /** 插入或更新投递状态（state_version 大的覆盖小的）。 */
  upsertDelivery: (sessionId: string, delivery: DeliveryStatusView) => void;
  /** 移除单个投递。 */
  removeDelivery: (sessionId: string, deliveryId: string) => void;
  /** 清空指定会话的所有投递。 */
  clearSession: (sessionId: string) => void;
  /** 清空全部投递。 */
  clearAll: () => void;
}

export const useMessageDeliveryStore = create<MessageDeliveryState>((set) => ({
  deliveriesBySessionId: {},

  upsertDelivery: (sessionId, delivery) =>
    set((state) => {
      const sessionDeliveries = state.deliveriesBySessionId[sessionId] ?? {};
      const existing = sessionDeliveries[delivery.delivery_id];
      // state_version 小的不覆盖大的（last-write-wins）
      if (existing && existing.state_version >= delivery.state_version) return state;
      return {
        deliveriesBySessionId: {
          ...state.deliveriesBySessionId,
          [sessionId]: { ...sessionDeliveries, [delivery.delivery_id]: delivery },
        },
      };
    }),

  removeDelivery: (sessionId, deliveryId) =>
    set((state) => {
      const sessionDeliveries = state.deliveriesBySessionId[sessionId];
      if (!sessionDeliveries || !(deliveryId in sessionDeliveries)) return state;
      const rest = { ...sessionDeliveries };
      delete rest[deliveryId];
      return {
        deliveriesBySessionId: {
          ...state.deliveriesBySessionId,
          [sessionId]: rest,
        },
      };
    }),

  clearSession: (sessionId) =>
    set((state) => {
      if (!(sessionId in state.deliveriesBySessionId)) return state;
      const rest = { ...state.deliveriesBySessionId };
      delete rest[sessionId];
      return { deliveriesBySessionId: rest };
    }),

  clearAll: () => set({ deliveriesBySessionId: {} }),
}));

// ── 选择器（纯函数，不挂在 store 上） ─────────────────────────────────────────

/** 获取指定会话的活跃（非终态）投递列表。 */
export function selectActiveDeliveries(state: MessageDeliveryState, sessionId: string): DeliveryStatusView[] {
  const sessionDeliveries = state.deliveriesBySessionId[sessionId];
  if (!sessionDeliveries) return [];
  return Object.values(sessionDeliveries).filter((d) => isActiveDelivery(d.status));
}

/** 获取指定会话的排队中投递列表。 */
export function selectQueuedDeliveries(state: MessageDeliveryState, sessionId: string): DeliveryStatusView[] {
  const sessionDeliveries = state.deliveriesBySessionId[sessionId];
  if (!sessionDeliveries) return [];
  return Object.values(sessionDeliveries).filter((d) => isQueuedDelivery(d.status));
}

/** 获取指定会话的处理中投递列表。 */
export function selectProcessingDeliveries(state: MessageDeliveryState, sessionId: string): DeliveryStatusView[] {
  const sessionDeliveries = state.deliveriesBySessionId[sessionId];
  if (!sessionDeliveries) return [];
  return Object.values(sessionDeliveries).filter((d) => isProcessingDelivery(d.status));
}

/** 获取指定会话中目标 bot 的投递列表。 */
export function selectDeliveriesByBot(
  state: MessageDeliveryState,
  sessionId: string,
  botId: string,
): DeliveryStatusView[] {
  const sessionDeliveries = state.deliveriesBySessionId[sessionId];
  if (!sessionDeliveries) return [];
  return Object.values(sessionDeliveries).filter((d) => d.target_bot_id === botId);
}
