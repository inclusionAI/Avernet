import type { ServiceError, Space, SpaceMember, SpaceType } from '@/domain/admin/models';
import { create } from 'zustand';

export interface AdminState {
  // 兼容旧字段
  keyword: string;
  setKeyword: (keyword: string) => void;
  // 空间列表
  spaceType: SpaceType | 'ALL';
  pageNo: number;
  pageSize: number;
  items: Space[];
  total: number;
  loading: boolean;
  error: ServiceError | null;
  // 当前空间 + 成员
  currentSpace: Space | null;
  members: SpaceMember[];
  membersLoading: boolean;
  setSpaceType: (spaceType: SpaceType | 'ALL') => void;
  setPageNo: (pageNo: number) => void;
  setPageSize: (pageSize: number) => void;
  setList: (items: Space[], total: number) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: ServiceError | null) => void;
  setCurrentSpace: (space: Space | null) => void;
  setMembers: (members: SpaceMember[]) => void;
  setMembersLoading: (loading: boolean) => void;
  reset: () => void;
}

// admin Store 只保存同步页面状态，不做 async 或接口调用；编排由 useAdmin 负责。
export const useAdminStore = create<AdminState>((set) => ({
  keyword: '',
  setKeyword: (keyword) => set({ keyword, pageNo: 1 }),
  spaceType: 'ALL',
  pageNo: 1,
  pageSize: 20,
  items: [],
  total: 0,
  loading: false,
  error: null,
  currentSpace: null,
  members: [],
  membersLoading: false,
  setSpaceType: (spaceType) => set({ spaceType, pageNo: 1 }),
  setPageNo: (pageNo) => set({ pageNo }),
  setPageSize: (pageSize) => set({ pageSize, pageNo: 1 }),
  setList: (items, total) => set({ items, total }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setCurrentSpace: (currentSpace) => set({ currentSpace, members: [] }),
  setMembers: (members) => set({ members }),
  setMembersLoading: (membersLoading) => set({ membersLoading }),
  reset: () =>
    set({
      keyword: '',
      spaceType: 'ALL',
      pageNo: 1,
      pageSize: 20,
      items: [],
      total: 0,
      loading: false,
      error: null,
      currentSpace: null,
      members: [],
      membersLoading: false,
    }),
}));
