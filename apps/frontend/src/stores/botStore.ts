/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot Store - 全局 Bot 状态管理
 *
 * 直接使用后端 API 的 Bot 类型（BotController.Bot），不做前端二次映射。
 * 所有字段名、枚举值与后端保持一致：bot_id、bot_name、ACTIVE、PENDING 等。
 */

import type { Bot } from '@/services/backend-api/BotController';
import { useUserStore } from '@/stores/userStore';
import { getCurrentBotKindContext } from '@/utils/botScopeContext';
import { Tracert } from '@/utils/tracert';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

// Re-export API 类型和常量，供全局使用
export {
  APPROVAL_STATUS,
  BOT_STATUS,
  BOT_TYPE,
  ENGINE_TYPE,
  ENTITY_TYPE,
  PERMISSION_OWNER,
  PUBLIC_STATUS,
} from '@/services/backend-api/BotController';
export type {
  ApprovalStatus,
  Bot,
  BotExt,
  BotStatus,
  BotType,
  EngineType,
  EntityType,
  PermissionOwner,
  PublicApproval,
  PublicStatus,
} from '@/services/backend-api/BotController';

export type BotPollTimeoutReason = 'create' | 'restart' | 'activate';

export interface BotPollTimeout {
  botId: string;
  reason: BotPollTimeoutReason;
}

interface BotState {
  // State
  bots: Bot[]; // 仅 personal 类型的 Bot（用于左侧菜单和我的 Bot 页面）
  allBots: Bot[]; // 所有 Bot（personal + service，用于能力集、任务、协作等页面）
  activeBotId: string | null; // 存储 bot_id，null 表示未选择或无效
  isLoading: boolean;

  // UI State
  isBotManagerOpen: boolean;
  botPollTimeout: BotPollTimeout | null;
  isDeletingActiveBot: boolean; // 删除当前激活Bot时显示遮罩

  // Bot 总数（个人 + 服务）
  totalBotCount: number;

  // Bot 数量上限（来自 /api/bots/ceiling，默认 5）
  maxBotCount: number;

  // Actions
  setBots: (bots: Bot[]) => void;
  setAllBots: (bots: Bot[]) => void;
  setActiveBotId: (id: string) => void;
  addBot: (bot: Bot) => void;
  updateBot: (botId: string, updates: Partial<Bot>) => void;
  removeBot: (botId: string) => void;
  setLoading: (loading: boolean) => void;
  setBotPollTimeout: (timeout: BotPollTimeout) => void;
  clearBotPollTimeout: () => void;
  setDeletingActiveBot: (deleting: boolean) => void;
  setTotalBotCount: (count: number) => void;
  setMaxBotCount: (count: number) => void;

  // UI Actions
  openBotManager: () => void;
  closeBotManager: () => void;
  toggleBotManager: () => void;

  // Computed
  getActiveBotById: () => Bot | undefined;
  getBotById: (id: string) => Bot | undefined;
  reset: () => void;
}

const initialState = {
  bots: [] as Bot[],
  allBots: [] as Bot[],
  activeBotId: null as string | null,
  isLoading: false,
  isBotManagerOpen: false,
  botPollTimeout: null as BotPollTimeout | null,
  isDeletingActiveBot: false,
  totalBotCount: 0,
  maxBotCount: 5,
};

export const useBotStore = create<BotState>()(
  devtools(
    (set, get) => ({
      // Initial State
      ...initialState,

      // Actions
      setBots: (bots) => set({ bots }, false, 'setBots'),

      setAllBots: (bots) => set({ allBots: bots }, false, 'setAllBots'),

      setActiveBotId: (id) => {
        const oldActiveBotId = get().activeBotId;

        // 切换 Bot 时重置高敏标签显示状态
        import('./uiStore').then(({ useUIStore }) => {
          useUIStore.getState().setShowHighSensitivityTags(false);
        });
        set({ activeBotId: id }, false, 'setActiveBotId');

        // 埋点：Bot 切换访问（仅切换场景上报，首次冷启动不上报，避免与 d693664 重复）
        // oldActiveBotId 非 null 且与目标不同 → 用户从左侧菜单或页面内主动切换 Bot
        if (oldActiveBotId && oldActiveBotId !== id) {
          const ctx = getCurrentBotKindContext(id);
          const userId = useUserStore.getState().userId || '';
          Tracert.click('ca116389.da197511', {
            user_id: userId,
            bot_id: id,
            engine_type: ctx.engine_type,
            template_type: ctx.template_type,
            bot_kind: ctx.bot_kind,
            from_bot_id: oldActiveBotId,
          }); /* spm: Bot管理-切换访问 */
        }
      },

      addBot: (bot) =>
        set(
          (state) => ({
            bots: [bot, ...state.bots],
          }),
          false,
          'addBot',
        ),

      updateBot: (botId, updates) =>
        set(
          (state) => ({
            bots: state.bots.map((bot) =>
              bot.bot_id === botId ? { ...bot, ...updates } : bot,
            ),
          }),
          false,
          'updateBot',
        ),

      removeBot: (botId) =>
        set(
          (state) => ({
            bots: state.bots.filter((bot) => bot.bot_id !== botId),
            // 使用 null 而非空字符串，区分"未选择"和"无效"
            activeBotId: state.activeBotId === botId ? null : state.activeBotId,
          }),
          false,
          'removeBot',
        ),

      setLoading: (loading) => set({ isLoading: loading }, false, 'setLoading'),

      setBotPollTimeout: (timeout) =>
        set({ botPollTimeout: timeout }, false, 'setBotPollTimeout'),

      clearBotPollTimeout: () =>
        set({ botPollTimeout: null }, false, 'clearBotPollTimeout'),

      setDeletingActiveBot: (deleting) =>
        set({ isDeletingActiveBot: deleting }, false, 'setDeletingActiveBot'),

      setTotalBotCount: (count) =>
        set({ totalBotCount: count }, false, 'setTotalBotCount'),

      setMaxBotCount: (count) =>
        set({ maxBotCount: count }, false, 'setMaxBotCount'),

      // UI Actions
      openBotManager: () =>
        set({ isBotManagerOpen: true }, false, 'openBotManager'),

      closeBotManager: () =>
        set({ isBotManagerOpen: false }, false, 'closeBotManager'),

      toggleBotManager: () =>
        set(
          (state) => ({ isBotManagerOpen: !state.isBotManagerOpen }),
          false,
          'toggleBotManager',
        ),

      // Computed Getters
      getActiveBotById: () => {
        const { bots, activeBotId } = get();
        if (!activeBotId) return undefined;
        return bots.find((bot) => bot.bot_id === activeBotId);
      },

      getBotById: (id) => {
        return get().bots.find((bot) => bot.bot_id === id);
      },

      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'BotStore' },
  ),
);
