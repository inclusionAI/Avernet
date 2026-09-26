/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * useBot - Bot 业务逻辑 Hook
 *
 * 直接使用后端 API 的 Bot 类型，不做前端二次映射。
 */

import { savePreferredBot } from '@/pages/Bootstrap/Bot/botPreference';
import * as BotController from '@/services/backend-api/BotController';
import {
  BOT_STATUS,
  ENGINE_TYPE,
  fetchBotConnection,
  PERMISSION_OWNER,
  type Bot,
  type BotType,
  type DesktopDevice,
  type DeviceFileNode,
  type EngineType,
  type McpInfo,
  type PermissionOwner,
  type PublicStatus,
} from '@/services/backend-api/BotController';
import { useBotStore, type BotPollTimeoutReason } from '@/stores/botStore';
import { setConnection, useConnectionStore } from '@/stores/connectionStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useModelStore } from '@/stores/modelStore';
import { useUserStore } from '@/stores/userStore';
import { resolveBotOwnerId } from '@/utils/activeBotContext';
import { countOwnedBots } from '@/utils/botCount';
import { pollBotUntilSettled } from '@/utils/botPolling';
import { findFallbackBot, isValidBotId } from '@/utils/botUtils';
import {
  handleApiError,
  handleNetworkError,
  showSuccessToast,
} from '@/utils/hooksErrorHandler';
import { removeSkillSetExpands } from '@/utils/skillSetExpandStorage';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

// 模块级 Set：跟踪正在轮询的 bot id，所有 useBot() 实例共享，避免重复轮询
const pollingBotIds = new Set<string>();

// 模块级 Map：每个 bot 的轮询 generation，用于取消旧轮询
const pollingGenerations = new Map<string, number>();

// 模块级 Set：服务 Bot 就绪时的刷新回调
const serviceBotReadyCallbacks = new Set<() => void>();

// 模块级锁：防止并发请求 /bots/by-owner
let isLoadingBotsLocked = false;

// 模块级标记：是否已经获取过 Bot 总数（全局只执行一次）
let hasFetchedTotalBotCount = false;

// 模块级 in-flight 去重：/bots/by-owner-or-collaborator 有三处调用方
// （loadBots / loadAllBots / fetchTotalBotCount），参数相同（user_id），但各用各的
// 去重门、互不感知，并发挂载时会重复打同一接口。这里把网络调用收口为一个共享
// in-flight Promise：并发调用合并为一次请求，各自处理同一份响应；settle 后清空，
// 不做长期缓存（仅去重并发重复）。
let byOwnerInFlight: ReturnType<typeof BotController.getBotsByOwner> | null =
  null;
let byOwnerInFlightUserId: string | null = null;

function fetchBotsByOwnerDeduped(userId: string) {
  if (byOwnerInFlight && byOwnerInFlightUserId === userId) {
    return byOwnerInFlight;
  }
  byOwnerInFlightUserId = userId;
  byOwnerInFlight = BotController.getBotsByOwner(
    { user_id: userId },
    { skipErrorHandler: true },
  );
  // settle 后清空 in-flight 句柄（成功/失败都清）；用 then 双回调避免悬空 rejection
  const clear = () => {
    byOwnerInFlight = null;
    byOwnerInFlightUserId = null;
  };
  byOwnerInFlight.then(clear, clear);
  return byOwnerInFlight;
}

/**
 * 注册服务 Bot 就绪时的刷新回调
 * @param callback 回调函数
 * @returns 注销函数
 */
export function onServiceBotReady(callback: () => void): () => void {
  serviceBotReadyCallbacks.add(callback);
  return () => {
    serviceBotReadyCallbacks.delete(callback);
  };
}

export interface CreateBotParams {
  customName: string;
  ownerType: 'staff' | 'team' | 'proj';
  ownerId: string;
  ownerName: string;
  engine?: EngineType;
  botDesc?: string;
  /** 头像 URL */
  avatarUrl?: string;
  /** Bot 类型: 'personal'=个人型, 'service'=服务型，默认 'personal' */
  botType?: BotType;
  /** Bot 创建成功后、刷新列表和轮询前执行的附加初始化 */
  afterCreate?: (bot: Bot) => Promise<void>;
  /** 模板配置（应用 Coding 等引擎的专属配置） */
  templateConfig?: import('@/services/backend-api/BotController').TemplateConfig;
  /** 模板类型，如 'applicationCoding' */
  templateType?: string;
}

/**
 * 创建 Bot 返回结果
 */
export type CreateBotResult =
  | { type: 'success'; bot: Bot }
  | {
      type: 'auth_required';
      botId: string;
      iframeUrl: string;
      redirectUrl: string;
    }
  | { type: 'error'; message: string };

/**
 * 搜索 Bot 参数
 */
export interface SearchBotsParams {
  /** 公开状态: '0'=私有, '1'=公开 */
  public?: PublicStatus;
  /** Bot 名称(支持模糊查询) */
  bot_name?: string;
  /** 所有者名称 */
  owner_name?: string;
  /** 页码,从 1 开始 */
  page?: number;
  /** 每页数量,范围 1-100 */
  page_size?: number;
  /** Bot 类型过滤: 'personal'=个人型, 'service'=服务型 */
  bot_type?: BotType;
  /** 关键词（模糊搜索 bot_name 或 owner_name） */
  key?: string;
  /** Bot 状态过滤 */
  bot_status?: 'PENDING' | 'ACTIVE' | 'FAILED' | 'RELEASED';
  /** 所有者 ID 过滤 */
  owner_id?: string;
  /** 发布状态过滤 */
  service_status_list?: string[];
}

/**
 * 搜索 Bot 结果
 */
export interface SearchBotsResult {
  items: Bot[];
  total: number;
}

/**
 * 设置 Bot 公开状态参数
 */
