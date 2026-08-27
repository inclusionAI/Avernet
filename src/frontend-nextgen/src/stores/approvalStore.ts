import { create } from 'zustand';

interface ApprovalState {
  pendingCount: number;
  setPendingCount: (count: number) => void;
  reset: () => void;
}

export const useApprovalStore = create<ApprovalState>((set) => ({
  pendingCount: 0,
  // 审批数量只作为同步状态保存，申请规则由 Service / Policy 计算。
  setPendingCount: (pendingCount) => set({ pendingCount }),
  reset: () => set({ pendingCount: 0 }),
}));
