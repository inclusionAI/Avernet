import { create } from 'zustand';

interface IdentityState {
  currentIdentityId?: string;
  setCurrentIdentityId: (identityId?: string) => void;
  reset: () => void;
}

export const useIdentityStore = create<IdentityState>((set) => ({
  currentIdentityId: undefined,
  // Store 只保存身份状态，真实身份校验由 Service / Adapter 负责。
  setCurrentIdentityId: (currentIdentityId) => set({ currentIdentityId }),
  reset: () => set({ currentIdentityId: undefined }),
}));
