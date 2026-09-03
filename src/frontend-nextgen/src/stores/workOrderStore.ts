import type { ServiceError, WorkOrder, WorkOrderCategory, WorkOrderView } from '@/domain/admin/models';
import { create } from 'zustand';

export interface WorkOrderState {
  view: WorkOrderView;
  category: WorkOrderCategory;
  pageNo: number;
  pageSize: number;
  items: WorkOrder[];
  total: number;
  loading: boolean;
  error: ServiceError | null;
  setView: (view: WorkOrderView) => void;
  setCategory: (category: WorkOrderCategory) => void;
  setPageNo: (pageNo: number) => void;
  setPageSize: (pageSize: number) => void;
  setList: (items: WorkOrder[], total: number) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: ServiceError | null) => void;
  removeItem: (workOrderId: number | string) => void;
  /** 就地翻已读态（审批类工单读过仍留在列表，不能像通知那样 removeItem）。 */
  markItemRead: (workOrderId: number | string) => void;
  reset: () => void;
}

// workOrder Store 只保存同步页面状态，不直接发请求；编排由 useWorkOrders 负责。
export const useWorkOrderStore = create<WorkOrderState>((set) => ({
  view: 'pending_mine',
  category: 'ALL',
  pageNo: 1,
  pageSize: 10,
  items: [],
  total: 0,
  loading: false,
  error: null,
  setView: (view) => set({ view, pageNo: 1 }),
  setCategory: (category) => set({ category, pageNo: 1 }),
  setPageNo: (pageNo) => set({ pageNo }),
  setPageSize: (pageSize) => set({ pageSize, pageNo: 1 }),
  setList: (items, total) => set({ items, total }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  removeItem: (workOrderId) =>
    set((s) => ({
      items: s.items.filter((i) => String(i.workOrderId) !== String(workOrderId)),
      total: Math.max(0, s.total - 1),
    })),
  markItemRead: (workOrderId) =>
    set((s) => ({
      items: s.items.map((i) => (String(i.workOrderId) === String(workOrderId) ? { ...i, isRead: true } : i)),
    })),
  reset: () =>
    set({
      view: 'pending_mine',
      category: 'ALL',
      pageNo: 1,
      pageSize: 10,
      items: [],
      total: 0,
      loading: false,
      error: null,
    }),
}));
