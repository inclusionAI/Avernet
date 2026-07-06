/**
 * BotNetwork Store - Bot 协作网络状态管理
 *
 * 遵循三层架构：Store层只管理纯数据状态，不包含API调用和Toast逻辑
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

// === 类型定义 ===

/** Bot 可见性/参与模式
 * - private: 隐身，仅注册，不可被发现，不可加好友（灰色）
 * - protected: 受保护，可以被发现，加好友要确认（蓝色）
 * - public: 公开，可以被发现，加好友不需要确认（绿色）
 * - offline: 未入网，历史 Bot 数据但尚未注册到 BCN 网络
 */
export type BotVisibility = 'private' | 'protected' | 'public' | 'offline';

/** 动态状态 */
export interface DynamicStatus {
  status: 'active' | 'offline';
  last_active_at?: number;
}

/**
 * 统一计算 Bot/Actor 是否"在线可用"
 *
 * 判断优先级：
 * 1. dynamic_status.status === 'offline' → 不可用（运行时与 BCN 连接断开）
 * 2. status === 'hidden' → 不可用（用户主动隐身）
 * 3. visibility === 'offline' → 不可用（未入网）
 * 4. 其余 → 在线可用
 */
export function computeIsOnline(bot: {
  status?: 'online' | 'hidden' | 'offline';
  visibility?: BotVisibility;
  dynamic_status?: DynamicStatus;
}): boolean {
  // 运行时连接断开，即使 status=online 也视为不可用
  if (bot.dynamic_status?.status === 'offline') return false;
  // actor 离线（human actor 使用 status 而非 dynamic_status）
  if (bot.status === 'offline') return false;
  // 用户主动隐身
  if (bot.status === 'hidden') return false;
  // 未入网
  if (bot.visibility === 'offline') return false;
  return true;
}

/** Bot 网络信息 */
export interface BotNetworkInfo {
  bot_uuid: string;
  bot_name: string;
  summary?: string;
  avatar_url?: string;
  visibility: BotVisibility;
  is_online: boolean;
  capabilities: {
    name: string | null;
    summary: string | null;
    domains: string[];
    skills: string[];
    scopes: string[];
  };
  /** 是否需要刷新（BCN 名称与外部名称不一致时为 true） */
  needsRefresh?: boolean;
  /** 外部传入的原始名称（用于刷新时调用 onboard） */
  externalName?: string;
  /** Actor 类型：human 或 bot */
  actor_kind?: 'human' | 'bot';
  /** Actor 在线状态 */
  status?: 'online' | 'hidden' | 'offline';
  /** 用户信息（当 actor_kind 为 human 时） */
  user_info?: {
    user_id: string;
    nickName?: string;
    realName?: string;
    avatarUrl?: string;
  };
  /** 动态状态（用于展示在线/离线） */
  dynamic_status?: DynamicStatus;
  /** 是否需要初始化 Human Actor */
  needsHumanInit?: boolean;
}

/** 发现 Bot 信息（含好友关系） */
export interface DiscoverBotInfo extends BotNetworkInfo {
  is_friend?: boolean | null;
  /** 追踪ID，用于埋点关联 */
  trace_id?: string;
  /** 查询类型 */
  type?: 'search' | 'recommend';
}

/** 驱动 Bot 信息 */
export interface DriverBot {
  bot_uuid: string;
  bot_name: string;
  visibility?: BotVisibility;
  avatar_url?: string;
  /** Actor 在线状态 */
  status?: 'online' | 'hidden' | 'offline';
  /** 动态状态（用于展示在线/隐身） */
  dynamic_status?: DynamicStatus;
  is_online?: boolean;
}

// === Store State ===

interface BotNetworkState {
  // === 我的 Bot 列表（从 BCN 获取） ===
  myBots: BotNetworkInfo[];
  isLoadingMyBots: boolean;

  // === 当前选中的驱动 Bot ===
  driverBot: DriverBot | null;

  // === 公开 Bot 发现（用于拉起协作）===
  discoveredBots: DiscoverBotInfo[];
  isLoadingDiscover: boolean;

  // === 推荐好友 Bot 列表（用于添加好友，不带 collaborate_bot 参数）===
  recommendedBots: DiscoverBotInfo[];
  isLoadingRecommended: boolean;
  recommendedPagination: {
    limit: number;
    offset: number;
    total: number;
  };

  // === 分页状态 ===
  discoverPagination: {
    limit: number;
    offset: number;
    total: number;
  };

  // === Actions: Bot 列表 ===
  setMyBots: (bots: BotNetworkInfo[]) => void;
  addMyBot: (bot: BotNetworkInfo) => void;
  updateMyBot: (botUuid: string, updates: Partial<BotNetworkInfo>) => void;
  removeMyBot: (botUuid: string) => void;

