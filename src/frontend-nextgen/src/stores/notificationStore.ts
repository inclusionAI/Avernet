import type { NotificationSummary } from '@/domain/admin/models';
import { create } from 'zustand';

export interface NotificationState {
  unreadCount: number;
  recent: NotificationSummary[];
  loadingUnread: boolean;
  loadingRecent: boolean;
  lastReadFetchedAt?: number;
  setUnreadCount: (count: number) => void;
  setRecent: (recent: NotificationSummary[]) => void;
  setLoadingUnread: (loading: boolean) => void;
  setLoadingRecent: (loading: boolean) => void;
  reset: () => void;
}

// notification Store 只保存同步状态，不做 async 或接口调用；轮询/拉取在 useNotifications 编排。
export const useNotificationStore = create<NotificationState>((set) => ({
  unreadCount: 0,
  recent: [],
  loadingUnread: false,
  loadingRecent: false,
  setUnreadCount: (unreadCount) => set({ unreadCount, lastReadFetchedAt: Date.now() }),
  setRecent: (recent) => set({ recent }),
  setLoadingUnread: (loadingUnread) => set({ loadingUnread }),
  setLoadingRecent: (loadingRecent) => set({ loadingRecent }),
  reset: () => set({ unreadCount: 0, recent: [], loadingUnread: false, loadingRecent: false }),
}));
