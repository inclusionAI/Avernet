import { create } from 'zustand';

/**
 * 协作广场（发现菜单）模块级工作身份 store。
 *
 * 背景：全局工作身份（左上角选择器 / workspaceStore.activeIdentityId）将逐步废弃，
 * 身份挂载迁移至各菜单模块下（docs/specs/2026-09-23-discovery-tab-identity/spec.md）。
 *
 * 语义：
 * - selectedIdentityId = null 表示登录用户身份（默认，无持久化）；
 * - 非 null 表示所选 Bot 身份 id，写入 localStorage，刷新后恢复；
 * - 恢复后的失效校验（id 不在身份列表中 → 回退 null）由 useSquareIdentity 负责。
 *
 * 不与全局 workspaceStore.activeIdentityId 交互：切换本模块身份不回写全局身份，
 * 也不影响工作区会话记忆。
 */
const STORAGE_KEY = 'teamclaw:square:identityId';

function readPersistedIdentityId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // 隐私模式等读取失败：回退默认（登录用户身份），不阻断页面。
    return null;
  }
}

function persistIdentityId(id: string | null): void {
  if (typeof window === 'undefined') return;
  try {
    if (id === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // 写入失败仅影响下次刷新恢复，内存态照常生效。
  }
}

export interface SquareIdentityState {
  /** null = 登录用户身份（默认）；非 null = 所选身份 id（Bot 项）。 */
  selectedIdentityId: string | null;
  selectIdentity: (id: string | null) => void;
  reset: () => void;
}

export const useSquareIdentityStore = create<SquareIdentityState>((set) => ({
  selectedIdentityId: readPersistedIdentityId(),
  selectIdentity: (id) => {
    persistIdentityId(id);
    set({ selectedIdentityId: id });
  },
  reset: () => {
    persistIdentityId(null);
    set({ selectedIdentityId: null });
  },
}));
