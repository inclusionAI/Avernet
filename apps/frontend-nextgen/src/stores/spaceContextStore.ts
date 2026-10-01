// 全局空间上下文 Store（Open Core）。承载当前工作空间 / 可切换列表 / 加载态。
// 治理原则对齐账号（identityStore/userStore 范式：全局 zustand store + 运行时注入），
// 但空间上下文语义独立、生命周期可频繁切换，故物理独立 store，不与账号 store 合并。
// 设计依据：docs/specs/2026-08-17-global-space-context-switcher/plan.md §1 D1/D2。
// 不在 selector 内调用 getState()；Store ≤ 150 行。
import type { Space } from '@/domain/admin/models';
import type { SpaceContextState } from '@/domain/spaceContext';
import { create } from 'zustand';

interface SpaceContextStoreState extends SpaceContextState {
  /** init() 成功完成的幂等标志；reset 清零以允许重新初始化（测试/登出场景）。 */
  initialized: boolean;
  setInitialized: (initialized: boolean) => void;
  /** 设当前空间 id；同步推算 currentSpace（在已知 spaces 中查，找不到则 undefined） */
  setCurrentSpaceId: (id: number | undefined) => void;
  /** 设可切换空间列表（仅已加入）；若当前 id 不在新列表中则回落 undefined */
  setSpaces: (spaces: Space[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error?: string) => void;
  reset: () => void;
}

const initialState: SpaceContextState & { initialized: boolean } = {
  currentSpaceId: undefined,
  currentSpace: undefined,
  spaces: [],
  loading: false,
  error: undefined,
  initialized: false,
};

function findSpace(spaces: Space[], id: number | undefined): Space | undefined {
  return id === undefined ? undefined : spaces.find((s) => s.spaceId === id);
}

export const useSpaceContextStore = create<SpaceContextStoreState>((set) => ({
  ...initialState,
  setInitialized: (initialized) => set({ initialized }),
  setCurrentSpaceId: (id) => set((state) => ({ currentSpaceId: id, currentSpace: findSpace(state.spaces, id) })),
  setSpaces: (spaces) =>
    set((state) => {
      const nextId = findSpace(spaces, state.currentSpaceId) ? state.currentSpaceId : undefined;
      // 空间 ID 不变时复用旧 currentSpace 引用，避免 refreshSpaceContext 重拉列表后
      // 产生新对象引用 → 下游 useMemo/useCallback 级联 → 误触发 skills 列表请求。
      const sameSpace = nextId === state.currentSpaceId ? state.currentSpace : undefined;
      return { spaces, currentSpaceId: nextId, currentSpace: sameSpace ?? findSpace(spaces, nextId) };
    }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  reset: () => set(initialState),
}));
