// useNotifications：铃铛未读数 + tooltip 最近 3 条 + 全部已读。
// 智能轮询：挂载首拉 + 30s 定时；document.hidden 暂停，可见恢复并补一次；操作后局部刷新；卸载清 timer。

import { notificationService } from '@/services/admin/notificationService';
import { useNotificationStore } from '@/stores/notificationStore';
import { extractFriendlyErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

export const NOTIFICATION_POLL_INTERVAL_MS = 30000;

export function useNotifications() {
  const {
    unreadCount,
    recent,
    loadingUnread,
    loadingRecent,
    setUnreadCount,
    setRecent,
    setLoadingUnread,
    setLoadingRecent,
  } = useNotificationStore();
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadedRef = useRef(false);

  const refreshUnread = useCallback(
    async (silent = false) => {
      if (!silent) setLoadingUnread(true);
      const r = await notificationService.fetchUnreadCount();
      setLoadingUnread(false);
      if (r.error) {
        if (!silent) toast.error(r.error.message);
        return;
      }
      setUnreadCount(r.data ?? 0);
    },
    [setUnreadCount, setLoadingUnread],
  );

  const loadRecent = useCallback(async () => {
    setLoadingRecent(true);
    const r = await notificationService.fetchRecentNotifications(3);
    setLoadingRecent(false);
    if (r.error) {
      toast.error(extractFriendlyErrorMessage(r.error));
      return;
    }
    setRecent(r.data ?? []);
  }, [setRecent, setLoadingRecent]);

  const markAllRead = useCallback(async () => {
    const r = await notificationService.markAllRead();
    if (r.error) {
      toast.error(r.error.message);
      return;
    }
    setUnreadCount(0);
    toast.success('已全部标记为已读');
    // 下拉里显示的最近 3 条同样标记为已读视觉态（数据层下次刷新拉取为准），立即刷新一次未读。
    void refreshUnread(true);
  }, [refreshUnread, setUnreadCount]);

  // 智能轮询：挂载首拉 + 定时 + 可见性优化
  useEffect(() => {
    void refreshUnread();
    loadedRef.current = true;
    const tick = () => {
      if (document.hidden) return;
      void refreshUnread(true);
    };
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(tick, NOTIFICATION_POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (document.hidden) {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      } else {
        void refreshUnread(true);
        if (!timerRef.current) timerRef.current = setInterval(tick, NOTIFICATION_POLL_INTERVAL_MS);
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [refreshUnread]);

  return {
    unreadCount,
    recent,
    loadingUnread,
    loadingRecent,
    refreshUnread,
    loadRecent,
    markAllRead,
  };
}
