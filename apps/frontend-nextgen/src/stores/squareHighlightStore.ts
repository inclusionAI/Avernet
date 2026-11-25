import { create } from 'zustand';

export interface SquareHighlightState {
  /** 深链定位的 Bot 高亮目标 id；由 UI 层滚动到卡片并短暂高亮后清除。 */
  highlightBotId: string | null;
  setHighlightBotId: (id: string | null) => void;
}

export const useSquareHighlightStore = create<SquareHighlightState>((set) => ({
  highlightBotId: null,
  setHighlightBotId: (highlightBotId) => set({ highlightBotId }),
}));
