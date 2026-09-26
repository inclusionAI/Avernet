import type { BotRuntime } from '@/adapters/bot-runtime';
import { create } from 'zustand';

interface EngineState {
  currentRuntime?: BotRuntime;
  setCurrentRuntime: (runtime?: BotRuntime) => void;
  reset: () => void;
}

export const useEngineStore = create<EngineState>((set) => ({
  currentRuntime: undefined,
  // Store 只接收已解析好的 BotRuntime，不在这里解析后端字段。
  setCurrentRuntime: (currentRuntime) => set({ currentRuntime }),
  reset: () => set({ currentRuntime: undefined }),
}));
