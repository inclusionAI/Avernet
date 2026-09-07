// useWorkOrders：工单中心三视图 / 分类 / 分页 / 审批 / 通知已读编排。
// view/category/pageNo 变化 -> 拉列表；审批后移除并 toast；错误回填 store + toast。
// user_id 由 workOrderService 内部经 resolveUserId(activeIdentityId) 注入，hook 不再透传 currentUserId。

import type { WorkOrderCategory, WorkOrderView } from '@/domain/admin/models';
import { notificationService } from '@/services/admin/notificationService';
import { workOrderService } from '@/services/admin/workOrderService';
import { useNotificationStore } from '@/stores/notificationStore';
import { useWorkOrderStore } from '@/stores/workOrderStore';
import { shouldMuteNonAuthedToast } from '@/utils/loginToastGate';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useEffect } from 'react';
import { toast } from 'sonner';

export function useWorkOrders() {
  const {
    view,
    category,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    setView,
    setCategory,
    setPageNo,
    setPageSize,
    setList,
    setLoading,
    setError,
    removeItem,
    markItemRead,
  } = useWorkOrderStore();

  const setUnreadCount = useNotificationStore((s) => s.setUnreadCount);

  // 审批/已读后重新拉取未读计数（铃铛角标）。原实现误把 setter 当 refresh 调用（no-op）。
  const refreshUnreadCount = useCallback(async () => {
    const r = await notificationService.fetchUnreadCount();
    if (!r.error) setUnreadCount(r.data ?? 0);
  }, [setUnreadCount]);

  const fetchList = useCallback(
    async (override?: { view?: WorkOrderView; category?: WorkOrderCategory; pageNo?: number }) => {
      const v = override?.view ?? view;
      const c = override?.category ?? category;
      const p = override?.pageNo ?? pageNo;
      setLoading(true);
      setError(null);
      const r = await workOrderService.list({
        view: v,
        category: c,
        page: p,
        pageSize,
      });
      setLoading(false);
      if (r.error) {
        setError(r.error);
        // 未登录（oauth-provider + 非 authenticated）静默：自动加载失败 toast 统一由
        // ExternalLoginPromptModal 承担（见 loginToastGate）；已登录照常提示真实失败，不丢反馈。
        if (!shouldMuteNonAuthedToast()) {
          toast.error(r.error.message);
        }
        return;
      }
      setList(r.data?.items ?? [], r.data?.total ?? 0);
    },
    [view, category, pageNo, pageSize, setLoading, setError, setList],
  );

  // 关键参数变化重新请求
  useEffect(() => {
    void fetchList();
  }, [fetchList]);

  const approve = useCallback(
    async (workOrderId: number | string) => {
      const r = await workOrderService.approve(workOrderId);
      if (r.error) {
        toast.error(r.error.message);
        void fetchList(); // 竞态：可能已被他人处理，刷新以同步按钮显隐
        return;
      }
      toast.success('已同意');
      removeItem(workOrderId);
      void refreshUnreadCount();
    },
    [removeItem, refreshUnreadCount, fetchList],
  );

  const reject = useCallback(
    async (workOrderId: number | string, remark: string) => {
      const r = await workOrderService.reject(workOrderId, remark);
      if (r.error) {
        toast.error(r.error.message);
        void fetchList(); // 竞态：可能已被他人处理，刷新以同步按钮显隐
        return;
      }
      toast.success('已驳回');
      removeItem(workOrderId);
      void refreshUnreadCount();
    },
    [removeItem, refreshUnreadCount, fetchList],
  );

  const markNotificationRead = useCallback(async (notificationId: number | string) => {
    const r = await workOrderService.markNotificationRead(notificationId);
    if (r.error) {
      toast.error(extractErrorMessage(r.error));
      return false;
    }
    return true;
  }, []);

  const getDetail = useCallback(async (workOrderId: number | string) => workOrderService.getDetail(workOrderId), []);
  const getNotificationDetail = useCallback(
    async (notificationId: number | string) => workOrderService.getNotificationDetail(notificationId),
    [],
  );

  return {
    view,
    category,
    pageNo,
    pageSize,
    items,
    total,
    loading,
    error,
    setView,
    setCategory,
    setPageNo,
    setPageSize,
    fetchList,
    approve,
    reject,
    removeItem,
    markItemRead,
    markNotificationRead,
    getDetail,
    getNotificationDetail,
  };
}
