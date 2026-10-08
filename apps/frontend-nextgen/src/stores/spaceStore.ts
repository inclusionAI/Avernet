import { create } from 'zustand';

/**
 * 全局「当前空间」公共状态。
 *
 * 空间切换属于整体产品功能（由全局布局/管理后台负责实现），这里只承载
 * 「当前选中的是哪个空间」这一公共变量，供各业务模块（如能力工坊按空间隔离
 * Skill / MCP）读取，并把空间上下文作为接口入参。
 *
 * 默认：个人空间。全局空间功能上线后，由它负责更新 currentSpace；
 * 本模块不承载空间列表、切换 UI 或身份校验逻辑。
 */
export type GlobalSpaceKind = 'personal' | 'team';

export interface GlobalSpace {
  id: string;
  kind: GlobalSpaceKind;
  name: string;
}

interface SpaceState {
  currentSpace: GlobalSpace;
  setCurrentSpace: (space: GlobalSpace) => void;
  reset: () => void;
}

const DEFAULT_SPACE: GlobalSpace = { id: 'personal', kind: 'personal', name: '个人空间' };

export const useSpaceStore = create<SpaceState>((set) => ({
  currentSpace: DEFAULT_SPACE,
  setCurrentSpace: (currentSpace) => set({ currentSpace }),
  reset: () => set({ currentSpace: DEFAULT_SPACE }),
}));