export interface SetBotPublicParams {
  botId: string;
  /**
   * 协作场景下显式传入的 Bot 拥有者 ID。
   * 服务 Bot 列表等场景的 Bot 不在 botStore 中，resolveBotOwnerId 反查不到，
   * 调用方手边已有 bot.owner_id 时应显式传入；省略则 fallback 到 resolveBotOwnerId(botId)。
   */
  ownerId?: string;
  /** 公开状态: '0'=私有, '1'=公开 */
  public: PublicStatus;
  /** 权限控制: caller=直接更新, owner=需要审批后更新 */
  permission_owner?: PermissionOwner;
  /** 好友审批: '0'=不需要审批, '1'=需要审批（公开时生效） */
  friend_approval?: '0' | '1';
}

/**
 * Bot 管理 Hook
 * 封装所有 Bot 相关的业务逻辑和 API 调用
 *
 * @param options.autoFetchTotalBotCount 挂载时是否自动拉取 Bot 总数（默认 true）。
 *   不消费 totalBotCount / 配额的页面（如 BCN 群聊）应传 false，避免无谓触发
 *   /api/bots/by-owner-or-collaborator（该接口产出在 BCN 闭包内零消费）。
 */
export function useBot(options?: { autoFetchTotalBotCount?: boolean }) {
  const userId = useUserStore((state) => state.userId);
  const {
    bots,
    activeBotId,
    isLoading,
    totalBotCount,
    setBots,
    setActiveBotId,
    addBot,
    updateBot,
    removeBot,
    setLoading,
    setTotalBotCount,
    setAllBots,
    getActiveBotById,
    getBotById,
  } = useBotStore();

  // 注意：使用模块级变量 hasFetchedTotalBotCount 来确保全局只获取一次 Bot 总数

  // 所有 Bot 列表（allBots 已存入 store，包含 personal 和 service）
  const allBots = useBotStore((state) => state.allBots);
  const [isLoadingAllBots, setIsLoadingAllBots] = useState(false);

  /**
   * 轮询 Bot 状态直到 ACTIVE，期间实时更新 store
   * 激活成功时：若 BotManager 已关闭，弹持久 toast 通知用户（含耗时）
   * 超时后写入 botStore.botPollTimeout，由 BotManager 统一渲染 AlertDialog
   * @returns true = 激活成功，false = 超时或失败
   */
  const pollBotStatus = useCallback(
    async (
      botId: string,
      reason: BotPollTimeoutReason = 'activate',
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      options?: { skipSyncMcps?: boolean },
    ): Promise<boolean> => {
      // 检查是否是服务 Bot，如果是则不轮询
      const bot = useBotStore.getState().getBotById(botId);
      if (bot?.bot_type === 'service') {
        console.log('[useBot] 服务 Bot 跳过轮询:', botId);
        return false;
      }

      const startTime = Date.now();
      // 分配一个新的 generation 给当前轮询
      const myGeneration = (pollingGenerations.get(botId) || 0) + 1;
      pollingGenerations.set(botId, myGeneration);
      console.log('[useBot] Starting polling with generation:', myGeneration);

      const result = await pollBotUntilSettled(botId, {
        shouldContinue: () => pollingGenerations.get(botId) === myGeneration,
      });

      switch (result.outcome) {
        case 'active': {
          updateBot(botId, result.bot);
          // 服务 Bot 就绪时触发刷新回调
          if (result.bot.bot_type === 'service') {
            serviceBotReadyCallbacks.forEach((cb) => {
              try {
                cb();
              } catch (e) {
                console.error('[useBot] 服务 Bot 就绪回调执行失败:', e);
              }
            });
          }
          // BotManager 已关闭时弹持久 toast 通知
          if (!useBotStore.getState().isBotManagerOpen) {
            const elapsedSec = Math.round((Date.now() - startTime) / 1000);
            const elapsedText =
              elapsedSec >= 60
                ? `${Math.floor(elapsedSec / 60)} 分 ${elapsedSec % 60} 秒`
                : `${elapsedSec} 秒`;
            toast.success(`Bot "${result.bot.bot_name || botId}" 已就绪`, {
              description: `耗时 ${elapsedText}`,
              duration: 15000,
            });
          }
          // NOTE: 0408 删除 syncMcpsToDevice 调用
          // Bot 启动成功后同步 MCPs 到设备（页面会刷新的场景跳过，依赖初始化调用）
          // if (!options?.skipSyncMcps) {
          //   syncMcpsToDevice(true).catch((error) => {
          //     console.error('[useBot] 同步 MCPs 失败:', error);
          //   });
          // }
          return true;
        }
        case 'failed':
          updateBot(botId, { status: BOT_STATUS.FAILED });
          toast.error(result.message);
          return false;
        case 'cancelled':
          // 轮询被新的重启操作取消，静默返回
          console.log('[useBot] Polling cancelled for bot:', botId);
          return false;
        case 'timeout':
          useBotStore.getState().setBotPollTimeout({ botId, reason });
          return false;
        case 'error': {
          console.error('[useBot] Poll bot status failed:', result.error);
          // 使用工具函数格式化错误信息
          const pollErrorMsg = handleNetworkError(result.error, {
            module: 'Bot',
            action: '轮询 Bot 状态',
            showToast: false,
          });
          toast.error(pollErrorMsg || result.message);
          return false;
        }
        default:
          return false;
      }
    },
    [updateBot],
  );

  /**
   * 加载 Bot 列表
   * @param options.skipAutoActivate - 是否跳过自动激活 default bot（切换流程中使用）
   * @param options.force - 是否强制刷新，默认 false（有数据时不重复请求）
   */
  const loadBots = useCallback(
    async (options?: { skipAutoActivate?: boolean; force?: boolean }) => {
      if (!userId) {
        console.warn('[useBot] userId is not set, skipping load');
        return;
      }

      // 模块级锁：防止并发请求
      if (isLoadingBotsLocked) {
        console.log(
          '[useBot] Request already in progress, skipping duplicate loadBots',
        );
        return;
      }

      // 获取当前状态
      const state = useBotStore.getState();
      // 如果不是强制刷新且已经有 Bot 数据，跳过请求
      if (!options?.force && state.bots.length > 0) {
        console.log('[useBot] Bots already loaded, skipping loadBots');
        return;
      }

      // 记录调用堆栈用于排查频繁调用问题
      console.log('[by-owner][useBot] loadBots called with options:', options);
      console.trace('[by-owner][useBot] loadBots call stack');

      try {
        isLoadingBotsLocked = true;
        setLoading(true);
        const response = await fetchBotsByOwnerDeduped(userId);

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '加载 Bot 列表',
        });
        if (errorMsg) return;

        // 只展示 personal 和 desktop 类型的 Bot，过滤掉 service 类型
        const allItems = response.data!.items || [];
        const apiBots = allItems.filter(
          (bot) =>
            !bot.bot_type ||
            bot.bot_type === 'personal' ||
            bot.bot_type === 'desktop',
        );
        const defaultBotInfo = response.data!.default_bot;
        setBots(apiBots);
        // 同时更新 allBots（包含 personal + service），供能力市场、定时任务等页面使用
        setAllBots(allItems);
        // 更新 Bot 总数：接口返回并集（owner + 协作），配额只统计当前用户拥有的 Bot，
        // 需排除协作 Bot 和桌面 Bot，不能直接用 response.data.total
        setTotalBotCount(countOwnedBots(allItems, userId));

        // 如果没有激活的 Bot，优先使用后端返回的 default_bot
        // 注意：我们不从 localStorage 读取，因为 initBot.ts（bootstrap 阶段）已经处理了偏好逻辑
        // skipAutoActivate: 切换流程中跳过，避免覆盖即将设置的目标 Bot
        if (
          !options?.skipAutoActivate &&
          !activeBotId &&
          apiBots.length > 0 &&
          defaultBotInfo?.bot_id
        ) {
          console.log(
            '[useBot] 使用后端返回的 default_bot:',
            defaultBotInfo.bot_id,
          );
          setActiveBotId(defaultBotInfo.bot_id);
        }

        // 对列表中 PENDING 状态的 Bot，先核查状态，两个字段都是 PENDING 才启动轮询
        const pendingBots = apiBots.filter(
          (bot) =>
            bot.status === BOT_STATUS.PENDING && !pollingBotIds.has(bot.bot_id),
        );
        for (const bot of pendingBots) {
          pollingBotIds.add(bot.bot_id);
          BotController.getBotStatus(
            {
              bot_id: bot.bot_id,
              owner_id: bot.owner_id,
            },
            { skipErrorHandler: true },
          )
            .then((statusRes) => {
              // 处理业务错误
              const statusError = handleApiError(statusRes, {
                module: 'Bot',
                action: '查询 Bot 状态',
                showToast: false,
              });
              if (statusError) {
                console.error(
                  `[useBot] Bot ${bot.bot_id} 状态查询失败:`,
                  statusError,
                );
                pollingBotIds.delete(bot.bot_id);
                return;
              }
              if (
                statusRes.data?.bot_status === BOT_STATUS.PENDING &&
                statusRes.data?.binding_status === BOT_STATUS.PENDING
              ) {
                pollBotStatus(bot.bot_id).finally(() => {
                  pollingBotIds.delete(bot.bot_id);
                });
              } else {
                pollingBotIds.delete(bot.bot_id);
              }
            })
            .catch((err: any) => {
              handleNetworkError(err, {
                module: 'Bot',
                action: '查询 Bot 状态',
              });
              pollingBotIds.delete(bot.bot_id);
            });
        }
      } catch (error) {
        console.error('[useBot] Failed to load bots:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '加载 Bot 列表',
        });
      } finally {
        setLoading(false);
        isLoadingBotsLocked = false;
      }
    },
    [userId, activeBotId, setBots, setActiveBotId, setLoading],
  );

  const maxBotCount = useBotStore((state) => state.maxBotCount);
  const setMaxBotCount = useBotStore((state) => state.setMaxBotCount);

  /**
   * 加载当前用户的 BOT 数量上限
   */
  const loadBotsCeiling = useCallback(async () => {
    try {
      const res = await BotController.getBotsCeiling();
      if (res.success && res.data?.ceiling) {
        setMaxBotCount(res.data.ceiling);
      }
    } catch (e) {
      console.warn('[useBot] Failed to load bots ceiling, using default 5', e);
    }
  }, [setMaxBotCount]);

  // 注意：ceiling（Bot 数量上限）只有 ServiceBotList 消费，故不在此 hook 内自动加载。
  // 由消费方按需调用 loadBotsCeiling()，避免所有借用 useBot 的页面（BCN 群聊等）
  // 都触发一次无谓的 /api/bots/ceiling 请求。
  const MAX_BOT_COUNT = maxBotCount;

  /**
   * 加载所有 Bot 列表（包含 personal 和 service，不做类型筛选）
   */
  const loadAllBots = useCallback(
    async (options?: { skipErrorHandler?: boolean; force?: boolean }) => {
      if (!userId) {
        console.warn('[useBot] userId is not set, skipping loadAllBots');
        return;
      }

      // 模块级锁：防止并发请求
      if (isLoadingBotsLocked) {
        console.log(
          '[useBot] Request already in progress, skipping duplicate loadAllBots',
        );
        return;
      }

      // 如果不是强制刷新且已经有 AllBots 数据，跳过请求（避免重复调用）
      const currentAllBots = useBotStore.getState().allBots;
      if (!options?.force && currentAllBots.length > 0) {
        console.log('[useBot] AllBots already loaded, skipping loadAllBots');
        return;
      }

      try {
        isLoadingBotsLocked = true;
        setIsLoadingAllBots(true);
        const response = await fetchBotsByOwnerDeduped(userId);

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '加载所有 Bot 列表',
        });
        if (errorMsg) return;

        // 不筛选类型，返回所有 Bot（personal + service）
        const apiBots = response.data?.items || [];
        setAllBots(apiBots);

        // 同时更新 Bot 总数：仅统计当前用户拥有的 Bot（排除协作 Bot 和桌面 Bot）
        setTotalBotCount(countOwnedBots(apiBots, userId));
      } catch (error) {
        console.error('[useBot] Failed to load all bots:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '加载所有 Bot 列表',
        });
      } finally {
        setIsLoadingAllBots(false);
        isLoadingBotsLocked = false;
      }
    },
    [userId, setAllBots, setTotalBotCount],
  );

  /**
   * 创建 Bot
   */
  const createBot = useCallback(
    async (params: CreateBotParams): Promise<CreateBotResult> => {
      if (!userId) {
        toast.error('用户信息缺失');
        return { type: 'error', message: '用户信息缺失' };
      }

      // 配额只统计当前用户拥有的 Bot（排除协作 Bot 和桌面 Bot）
      const currentCount = countOwnedBots(useBotStore.getState().bots, userId);
      if (currentCount >= MAX_BOT_COUNT) {
        toast.error(`云端 Bot 数量已达上限（${MAX_BOT_COUNT} 个）`);
        return {
          type: 'error',
          message: `云端 Bot 数量已达上限（${MAX_BOT_COUNT} 个）`,
        };
      }

      try {
        const response = await BotController.createBot(
          {
            bot_name: params.customName,
            bot_desc: params.botDesc || `${params.ownerName} 的 Bot`,
            avatar_url: params.avatarUrl,
            entity_id: `${userId}`,
            entity_type: params.ownerType,
            engine_type: params.engine || ENGINE_TYPE.OPENCLAW,
            bot_type: params.botType || 'personal',
            template_type: params.templateType,
            template_config: params.templateConfig,
          },
          { skipErrorHandler: true },
        );

        // 处理需要授权的场景
        if (
          response.error_code === 401 &&
          response.data &&
          'need_authorization' in response.data
        ) {
          const authData = response.data;
          return {
            type: 'auth_required',
            botId: authData.bot_id,
            iframeUrl: authData.iframe_url,
            redirectUrl: authData.redirect_url,
          };
        }

        // 处理其他业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '创建 Bot',
        });
        if (errorMsg) {
          return { type: 'error', message: errorMsg };
        }

        // 处理新响应结构：data 包含 bot 字段
        const newBot = (response.data as { bot: Bot }).bot;
        if (params.afterCreate) {
          await params.afterCreate(newBot);
        }
        toast.success('Bot 创建成功，正在等待激活...');

        // 刷新 Bot 列表（强制刷新，确保新创建的 Bot 出现在列表中）
        await loadBots({ force: true });

        // 如果是 PENDING 状态，启动后台轮询
        if (newBot.status !== BOT_STATUS.ACTIVE) {
          const newBotId = newBot.bot_id;
          pollingBotIds.add(newBotId);
          pollBotStatus(newBotId, 'create').finally(() => {
            pollingBotIds.delete(newBotId);
          });
        }

        return { type: 'success', bot: newBot };
      } catch (error) {
        console.error('[useBot] Failed to create bot:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '创建 Bot',
        });
        return { type: 'error', message: '创建 Bot 失败' };
      }
    },
    [userId, addBot, pollBotStatus, MAX_BOT_COUNT],
  );

  /**
   * 编辑 Bot（更新名称、描述、头像等）
   */
  const editBot = useCallback(
    async (
      botId: string,
      params: { bot_name?: string; bot_desc?: string; avatar_url?: string },
    ) => {
      try {
        const response = await BotController.updateBot(
          {
            bot_id: botId,
            owner_id: resolveBotOwnerId(botId),
            ...params,
          },
          { skipErrorHandler: true },
        );

        if (response.success && response.data) {
          updateBot(botId, response.data);
          toast.success('Bot 信息已更新');
          return response.data;
        } else {
          toast.error(response.message || 'Bot 更新失败');
          return null;
        }
      } catch (error: any) {
        console.error('[useBot] Failed to update bot:', error);
        toast.error(error?.data?.message || error?.message || 'Bot 更新失败');
        return null;
      }
    },
    [updateBot],
  );

  /**
   * 切换 Bot
   * @param botId 目标 Bot ID
   * @param options 可选配置
   * @param options.skipReload 是否跳过页面刷新（默认 false，兼容旧行为）
   * @returns 是否切换成功
   */
  const switchBot = useCallback(
    async (
      botId: string,
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      // _options?: { skipReload?: boolean } // 保留参数位置用于向后兼容，当前未使用
    ) => {
      const targetBot = useBotStore.getState().getBotById(botId);
      if (!targetBot?.binding_id) {
        toast.error('Bot 连接信息缺失，无法切换');
        return false;
      }

      try {
        const conn = await fetchBotConnection(targetBot.binding_id, {
          botId: targetBot.bot_id,
        });

        // 引擎类型：优先 active_engine，fallback engine_types[0]，默认 openclaw
        const apiEngine =
          targetBot.active_engine ||
          (targetBot.engine_types?.length
            ? targetBot.engine_types[0]
            : ENGINE_TYPE.OPENCLAW);

        // 清空当前模型和会话，避免切换时显示旧 Bot 的数据
        useModelStore.getState().setActiveModel(null);
        useConversationStore.getState().setActiveConvId('');

        setConnection(conn.target, conn.token, conn.type, apiEngine);

        if (userId) {
          savePreferredBot(userId, botId);
        }

        // 更新本地 store 状态
        if (activeBotId && activeBotId !== botId) {
          updateBot(activeBotId, { status: BOT_STATUS.RELEASED });
        }
        updateBot(botId, { status: BOT_STATUS.ACTIVE });
        setActiveBotId(botId);

        showSuccessToast(
          { module: 'Bot', action: '切换 Bot' },
          'Bot 切换成功，页面即将刷新',
        );
        setTimeout(() => window.location.reload(), 1500);
        return true;
      } catch (error) {
        console.error('[useBot] Failed to switch bot:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '切换 Bot',
        });
        return false;
      }
    },
    [activeBotId, updateBot, setActiveBotId, userId],
  );

  /**
   * 重启 Bot
   */
  const restartBot = useCallback(
    async (botId: string) => {
      if (!userId) {
        toast.error('用户信息缺失');
        return false;
      }

      try {
        const res = await BotController.restartBot(
          {
            bot_id: botId,
            user_id: userId,
            owner_id: resolveBotOwnerId(botId),
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(res, {
          module: 'Bot',
          action: '重启 Bot',
        });
        if (errorMsg) return false;

        updateBot(botId, { status: BOT_STATUS.PENDING, binding_id: null });
        toast.info('Bot 正在重启，请稍候...');

        pollingBotIds.add(botId);
        // 页面会刷新，跳过 syncMcps，依赖初始化调用
        const didActivate = await pollBotStatus(botId, 'restart', {
          skipSyncMcps: true,
        });
        pollingBotIds.delete(botId);

        if (didActivate) {
          showSuccessToast(
            { module: 'Bot', action: '重启 Bot' },
            'Bot 重启成功，页面即将刷新',
          );
          setTimeout(() => window.location.reload(), 1500);
        }
        return didActivate;
      } catch (error) {
        console.error('[useBot] Failed to restart bot:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '重启 Bot',
        });
        return false;
      }
    },
    [userId, updateBot, pollBotStatus],
  );

  /**
   * Reset Bot（更换容器实例）
   */
  const resetBot = useCallback(
    async (botId: string) => {
      if (!userId) {
        toast.error('用户信息缺失');
        return false;
      }

      try {
        const response = await BotController.resetBot(
          {
            bot_id: botId,
            user_id: userId,
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '重置 Bot',
        });
        if (errorMsg) return false;

        await loadBots({ force: true });
        showSuccessToast({ module: 'Bot', action: '重置 Bot' }, 'Bot 已重置');
        return true;
      } catch (error) {
        console.error('[useBot] Failed to reset bot:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '重置 Bot',
        });
        return false;
      }
    },
    [userId, loadBots],
  );

  /**
   * 删除 Bot
   * 注意：删除当前激活的 Bot 的处理逻辑：
   * 1. 如果有其他可用的 Bot，自动切换（方案2：无缝切换）
   * 2. 如果没有其他 Bot，立即刷新页面重新初始化（方案1：立即刷新）
   * 无论哪种情况，都会先显示遮罩禁止用户操作
   *
   * @param botId 要删除的 Bot ID
   * @param options 可选配置
   * @param options.onSwitchBot 自定义切换函数（如 useBotSwitch 的 switchBot），用于在 Assistant 页面内切换而不刷新页面
   * @param options.onSuccess 删除成功回调，用于自定义删除后的行为（如服务 Bot 删除后跳转列表页）
   */
  const deleteBot = useCallback(
    async (
      botId: string,
      options?: {
        onSwitchBot?: (targetBot: Bot) => Promise<boolean>;
        onSuccess?: () => void;
      },
    ) => {
      if (!userId) {
        toast.error('用户信息缺失');
        return false;
      }

      const isActiveBot = useBotStore.getState().activeBotId === botId;

      // 【方案一】删除前停止该 Bot 的轮询，避免 deleteBot → status 报错
      if (pollingBotIds.has(botId)) {
        pollingBotIds.delete(botId);
        // 增加 generation 以取消正在进行的轮询
        pollingGenerations.set(botId, (pollingGenerations.get(botId) || 0) + 1);
        console.log('[useBot] Stopped polling for bot before delete:', botId);
      }

      // 如果是当前激活的 Bot，先设置遮罩状态，防止用户继续操作
      if (isActiveBot) {
        useBotStore.getState().setDeletingActiveBot(true);
      }

      try {
        const response = await BotController.deleteBot(
          {
            bot_id: botId,
            owner_id: resolveBotOwnerId(botId),
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '删除 Bot',
        });
        if (errorMsg) {
          // 出错时取消遮罩状态
          if (isActiveBot) {
            useBotStore.getState().setDeletingActiveBot(false);
          }
          return false;
        }

        // 如果传入了自定义成功回调，直接执行（如服务 Bot 跳转列表页）
        if (options?.onSuccess) {
          removeBot(botId);
          removeSkillSetExpands(botId);
          options.onSuccess();
          return true;
        }

        // 查找备选 Bot（用于自动切换）
        const fallbackBot = isActiveBot
          ? findFallbackBot(useBotStore.getState().bots, botId)
          : null;

        // 如果是当前激活的 Bot，先重置连接（防止后续请求路由到已删除的 Bot）
        if (isActiveBot) {
          useConnectionStore.getState().reset();
        }

        removeBot(botId);

        // 清理该 Bot 的技能集展开状态
        removeSkillSetExpands(botId);

        if (isActiveBot) {
          if (fallbackBot && isValidBotId(fallbackBot.bot_id)) {
            // 方案2: 有备选 Bot，自动切换（无缝切换）
            toast.info(`当前 Bot 已删除，自动切换到 "${fallbackBot.bot_name}"`);

            // 优先使用传入的自定义切换函数（如 useBotSwitch 的 switchBot，不刷新页面）
            // 否则使用默认的 switchBot（会刷新页面）
            const switchFn = options?.onSwitchBot || switchBot;
            const switched = await switchFn(fallbackBot);
            return switched;
          } else {
            // 方案1: 没有可切换的备选 Bot（无其他 Bot 或其他 Bot 非 ACTIVE）
            // 刷新页面回到初始化状态，像新用户一样走 Bootstrap 流程
            useConnectionStore.getState().reset();
            toast.info('当前 Bot 已删除，页面将刷新以重新初始化');
            // 立即刷新页面，Bootstrap 会处理后续初始化
            window.location.reload();
          }
        } else {
          showSuccessToast({ module: 'Bot', action: '删除 Bot' }, 'Bot 已删除');
        }
        return true;
      } catch (error) {
        // 出错时取消遮罩状态
        if (isActiveBot) {
          useBotStore.getState().setDeletingActiveBot(false);
        }
        console.error('[useBot] Failed to delete bot:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '删除 Bot',
        });
        return false;
      }
    },
    [userId, removeBot, switchBot],
  );

  /**
   * 获取 Bot 详情
   * @param botId Bot ID
   * @param ownerId 协作场景下显式传入的拥有者 ID（服务 Bot 等不在 botStore 的场景）；
   *                省略则 fallback 到 resolveBotOwnerId(botId)
   * @returns Bot 详情，失败返回 null
   */
  const getBotDetail = useCallback(
    async (botId: string, ownerId?: string): Promise<Bot | null> => {
      try {
        const response = await BotController.getBotDetail(
          {
            bot_id: botId,
            owner_id: ownerId ?? resolveBotOwnerId(botId),
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '获取 Bot 详情',
        });
        if (errorMsg) return null;

        // 更新 store 中的 Bot 信息
        updateBot(botId, response.data!);

        return response.data;
      } catch (error) {
        console.error('[useBot] Failed to get bot detail:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '获取 Bot 详情',
        });
        return null;
      }
    },
    [updateBot],
  );

  /**
   * 检查 Bot 名称是否已存在
   * @param botName Bot 名称
   * @returns 检查结果，失败返回 null
   */
  const checkBotNameExists = useCallback(
    async (
      botName: string,
    ): Promise<{ exists: boolean; botName: string } | null> => {
      try {
        const response = await BotController.checkBotName(
          { bot_name: botName },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '检查 Bot 名称',
        });
        if (errorMsg) return null;

        return {
          exists: Boolean(response.data!.exists),
          botName: response.data!.bot_name,
        };
      } catch (error) {
        console.error('[useBot] Failed to check bot name:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '检查 Bot 名称',
        });
        return null;
      }
    },
    [],
  );

  /**
   * 条件搜索 Bot
   * 用于能力市场等场景搜索公开的 Bot
   */
  const searchBots = useCallback(
    async (params: SearchBotsParams): Promise<SearchBotsResult | null> => {
      try {
        const response = await BotController.searchBotsByConditions(params, {
          skipErrorHandler: true,
        });

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '搜索 Bot',
        });
        if (errorMsg) return null;

        return {
          items: response.data!.items,
          total: response.data!.total,
        };
      } catch (error) {
        console.error('[useBot] Failed to search bots:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '搜索 Bot',
        });
        return null;
      }
    },
    [],
  );

  /**
   * 设置 Bot 公开状态
   * @param params 设置参数（含可选 ownerId，服务 Bot 等场景显式传入）
   * @returns 更新后的 Bot 信息，失败返回 null
   */
  const setBotPublic = useCallback(
    async (params: SetBotPublicParams): Promise<Bot | null> => {
      try {
        const response = await BotController.setBotPublic(
          {
            bot_id: params.botId,
            owner_id: params.ownerId ?? resolveBotOwnerId(params.botId),
            public: params.public,
            permission_owner: params.permission_owner || PERMISSION_OWNER.OWNER,
            friend_approval: params.friend_approval,
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '设置 Bot 公开状态',
        });
        if (errorMsg) return null;

        // 更新 store 中的 Bot 信息
        updateBot(params.botId, response.data!);

        return response.data;
      } catch (error) {
        console.error('[useBot] Failed to set bot public:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '设置 Bot 公开状态',
        });
        return null;
      }
    },
    [updateBot],
  );

  /**
   * 查询 Bot Agent Passport 信息
   * @param botId Bot ID
   * @param ownerId 协作场景下显式传入的拥有者 ID；省略则 fallback 到 resolveBotOwnerId(botId)
   * @returns Passport 信息，失败返回 null
   */
  const queryAgentPassport = useCallback(
    async (
      botId: string,
      ownerId?: string,
    ): Promise<{
      agentId: string;
      agentCode: string;
      credentialId: string;
      expireAt: string;
      mcps: McpInfo[];
      certificateUrl: string;
    } | null> => {
      try {
        const response = await BotController.queryAgentPassport(
          {
            bot_id: botId,
            owner_id: ownerId ?? resolveBotOwnerId(botId),
          },
          { skipErrorHandler: true },
        );

        // 许可证查询失败静默处理，不出 toast
        if (!response.success || !response.data) {
          return null;
        }

        const data = response.data;
        return {
          agentId: data.agent_id,
          agentCode: data.agent_code,
          credentialId: data.credential_id,
          expireAt: data.expire_at,
          mcps: data.mcps,
          certificateUrl: data.certificate_url,
        };
      } catch (error) {
        console.error('[useBot] Failed to query agent passport:', error);
        // handleNetworkError(error, {
        //   module: 'Bot',
        //   action: '查询 Bot 许可证',
        // });
        return null;
      }
    },
    [],
  );

  /**
   * 查询 Bot 授权状态
   * @param params 授权状态查询参数
   * @returns 授权状态响应，失败返回 null
   */
  const getBotAuthStatus = useCallback(
    async (params: {
      bot_id: string;
      bot_name: string;
      bot_desc: string;
      entity_id?: string;
      entity_type?: 'staff' | 'proj' | 'team';
      share_policy?: Record<string, any>;
      engine_type?: string;
      avatar_url?: string;
      bot_type?: BotType;
      template_type?: string;
      template_config?: import('@/services/backend-api/BotController').TemplateConfig;
    }): Promise<{
      status: 'PENDING' | 'ISSUED';
      bot?: Bot;
    } | null> => {
      try {
        const response = await BotController.getBotAuthStatus(params, {
          skipErrorHandler: true,
        });

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '查询 Bot 授权状态',
        });
        if (errorMsg) {
          return null;
        }

        return {
          status: response?.data?.status,
          bot: response?.data?.bot,
        };
      } catch (error) {
        console.error('[useBot] Failed to get bot auth status:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '查询 Bot 授权状态',
        });
        return null;
      }
    },
    [],
  );

  /**
   * 查询桌面 Bot 授权状态（两段式第二步）
   * POST /api/desktop/bots/auth-status
   */
  const getDesktopBotAuthStatus = useCallback(
    async (params: {
      bot_id: string;
      bot_name?: string;
      bot_desc?: string;
      avatar_url?: string;
      machine_id?: string;
      mount_path?: string;
    }): Promise<{
      status: 'PENDING' | 'ISSUED';
      bot?: Bot;
    } | null> => {
      try {
        const response = await BotController.getDesktopBotAuthStatus(params, {
          skipErrorHandler: true,
        });

        // 处理业务错误
        const errorMsg = handleApiError(response, {
          module: 'Bot',
          action: '查询桌面 Bot 授权状态',
        });
        if (errorMsg) {
          return null;
        }

        return {
          status: response?.data?.status,
          bot: response?.data?.bot,
        };
      } catch (error) {
        console.error('[useBot] Failed to get desktop bot auth status:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '查询桌面 Bot 授权状态',
        });
        return null;
      }
    },
    [],
  );

  /**
   * 触发 Bot 数据初始化
   * @param botId Bot ID
   * @param force 是否强制重新执行
   * @returns 初始化状态响应，失败返回 null
   */
  const triggerDataInit = useCallback(
    async (
      botId: string,
      force?: boolean,
    ): Promise<{
      status: 'in_progress' | 'pending_init' | 'skipped' | string;
      message: string;
    } | null> => {
      try {
        const response = await BotController.triggerBotDataInit(
          {
            bot_id: botId,
            owner_id: resolveBotOwnerId(botId),
            force: force || false,
          },
          { skipErrorHandler: true },
        );

        // 处理业务错误，仅输出日志
        if (response?.success === false) {
          console.log('[useBot] triggerDataInit business error:', response);
          return null;
        }

        console.log('[useBot] triggerDataInit success:', response?.data);

        return {
          status: response?.data?.status,
          message: response?.data?.message,
        };
      } catch (error) {
        console.log('[useBot] triggerDataInit error:', error);
        return null;
      }
    },
    [],
  );

  const pendingBots = bots.filter((b: Bot) => b.status === BOT_STATUS.PENDING);

  // 获取用户 Bot 总数（通过 by-owner 接口，只执行一次）
  const fetchTotalBotCount = useCallback(async () => {
    if (!userId || hasFetchedTotalBotCount) return;
    try {
      hasFetchedTotalBotCount = true;
      const response = await fetchBotsByOwnerDeduped(userId);
      if (response.success && response.data) {
        // 接口返回 owner + 协作的并集，配额只统计当前用户拥有的 Bot，
        // 需排除协作 Bot（属于他人）和桌面 Bot（不占云端配额）
        setTotalBotCount(countOwnedBots(response.data?.items || [], userId));
      }
    } catch (error) {
      console.error('[useBot] Failed to fetch total bot count:', error);
      hasFetchedTotalBotCount = false;
    }
  }, [userId]);

  // 组件挂载或 userId 变化时获取 Bot 总数（只执行一次）
  // 不消费 totalBotCount 的页面（如 BCN 群聊）传 autoFetchTotalBotCount:false 关闭，
  // 避免无谓触发 /api/bots/by-owner-or-collaborator。
  const autoFetchTotalBotCount = options?.autoFetchTotalBotCount ?? true;
  useEffect(() => {
    if (autoFetchTotalBotCount) fetchTotalBotCount();
  }, [fetchTotalBotCount, autoFetchTotalBotCount]);

  const isMaxBotCount = totalBotCount >= MAX_BOT_COUNT;

  /**
   * 获取桌面设备列表
   */
  const getDesktopDevices = useCallback(async (): Promise<DesktopDevice[]> => {
    try {
      const response = await BotController.getDesktopDevices(
        { page: 1, page_size: 100 },
        { skipErrorHandler: true },
      );
      if (response && response.items) {
        return response.items;
      }
      return [];
    } catch (error) {
      console.error('[useBot] 获取桌面设备列表失败:', error);
      return [];
    }
  }, []);

  /**
   * 获取设备目录树
   */
  const listDeviceFiles = useCallback(
    async (
      machineId: string,
    ): Promise<{ absolutePath: string; dirs: DeviceFileNode } | null> => {
      try {
        const response = await BotController.listDeviceFiles(machineId, {
          skipErrorHandler: true,
        });
        if (response.success) {
          return {
            absolutePath: response.data.absolute_path || '',
            dirs: response.data.dirs,
          };
        }
        return null;
      } catch (error) {
        console.error('[useBot] 获取设备目录列表失败:', error);
        return null;
      }
    },
    [],
  );

  /**
   * 创建桌面 Bot（复用云端 Bot 的授权流程：先授权再创建）
   */
  const createDesktopBot = useCallback(
    async (params: {
      botName: string;
      botDesc?: string;
      machineId: string;
      mountPath?: string;
      engineType?: EngineType;
    }): Promise<CreateBotResult> => {
      if (!userId) {
        toast.error('用户信息缺失');
        return { type: 'error', message: '用户信息缺失' };
      }

      // 桌面 Bot 不计算在数量限制内

      try {
        const response = await BotController.createDesktopBot(
          {
            bot_name: params.botName,
            bot_desc: params.botDesc,
            machine_id: params.machineId,
            mount_path: params.mountPath,
            engine_type: params.engineType,
          },
          { skipErrorHandler: true },
        );

        // 处理需要授权的场景（与云端 Bot 一致）
        if (
          (response as any).error_code === 401 &&
          (response as any).data &&
          ('need_authorization' in (response as any).data ||
            'needs_authorization' in (response as any).data)
        ) {
          const authData = (response as any).data;
          return {
            type: 'auth_required',
            botId: authData.bot_id,
            iframeUrl: authData.iframe_url,
            redirectUrl: authData.redirect_url,
          };
        }

        if (response.success && response.data) {
          toast.success('桌面 Bot 创建成功，正在等待激活...');

          // 刷新 Bot 列表（强制刷新，确保新创建的 Bot 出现在列表中）
          await loadBots({ force: true });

          // 查找新创建的 Bot 并启动轮询
          const newBot = useBotStore
            .getState()
            .bots.find((b: Bot) => b.bot_id === response.data!.bot_id);
          if (newBot && newBot.status !== BOT_STATUS.ACTIVE) {
            const newBotId = newBot.bot_id;
            pollingBotIds.add(newBotId);
            pollBotStatus(newBotId, 'create').finally(() => {
              pollingBotIds.delete(newBotId);
            });
          }

          return { type: 'success', bot: newBot! };
        }

        const errorMsg = response?.message || '创建桌面 Bot 失败';
        toast.error(errorMsg);
        return { type: 'error', message: errorMsg };
      } catch (error: any) {
        console.error('[useBot] 创建桌面 Bot 失败:', error);
        handleNetworkError(error, {
          module: 'Bot',
          action: '创建桌面 Bot',
        });
        return { type: 'error', message: '创建桌面 Bot 失败' };
      }
    },
    [userId, loadBots, pollBotStatus],
  );

  /**
   * 重启桌面 Bot
   */
  const restartDesktopBot = useCallback(
    async (botId: string): Promise<boolean> => {
      if (!userId) {
        toast.error('用户信息缺失');
        return false;
      }

      try {
        const response = await BotController.restartDesktopBot(botId, {
          skipErrorHandler: true,
        });

        if (response.success) {
          updateBot(botId, { status: BOT_STATUS.PENDING });
          toast.info('桌面 Bot 正在重启，请稍候...');

          pollingBotIds.add(botId);
          const didActivate = await pollBotStatus(botId, 'restart', {
            skipSyncMcps: true,
          });
          pollingBotIds.delete(botId);

          if (didActivate) {
            showSuccessToast(
              { module: 'Bot', action: '重启桌面 Bot' },
              '桌面 Bot 重启成功，页面即将刷新',
            );
            setTimeout(() => window.location.reload(), 1500);
          }
          return didActivate;
        }

        const errorMsg = response?.data?.status || '重启桌面 Bot 失败';
        toast.error(errorMsg);
        return false;
      } catch (error: any) {
        const errorMsg =
          error?.data?.message || error?.message || '重启桌面 Bot 失败';
        console.error('[useBot] 重启桌面 Bot 失败:', error);
        toast.error(errorMsg);
        return false;
      }
    },
    [userId, updateBot, pollBotStatus],
  );

  /**
   * 删除桌面 Bot
   * 删除后的刷新逻辑与云端 Bot 一致：
   * - 如果是当前激活的 Bot：查找备选 Bot 自动切换，无备选则刷新页面
   * - 如果不是当前激活的 Bot：仅从列表中移除，由调用方刷新列表
   */
  const deleteDesktopBot = useCallback(
    async (
      botId: string,
      options?: {
        onSwitchBot?: (targetBot: Bot) => Promise<boolean>;
      },
    ): Promise<boolean> => {
      if (!userId) {
        toast.error('用户信息缺失');
        return false;
      }

      // 停止轮询
      if (pollingBotIds.has(botId)) {
        pollingBotIds.delete(botId);
        pollingGenerations.set(botId, (pollingGenerations.get(botId) || 0) + 1);
      }

      const isActiveBot = useBotStore.getState().activeBotId === botId;
      if (isActiveBot) {
        useBotStore.getState().setDeletingActiveBot(true);
      }

      try {
        const response = await BotController.deleteDesktopBot(botId, {
          skipErrorHandler: true,
        });

        if (response.success) {
          // 查找备选 Bot（用于自动切换）
          const fallbackBot = isActiveBot
            ? findFallbackBot(useBotStore.getState().bots, botId)
            : null;

          // 如果是当前激活的 Bot，先重置连接（防止后续请求路由到已删除的 Bot）
          if (isActiveBot) {
            useConnectionStore.getState().reset();
          }

          removeBot(botId);

          // 清理该 Bot 的技能集展开状态
          removeSkillSetExpands(botId);

          if (isActiveBot) {
            if (fallbackBot && isValidBotId(fallbackBot.bot_id)) {
              // 有备选 Bot，自动切换
              toast.info(
                `当前 Bot 已删除，自动切换到 "${fallbackBot.bot_name}"`,
              );
              const switchFn = options?.onSwitchBot || switchBot;
              const switched = await switchFn(fallbackBot);
              return switched;
            } else {
              // 没有可切换的备选 Bot，刷新页面重新初始化
              useConnectionStore.getState().reset();
              toast.info('当前 Bot 已删除，页面将刷新以重新初始化');
              window.location.reload();
            }
          } else {
            showSuccessToast(
              { module: 'Bot', action: '删除 Bot' },
              '桌面 Bot 已删除',
            );
          }
          return true;
        }

        if (isActiveBot) {
          useBotStore.getState().setDeletingActiveBot(false);
        }
        const errorMsg = '删除桌面 Bot 失败';
        toast.error(errorMsg);
        return false;
      } catch (error: any) {
        if (isActiveBot) {
          useBotStore.getState().setDeletingActiveBot(false);
        }
        const errorMsg =
          error?.data?.message || error?.message || '删除桌面 Bot 失败';
        console.error('[useBot] 删除桌面 Bot 失败:', error);
        toast.error(errorMsg);
        return false;
      }
    },
    [userId, removeBot, removeSkillSetExpands, switchBot],
  );

  return {
    // State
    bots,
    activeBotId,
    isLoading,
    pendingBots,
    isMaxBotCount,
    maxBotCount,
    loadBotsCeiling,
    totalBotCount,

    // 所有 Bot（personal + service）
    allBots,
    isLoadingAllBots,

    // Computed
    activeBot: getActiveBotById(),

    // Actions
    loadBots,
    loadAllBots,
    createBot,
    editBot,
    switchBot,
    restartBot,
    resetBot,
    deleteBot,
    getBotDetail,
    checkBotNameExists,
    searchBots,
    setBotPublic,
    queryAgentPassport,
    getBotAuthStatus,
    pollBotStatus,
    triggerDataInit,

    // 桌面设备相关
    getDesktopDevices,
    createDesktopBot,
    getDesktopBotAuthStatus,
    restartDesktopBot,
    deleteDesktopBot,
    listDeviceFiles,

    // Helpers
    getBotById,

    // 刷新 Bot 总数
    refreshBotCount: fetchTotalBotCount,
  };
}

/**
 * 桌面 Bot 状态检查轮询 Hook
 * 当用户拥有桌面 Bot 时，每 30s 调用 /api/desktop/bots/status-check
 * 只发送请求，不处理返回，跳过异常
 * 监听 store 中 bots 变化，自动启停轮询
 */
export function useDesktopBotStatusPolling() {
  const hasDesktopBot = useBotStore((s) =>
    s.bots.some((b: Bot) => b.bot_type === 'desktop'),
  );

  useEffect(() => {
    if (!hasDesktopBot) return;

    const poll = () => {
      BotController.checkDesktopBotsStatus({
        skipErrorHandler: true,
      }).catch(() => {
        // 跳过异常
      });
    };

    // 立即执行一次
    poll();

    const timer = setInterval(poll, 30_000);
    return () => clearInterval(timer);
  }, [hasDesktopBot]);
}