  // === Actions: 驱动 Bot ===
  setDriverBot: (bot: DriverBot | null) => void;

  // === Actions: 发现 Bot（拉起协作）===
  setDiscoveredBots: (bots: DiscoverBotInfo[]) => void;
  appendDiscoveredBots: (bots: DiscoverBotInfo[]) => void;
  updateDiscoverPagination: (
    pagination: Partial<BotNetworkState['discoverPagination']>,
  ) => void;

  // === Actions: 推荐好友 Bot ===
  setRecommendedBots: (bots: DiscoverBotInfo[]) => void;
  appendRecommendedBots: (bots: DiscoverBotInfo[]) => void;
  updateRecommendedPagination: (
    pagination: Partial<BotNetworkState['recommendedPagination']>,
  ) => void;

  // === Loading Setters ===
  setLoadingMyBots: (loading: boolean) => void;
  setLoadingDiscover: (loading: boolean) => void;
  setLoadingRecommended: (loading: boolean) => void;

  // === Computed ===
  getMyBotByUuid: (botUuid: string) => BotNetworkInfo | undefined;

  // === Reset ===
  reset: () => void;
}

const initialState = {
  myBots: [] as BotNetworkInfo[],
  isLoadingMyBots: false,
  driverBot: null as DriverBot | null,
  discoveredBots: [] as DiscoverBotInfo[],
  isLoadingDiscover: false,
  recommendedBots: [] as DiscoverBotInfo[],
  isLoadingRecommended: false,
  recommendedPagination: {
    limit: 20,
    offset: 0,
    total: 0,
  },
  discoverPagination: {
    limit: 20,
    offset: 0,
    total: 0,
  },
};

export const useBotNetworkStore = create<BotNetworkState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      // === Actions: Bot 列表 ===
      setMyBots: (bots) => set({ myBots: bots }, false, 'setMyBots'),

      addMyBot: (bot) =>
        set((state) => ({ myBots: [...state.myBots, bot] }), false, 'addMyBot'),

      updateMyBot: (botUuid, updates) =>
        set(
          (state) => ({
            myBots: state.myBots.map((bot) =>
              bot.bot_uuid === botUuid ? { ...bot, ...updates } : bot,
            ),
          }),
          false,
          'updateMyBot',
        ),

      removeMyBot: (botUuid) =>
        set(
          (state) => ({
            myBots: state.myBots.filter((bot) => bot.bot_uuid !== botUuid),
            driverBot:
              state.driverBot?.bot_uuid === botUuid ? null : state.driverBot,
          }),
          false,
          'removeMyBot',
        ),

      // === Actions: 驱动 Bot ===
      setDriverBot: (bot) => set({ driverBot: bot }, false, 'setDriverBot'),

      // === Actions: 发现 Bot（拉起协作）===
      setDiscoveredBots: (bots) =>
        set({ discoveredBots: bots }, false, 'setDiscoveredBots'),

      appendDiscoveredBots: (bots) =>
        set(
          (state) => ({ discoveredBots: [...state.discoveredBots, ...bots] }),
          false,
          'appendDiscoveredBots',
        ),

      updateDiscoverPagination: (pagination) =>
        set(
          (state) => ({
            discoverPagination: { ...state.discoverPagination, ...pagination },
          }),
          false,
          'updateDiscoverPagination',
        ),

      // === Actions: 推荐好友 Bot ===
      setRecommendedBots: (bots) =>
        set({ recommendedBots: bots }, false, 'setRecommendedBots'),

      appendRecommendedBots: (bots) =>
        set(
          (state) => ({ recommendedBots: [...state.recommendedBots, ...bots] }),
          false,
          'appendRecommendedBots',
        ),

      updateRecommendedPagination: (pagination) =>
        set(
          (state) => ({
            recommendedPagination: {
              ...state.recommendedPagination,
              ...pagination,
            },
          }),
          false,
          'updateRecommendedPagination',
        ),

      // === Loading Setters ===
      setLoadingMyBots: (loading) =>
        set({ isLoadingMyBots: loading }, false, 'setLoadingMyBots'),
      setLoadingDiscover: (loading) =>
        set({ isLoadingDiscover: loading }, false, 'setLoadingDiscover'),
      setLoadingRecommended: (loading) =>
        set({ isLoadingRecommended: loading }, false, 'setLoadingRecommended'),

      // === Computed ===
      getMyBotByUuid: (botUuid) => {
        return get().myBots.find((bot) => bot.bot_uuid === botUuid);
      },

      // === Reset ===
      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'BotNetworkStore' },
  ),
);
