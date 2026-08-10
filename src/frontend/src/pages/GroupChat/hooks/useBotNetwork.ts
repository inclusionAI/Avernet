/**
 * useBotNetwork - Bot 协作网络管理 Hook
 *
 * 遵循三层架构：Hook层封装业务逻辑、API调用、错误处理和Toast
 *
 * 支持两种数据传入方式：
 * 1. 外部传入 bots（通过 initBotTabs 方法）
 * 2. 从 BCN 接口加载（通过 loadMyBots 方法，兼容旧逻辑）
 */

import { useActor } from '@/hooks/useActor';
import type { MyBotInfo } from '@/services/backend-api/BcnController';
import * as BcnController from '@/services/backend-api/BcnController';
import { getUsersByIds } from '@/services/backend-api/UserController';
import type { ActorBot } from '@/stores/actorStore';
import {
  computeIsOnline,
  useBotNetworkStore,
  type BotNetworkInfo,
  type BotVisibility,
  type DiscoverBotInfo,
  type DriverBot,
} from '@/stores/botNetworkStore';
import { useBotStore, type Bot } from '@/stores/botStore';
import { useUserStore } from '@/stores/userStore';
import { getBotRecommendTracker } from '@/utils/botRecommendTracker';
import { handleNetworkError } from '@/utils/hooksErrorHandler';
import { retryOnTransient } from '@/utils/retryRequest';
import { useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { getBcnActiveOnlyPreference } from '../constants';
import type { BotTabItem } from '../types';
/** sessionStorage key — 每个标签页独立，避免多标签页互相覆盖 */
const SELECTED_BOT_UUID_KEY = 'oc_groupchat_selected_bot_uuid';

const LOCAL_CLAUDE_ROLE_NAME = /^Claude (Planner|Developer|Reviewer)(（当前）)?$/;
const LOCAL_CURRENT_CLAUDE_ROLE_NAME = /^Claude (Planner|Developer|Reviewer)（当前）$/;

function filterStaleLocalClaudeRoles<T>(
  bots: T[],
  getName: (bot: T) => string | null | undefined,
): T[] {
  const isLocalSinglebox =
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1');
  const hasCurrentRole = bots.some((bot) =>
    LOCAL_CURRENT_CLAUDE_ROLE_NAME.test(getName(bot) || ''),
  );

  if (!isLocalSinglebox && !hasCurrentRole) return bots;

  return bots.filter((bot) => {
    const name = getName(bot) || '';
    return !LOCAL_CLAUDE_ROLE_NAME.test(name) ||
      LOCAL_CURRENT_CLAUDE_ROLE_NAME.test(name);
  });
}

// === 统一 Bot 数据合并辅助函数 ===

/** initUnifiedBotTabs 参数 */
export interface InitUnifiedBotTabsParams {
  /** 本地 Bot Store / externalBots 转换后的列表 */
  localBots?: BotTabItem[];
  /** URL 参数中的 bot_uuid，用于预选 driverBot */
  targetBotUuid?: string;
  /** 是否仅加载活跃 Bot（覆盖 localStorage 偏好） */
  activeOnly?: boolean;
}

/**
 * BCN 闭包：将 MyBotInfo[] 映射为 BotTabItem[]
 * BCN /bots/my 返回的 bot 天然满足 canJoinBcn，无需 ACTIVE/canJoinBcn 过滤。
 * 排除 human actor（由 Phase 3 单独处理）。
 */
function convertBcnBotsToTabItems(bcnBots: MyBotInfo[]): BotTabItem[] {
  return bcnBots
    .filter((b) => b.actor_kind !== 'human')
    .map((b) => ({
      bot_uuid: b.bot_uuid,
      bot_name: b.capabilities?.name || b.bot_uuid,
      avatar_url: b.avatar_url ?? undefined,
      summary: b.capabilities?.summary ?? undefined,
    }));
}

/**
 * BCN 闭包：将 MyBotInfo 转换为最小 Bot 对象，用于反写 botStore。
 * MyBotInfo 缺少 engine_types/template_type/bot_type 等，用安全默认值填充。
 * BCN 一期仅消费 owner_id + bot_type + status，默认值足够。
 */
function myBotInfoToBot(bcnBot: MyBotInfo, userId: string): Bot {
  return {
    bot_id: bcnBot.bot_id || bcnBot.bot_uuid.split(':')[0],
    bot_name: bcnBot.capabilities?.name || bcnBot.bot_uuid,
    bot_desc: bcnBot.capabilities?.summary ?? null,
    entity_id: bcnBot.entity_id || bcnBot.bot_uuid.split(':')[1] || '',
    entity_type: 'staff',
    creator_id: userId,
    owner_id: userId,
    bot_type: 'personal',
    status: bcnBot.is_online !== false ? 'ACTIVE' : 'OFFLINE',
    active_engine: 'openclaw',
    engine_types: ['openclaw'],
    binding_id: null,
    gmt_create: '',
    gmt_modified: '',
    is_delete: 0,
    ...(bcnBot.avatar_url
      ? {
          avatar_url: bcnBot.avatar_url,
          ext: { avatar_url: bcnBot.avatar_url },
        }
      : {}),
  };
}

/**
 * 将 local BotTabItem 与 BCN MyBotInfo 合并为 BotNetworkInfo
 */
function mergeBotFromLocal(
  localBot: BotTabItem,
  bcnBot?: MyBotInfo,
): BotNetworkInfo {
  const isOnboarded = !!bcnBot;

  // BCN 名称与本地名称不一致时标记需要刷新
  const bcnName = bcnBot?.capabilities?.name;
  const needsRefresh =
    isOnboarded && !!bcnName && bcnName !== localBot.bot_name;

  return {
    bot_uuid: localBot.bot_uuid,
    bot_name: localBot.bot_name,
    summary: localBot.summary ?? bcnBot?.capabilities?.summary ?? undefined,
    avatar_url: localBot.avatar_url ?? bcnBot?.avatar_url,
    visibility: isOnboarded ? bcnBot!.visibility ?? 'public' : 'offline',
    is_online: isOnboarded
      ? computeIsOnline({
          status: bcnBot!.status,
          visibility: bcnBot!.visibility,
          dynamic_status: bcnBot!.dynamic_status,
        })
      : false,
    status: isOnboarded ? bcnBot!.status : undefined,
    dynamic_status: isOnboarded ? bcnBot!.dynamic_status : undefined,
    capabilities: isOnboarded
      ? bcnBot!.capabilities
      : {
          name: localBot.bot_name,
          summary: localBot.summary ?? null,
          domains: [],
          skills: [],
          scopes: [],
        },
    actor_kind:
      bcnBot?.actor_kind ||
      (bcnBot?.bot_uuid?.startsWith('human_') ? 'human' : 'bot'),
    needsRefresh,
    externalName: needsRefresh ? localBot.bot_name : undefined,
  };
}

/**
 * 将纯 BCN MyBotInfo（不在 local 中的）转换为 BotNetworkInfo
 */
function resolveBcnBotDisplayName(
  bcnBot: MyBotInfo,
  actorKind: 'human' | 'bot',
): string {
  if (bcnBot.capabilities?.name) return bcnBot.capabilities.name;

  if (actorKind === 'human') {
    const { nickName, userId } = useUserStore.getState();
    return nickName || userId || bcnBot.bot_uuid;
  }

  return bcnBot.bot_uuid;
}

function mergeBotFromBcnOnly(bcnBot: MyBotInfo): BotNetworkInfo {
  const actorKind: 'human' | 'bot' =
    bcnBot.actor_kind ||
    (bcnBot.bot_uuid?.startsWith('human_') ? 'human' : 'bot');

  const needsHumanInit =
    actorKind === 'human' && bcnBot.capabilities?.summary === null;

  return {
    bot_uuid: bcnBot.bot_uuid,
    bot_name: resolveBcnBotDisplayName(bcnBot, actorKind),
    summary: bcnBot.capabilities?.summary ?? undefined,
    avatar_url: bcnBot.avatar_url,
    visibility: bcnBot.visibility ?? 'public',
    is_online: computeIsOnline({
      status: bcnBot.status,
      visibility: bcnBot.visibility,
      dynamic_status: bcnBot.dynamic_status,
    }),
    status: bcnBot.status,
    dynamic_status: bcnBot.dynamic_status,
    capabilities: bcnBot.capabilities,
    actor_kind: actorKind,
    needsHumanInit,
  };
}

/**
 * 排序：在线 Bot → offline Bot → Human 最后
 */
function sortUnifiedBots(bots: BotNetworkInfo[]): BotNetworkInfo[] {
  return [...bots].sort((a, b) => {
    // Human actor 始终排最后
    const aIsHuman = a.actor_kind === 'human' ? 1 : 0;
    const bIsHuman = b.actor_kind === 'human' ? 1 : 0;
    if (aIsHuman !== bIsHuman) return aIsHuman - bIsHuman;
    // 在线 bot 排前面，offline 排后面
    const aOffline = a.visibility === 'offline' ? 1 : 0;
    const bOffline = b.visibility === 'offline' ? 1 : 0;
    return aOffline - bOffline;
  });
}

export function useBotNetwork() {
  const {
    myBots,
    isLoadingMyBots,
    driverBot,
    discoveredBots,
    isLoadingDiscover,
    discoverPagination,
    recommendedBots,
    isLoadingRecommended,
    recommendedPagination,
    setMyBots,
    updateMyBot,
    setDriverBot,
    setDiscoveredBots,
    appendDiscoveredBots,
    updateDiscoverPagination,
    setRecommendedBots,
    appendRecommendedBots,
    updateRecommendedPagination,
    setLoadingMyBots,
    setLoadingDiscover,
    setLoadingRecommended,
    getMyBotByUuid,
    reset,
  } = useBotNetworkStore();

  // Actor hook（用于替换 discoverBots）
  const { loadActors, searchActors, setCooperatableOnly } = useActor();

  // 埋点 tracker
  const trackerRef = useRef(getBotRecommendTracker());

  /**
   * 统一初始化 Bot Tabs
   *
   * 合并两个数据源：
   * 1. localBots — 本地 Bot Store / externalBots 转换后的列表（已过滤 ACTIVE + canJoinBcn）
   * 2. BCN /bots/my — 用户在协作网络中注册的全部 Bot
   *
   * 去重规则：以 bot_uuid 为 key，local 优先（名称/头像），BCN 补充状态信息
   * 排序规则：offline 优先 → 在线 Bot → Human 最后
   */
  const initUnifiedBotTabs = useCallback(
    async (
      params: InitUnifiedBotTabsParams = {},
    ): Promise<BotNetworkInfo[]> => {
      const {
        localBots = [],
        targetBotUuid,
        activeOnly: activeOnlyOverride,
      } = params;

      try {
        setLoadingMyBots(true);

        const activeOnly = activeOnlyOverride ?? getBcnActiveOnlyPreference();
        const userId = useUserStore.getState().userId;
        const humanUuid = userId ? `human_${userId}` : null;

        console.log('[useBotNetwork] initUnifiedBotTabs:', {
          localBotsCount: localBots.length,
          targetBotUuid,
          activeOnly,
          humanUuid,
        });

        // 并行请求：BCN /bots/my + Human Actor query + 用户信息
        const [myBotsRes, humanQueryRes, usersRes] = await Promise.all([
          // 网关瞬时 504 重试（进页面并发拉取，间歇超时重试通常即成功）
          retryOnTransient(
            () => BcnController.getMyBots({ active_only: activeOnly }),
            { retries: 1 },
          ).catch((err) => {
            console.warn('[useBotNetwork] getMyBots failed, degrading:', err);
            return null;
          }),
          humanUuid
            ? BcnController.queryBots({ bot_uuids: [humanUuid] }).catch(
                () => null,
              )
            : null,
          userId ? getUsersByIds([userId]).catch(() => undefined) : undefined,
        ]);

        const bcnItems = filterStaleLocalClaudeRoles(
          myBotsRes?.items || [],
          (bot) => bot.capabilities?.name,
        );
        const humanQueryData = humanQueryRes?.[0];
        const userInfoRes = usersRes?.[0];

        // Phase 0.5：如果未传 localBots，从 BCN 数据构造 BotTabItem[]
        // BCN 闭包：GroupChat 不再调 loadBots（/api），initUnifiedBotTabs 完全由
        // /bcnproxy/bots/my 驱动。localBots 为空时用 bcnItems 生成，走同样的合并逻辑。
        let effectiveLocalBots = filterStaleLocalClaudeRoles(
          localBots,
          (bot) => bot.bot_name,
        );
        if (effectiveLocalBots.length === 0 && bcnItems.length > 0) {
          effectiveLocalBots = convertBcnBotsToTabItems(bcnItems);
        }

        // 构建 BCN bot_uuid → MyBotInfo 映射
        const bcnMap = new Map<string, MyBotInfo>();
        bcnItems.forEach((item) => bcnMap.set(item.bot_uuid, item));

        // activeOnly 模式下，getMyBots 只返回活跃 bot，未命中的 local bot 会被误判为未入网
        // 用 queryBots 补查真实 BCN 状态
        if (activeOnly && effectiveLocalBots.length > 0) {
          const unmatchedUuids = effectiveLocalBots
            .filter((bot) => !bcnMap.has(bot.bot_uuid))
            .map((bot) => bot.bot_uuid);

          if (unmatchedUuids.length > 0) {
            try {
              const queryRes = await BcnController.queryBots({
                bot_uuids: unmatchedUuids,
              });
              if (queryRes) {
                for (const qBot of queryRes) {
                  bcnMap.set(qBot.bot_uuid, {
                    bot_uuid: qBot.bot_uuid,
                    bot_id: qBot.bot_uuid.split(':')[0] || qBot.bot_uuid,
                    entity_id: qBot.bot_uuid.split(':')[1] || '',
                    capabilities: {
                      name: qBot.capabilities?.name ?? null,
                      summary: qBot.capabilities?.summary ?? null,
                      domains: qBot.capabilities?.domains || [],
                      skills: qBot.capabilities?.skills || [],
                      scopes: qBot.capabilities?.scopes || [],
                    },
                    visibility: qBot.visibility,
                    status: qBot.status,
                    dynamic_status: qBot.dynamic_status,
                  });
                }
                console.log(
                  '[useBotNetwork] queryBots corrected',
                  queryRes.length,
                  'unmatched bots',
                );
              }
            } catch (err) {
              console.warn(
                '[useBotNetwork] queryBots for unmatched bots failed:',
                err,
              );
            }
          }
        }

        const seenUuids = new Set<string>();
        const mergedBots: BotNetworkInfo[] = [];

        // Phase 1：处理 local bots
        for (const localBot of effectiveLocalBots) {
          const bcnBot = bcnMap.get(localBot.bot_uuid);
          mergedBots.push(mergeBotFromLocal(localBot, bcnBot));
          seenUuids.add(localBot.bot_uuid);
        }

        // Phase 2：添加 BCN-only bots（不在 local 中的）
        for (const bcnBot of bcnItems) {
          if (!seenUuids.has(bcnBot.bot_uuid)) {
            mergedBots.push(mergeBotFromBcnOnly(bcnBot));
            seenUuids.add(bcnBot.bot_uuid);
          }
        }

        // Phase 3：Human Actor 处理
        if (humanUuid) {
          const humanAlreadyInList = mergedBots.some(
            (b) => b.bot_uuid === humanUuid,
          );

          if (!humanAlreadyInList) {
            // 尝试从 queryBots 结果获取 Human Actor 数据
            const humanBcnData = humanQueryData;

            // 构建 user_info
            const userInfo = userInfoRes
              ? {
                  user_id: userInfoRes.user_id,
                  nickName: userInfoRes.nickName,
                  realName: userInfoRes.realName,
                  avatarUrl: undefined,
                }
              : undefined;

            if (humanBcnData) {
              // Human Actor 已在 BCN 注册
              const displayName =
                userInfo?.nickName ||
                humanBcnData.capabilities?.name ||
                userId ||
                humanUuid;
              const needsHumanInit =
                humanBcnData.capabilities?.summary === null;

              mergedBots.push({
                bot_uuid: humanUuid,
                bot_name: displayName,
                summary: humanBcnData.capabilities?.summary ?? undefined,
                avatar_url: undefined,
                visibility: humanBcnData.visibility ?? 'public',
                is_online: computeIsOnline({
                  status: humanBcnData.status,
                  visibility: humanBcnData.visibility,
                  dynamic_status: humanBcnData.dynamic_status,
                }),
                capabilities: humanBcnData.capabilities,
                actor_kind: 'human',
                status: humanBcnData.status,
                dynamic_status: humanBcnData.dynamic_status,
                user_info: userInfo,
                needsHumanInit,
              });
              console.log(
                '[useBotNetwork] Added human actor from queryBots:',
                humanUuid,
                displayName,
                needsHumanInit ? '(needs init)' : '',
              );
            } else {
              // Human Actor 未注册，添加占位
              console.log(
                '[useBotNetwork] Human actor not in BCN, adding placeholder:',
                humanUuid,
              );
              const displayName = userInfo?.nickName || userId || humanUuid;
              mergedBots.push({
                bot_uuid: humanUuid,
                bot_name: displayName,
                summary: undefined,
                avatar_url: undefined,
                visibility: 'offline',
                is_online: false,
                status: 'hidden',
                capabilities: {
                  name: displayName,
                  summary: null,
                  domains: [],
                  skills: [],
                  scopes: [],
                },
                actor_kind: 'human',
                user_info: userInfo,
                needsHumanInit: true,
              });
            }
          } else {
            // Human Actor 已在列表中（来自 /bots/my），补充 user_info + needsHumanInit
            const humanIdx = mergedBots.findIndex(
              (b) => b.bot_uuid === humanUuid,
            );
            if (humanIdx >= 0) {
              const existing = mergedBots[humanIdx];
              const needsHumanInit =
                existing.needsHumanInit ??
                existing.capabilities?.summary === null;
              mergedBots[humanIdx] = {
                ...existing,
                needsHumanInit,
                ...(userInfoRes
                  ? {
                      user_info: {
                        user_id: userInfoRes.user_id,
                        nickName: userInfoRes.nickName,
                        realName: userInfoRes.realName,
                        avatarUrl: undefined,
                      },
                    }
                  : {}),
              };
            }
          }
        }

        // Phase 4：activeOnly 模式下过滤 BCN 确认不活跃的 bot
        // bot 类型看 dynamic_status.status，human 类型看 status
        const filteredBots = activeOnly
          ? mergedBots.filter((bot) => {
              if (bot.visibility === 'offline') return true; // 未入网的保留，供用户入网操作
              if (bot.actor_kind === 'human') {
                return bot.status !== 'offline';
              }
              return bot.dynamic_status?.status !== 'offline';
            })
          : mergedBots;

        // Phase 5：排序
        const sorted = sortUnifiedBots(filteredBots);

        // Phase 6：写入 store
        setMyBots(sorted);

        // Phase 6.5：反写 botStore（BCN 闭包）
        // BCN 页面不再调 loadBots（/api），由 initUnifiedBotTabs 完全驱动。
        // botStore 的 bots/allBots 仍被 currentBotDetail / resolveBotOwnerId 消费，
        // 从 bcnItems 构造最小 Bot 对象反写，owner_id 统一为当前用户。
        if (effectiveLocalBots.length > 0 || bcnItems.length > 0) {
          const storeBots = bcnItems
            .filter((b) => b.actor_kind !== 'human')
            .map((b) => myBotInfoToBot(b, userId || ''));
          useBotStore.getState().setBots(storeBots);
          useBotStore.getState().setAllBots(storeBots);
          console.log(
            '[useBotNetwork] backfilled botStore from bcnItems:',
            storeBots.length,
            'bots',
          );
        }

        // Phase 7：选择 driverBot
        let selectedBot: BotNetworkInfo | undefined;

        if (targetBotUuid) {
          selectedBot = sorted.find((b) => b.bot_uuid === targetBotUuid);
        }

        if (!selectedBot) {
          const savedBotUuid = sessionStorage.getItem(SELECTED_BOT_UUID_KEY);
          if (savedBotUuid && sorted.some((b) => b.bot_uuid === savedBotUuid)) {
            selectedBot = sorted.find((b) => b.bot_uuid === savedBotUuid);
          }
        }

        if (!selectedBot && sorted.length > 0) {
          selectedBot =
            sorted.find((b) => b.actor_kind !== 'human') || sorted[0];
        }

        if (selectedBot) {
          setDriverBot({
            bot_uuid: selectedBot.bot_uuid,
            bot_name: selectedBot.bot_name,
            visibility: selectedBot.visibility,
            status: selectedBot.status,
            dynamic_status: selectedBot.dynamic_status,
            is_online: selectedBot.is_online,
          });
          sessionStorage.setItem(SELECTED_BOT_UUID_KEY, selectedBot.bot_uuid);
        } else {
          setDriverBot(null);
          sessionStorage.removeItem(SELECTED_BOT_UUID_KEY);
        }

        console.log(
          '[useBotNetwork] initUnifiedBotTabs done:',
          sorted.length,
          'bots',
        );
        return sorted;
      } catch (error: any) {
        console.error(
          '[useBotNetwork] Failed to init unified bot tabs:',
          error,
        );
        handleNetworkError(error, {
          module: 'BotNetwork',
          action: '初始化 Bot 列表',
        });
        return [];
      } finally {
        setLoadingMyBots(false);
      }
    },
    [setLoadingMyBots, setMyBots, setDriverBot],
  );

  // 旧方法改为 wrapper，保持向后兼容（移除不再需要的 userIdParam 参数）
  const initBotTabs = useCallback(
    async (externalBots: BotTabItem[]): Promise<BotNetworkInfo[]> => {
      return initUnifiedBotTabs({ localBots: externalBots });
    },
    [initUnifiedBotTabs],
  );

  const initFromBcnMyBots = useCallback(
    async (
      targetBotUuid?: string,
      userIdParam?: string,
      activeOnlyParam?: boolean,
    ): Promise<BotNetworkInfo[]> => {
      void userIdParam; // 保持签名兼容，实际不再使用
      return initUnifiedBotTabs({
        targetBotUuid,
        activeOnly: activeOnlyParam,
      });
    },
    [initUnifiedBotTabs],
  );

  const initFromUrl = useCallback(
    async (botUuid: string): Promise<BotNetworkInfo | null> => {
      const bots = await initUnifiedBotTabs({ targetBotUuid: botUuid });
      return bots.find((b) => b.bot_uuid === botUuid) || null;
    },
    [initUnifiedBotTabs],
  );

  /**
   * 设置驱动 Bot（并保存到 sessionStorage）
   */
  const selectDriverBot = useCallback(
    (bot: DriverBot | null) => {
      setDriverBot(bot);
      if (bot) {
        sessionStorage.setItem(SELECTED_BOT_UUID_KEY, bot.bot_uuid);
      } else {
        sessionStorage.removeItem(SELECTED_BOT_UUID_KEY);
      }
    },
    [setDriverBot],
  );

  /**
   * 设置 Bot 可见性
   */
  const setBotVisibility = useCallback(
    async (botUuid: string, visibility: BotVisibility): Promise<boolean> => {
      try {
        const response = await BcnController.setBotVisibility({
          bot_uuid: botUuid,
          visibility,
        });

        if (response.error) {
          throw new Error(response.error);
        }

        updateMyBot(botUuid, { visibility });

        // 如果更新的是当前 driverBot，同步更新 driverBot 状态
        // 使用 getState() 获取最新的 driverBot，避免闭包引用问题
        const currentDriverBot = useBotNetworkStore.getState().driverBot;
        if (botUuid === currentDriverBot?.bot_uuid) {
          setDriverBot({ ...currentDriverBot, visibility });
        }

        const visibilityLabels: Record<BotVisibility, string> = {
          public: '其他成员可直接添加你为好友',
          protected: '需经你手动审核同意才可成为好友',
          private: '不允许被添加为好友',
        };
        toast.success(`Bot 已设为：${visibilityLabels[visibility]}`);
        return true;
      } catch (error: any) {
        console.error('[useBotNetwork] Failed to set visibility:', error);
        handleNetworkError(error, {
          module: 'BotNetwork',
          action: '设置可见性',
        });
        return false;
      }
    },
    [updateMyBot, setDriverBot],
  );

  /**
   * 发现 Bot（可协作的 Bot 列表）
   * 使用 Actor API 替换原有的 discoverBots
   * - 有 q 参数时：调用 searchActors（智能搜索）
   * - 无 q 参数时：调用 loadActors（普通列表）
   * 此方法目前没有被使用
   */
  const discoverBots = useCallback(
    async (
      params: {
        q?: string;
        visibility?: BotVisibility;
        collaborate_bot?: string;
        limit?: number;
        offset?: number;
        append?: boolean;
      } = {},
    ): Promise<DiscoverBotInfo[]> => {
      const requestStartTime = Date.now();

      // 获取当前 driverBot 的 UUID 作为 currentBotUuid
      const currentBotUuid = params.collaborate_bot || driverBot?.bot_uuid;
      if (!currentBotUuid) {
        console.warn('[useBotNetwork] No current bot UUID available');
        return [];
      }

      try {
        setLoadingDiscover(true);

        let responseBots: Array<{
          bot_uuid: string;
          bot_name: string;
          summary?: string;
          avatar_url?: string;
          visibility?: string;
          is_online?: boolean;
          capabilities?: any;
          is_friend?: boolean;
        }> = [];
        let total = 0;
        let traceId = '';
        let resultType = 'recommend';

        if (params.q) {
          // 场景 2-3：智能搜索（语义搜索）
          resultType = 'recommend';
          const searchResponse = await searchActors({
            currentBotUuid,
            cooperatableOnly: true,
            q: params.q,
          });
          responseBots = searchResponse.bots || [];
          total = searchResponse.bots?.length || 0;
          traceId = searchResponse.context?.recommend_response?.trace_id || '';

          // query_result 埋点：上报发现结果
          // trace_id 来源: response.data?.context?.recommend_response?.trace_id || response.data?.trace_id

          const latencyMs = Date.now() - requestStartTime;
          trackerRef.current.onQueryResult({
            trace_id: traceId,
            type: 'recommend',
            keyword: params.q,
            bot_count: responseBots.length,
            latency_ms: latencyMs,
            bots: responseBots.map((b) => ({ bot_id: b.bot_uuid })),
          });
        } else {
          // 场景 2-2：公开 Bot 搜索（cooperatable_only=true）
          const pageNo =
            Math.floor((params.offset ?? 0) / (params.limit ?? 20)) + 1;
          const listResponse = await loadActors({
            currentBotUuid,
            cooperatableOnly: true,
            pageNo,
            pageSize: params.limit ?? 20,
            name: undefined,
            append: params.append,
          });
          responseBots = listResponse.bots || [];
          total = listResponse.total || 0;
          traceId = '';
        }

        const bots: DiscoverBotInfo[] = responseBots.map((bot: ActorBot) => ({
          bot_uuid: bot.bot_uuid,
          bot_name: bot.bot_name || bot.bot_uuid,
          summary: bot.summary ?? undefined,
          visibility: (bot.visibility as BotVisibility) ?? 'public',
          is_online: computeIsOnline({
            status: bot.status,
            visibility: (bot.visibility as BotVisibility) ?? 'public',
            dynamic_status: bot.dynamic_status,
          }),
          capabilities: bot.capabilities,
          is_friend: bot.is_friend,
          trace_id: traceId,
          type: resultType,
          dynamic_status: bot.dynamic_status,
        }));

        if (params.append) {
          appendDiscoveredBots(bots);
        } else {
          setDiscoveredBots(bots);
        }

        updateDiscoverPagination({
          total: total,
          offset: params.offset ?? 0,
        });

        return bots;
      } catch (error: any) {
        console.error('[useBotNetwork] Failed to discover bots:', error);
        handleNetworkError(error, { module: 'BotNetwork', action: '发现 Bot' });
        return [];
      } finally {
        setLoadingDiscover(false);
      }
    },
    [
      driverBot,
      loadActors,
      searchActors,
      setLoadingDiscover,
      setDiscoveredBots,
      appendDiscoveredBots,
      updateDiscoverPagination,
    ],
  );

  /**
   * 加载更多发现 Bot
   */
  const loadMoreDiscoveredBots = useCallback(async (): Promise<
    DiscoverBotInfo[]
  > => {
    const nextOffset = discoverPagination.offset + discoverPagination.limit;
    if (nextOffset >= discoverPagination.total) return [];

    return discoverBots({
      offset: nextOffset,
      append: true,
    });
  }, [discoverPagination, discoverBots]);

  /**
   * 加载推荐好友 Bot（Add Friend 场景）
   * 使用 Actor API 替换，cooperatable_only=false
   */
  const loadRecommendedBots = useCallback(
    async (
      params: {
        name?: string;
        visibility?: BotVisibility;
        limit?: number;
        offset?: number;
        append?: boolean;
      } = {},
    ): Promise<DiscoverBotInfo[]> => {
      // 获取当前 driverBot 的 UUID 作为 currentBotUuid
      const currentBotUuid = driverBot?.bot_uuid;
      if (!currentBotUuid) {
        console.warn('[useBotNetwork] No current bot UUID available');
        return [];
      }

      try {
        setLoadingRecommended(true);

        // 场景 2-1：添加好友（cooperatable_only=false）
        const pageNo =
          Math.floor((params.offset ?? 0) / (params.limit ?? 20)) + 1;

        setCooperatableOnly(false);
        const response = await loadActors({
          currentBotUuid,
          cooperatableOnly: false,
          pageNo,
          pageSize: params.limit ?? 20,
          name: params.name,
          append: params.append,
        });
        const responseBots = response.bots || [];
        const bots: DiscoverBotInfo[] = responseBots.map((bot: ActorBot) => ({
          bot_uuid: bot.bot_uuid,
          bot_name: bot.bot_name || bot?.capabilities?.name || bot.bot_uuid,
          summary: bot.summary ?? undefined,
          visibility: (bot.visibility as BotVisibility) ?? 'public',
          is_online: computeIsOnline({
            status: bot.status,
            visibility: (bot.visibility as BotVisibility) ?? 'public',
            dynamic_status: bot.dynamic_status,
          }),
          capabilities: bot.capabilities,
          is_friend: bot.is_friend,
          dynamic_status: bot.dynamic_status,
        }));

        if (params.append) {
          appendRecommendedBots(bots);
        } else {
          setRecommendedBots(bots);
        }

        updateRecommendedPagination({
          total: bots.length, // Actor API 返回的是分页结果，简化处理
          offset: params.offset ?? 0,
          limit: params.limit ?? 20,
        });

        return bots;
      } catch (error: any) {
        console.error(
          '[useBotNetwork] Failed to load recommended bots:',
          error,
        );
        handleNetworkError(error, {
          module: 'BotNetwork',
          action: '加载推荐好友',
        });
        return [];
      } finally {
        setLoadingRecommended(false);
      }
    },
    [
      driverBot,
      loadActors,
      setCooperatableOnly,
      setLoadingRecommended,
      setRecommendedBots,
      appendRecommendedBots,
      updateRecommendedPagination,
    ],
  );

  /**
   * 加载更多推荐好友 Bot
   */
  const loadMoreRecommendedBots = useCallback(async (): Promise<
    DiscoverBotInfo[]
  > => {
    const nextOffset =
      recommendedPagination.offset + recommendedPagination.limit;
    if (nextOffset >= recommendedPagination.total) return [];

    return loadRecommendedBots({
      offset: nextOffset,
      append: true,
    });
  }, [recommendedPagination, loadRecommendedBots]);

  /**
   * 获取 Bot 名称
   */
  const getBotName = useCallback(
    (botUuid: string): string => {
      const bot = getMyBotByUuid(botUuid);
      return bot?.bot_name || botUuid;
    },
    [getMyBotByUuid],
  );

  /**
   * 刷新 Bot 名称（调用 onboard 接口同步名称）
   */
  const refreshBotName = useCallback(
    async (
      botUuid: string,
      externalName: string,
      summary?: string,
    ): Promise<boolean> => {
      const bot = getMyBotByUuid(botUuid);
      if (!bot) {
        toast.error('Bot 不存在');
        return false;
      }

      try {
        // 调用 BCN onboard 接口，传入外部名称
        const response = await BcnController.onboardBot({
          bot_id: botUuid,
          name: externalName,
          summary: summary || bot.summary || '',
          hidden: false,
        });

        if (response.error) {
          throw new Error(response.error);
        }

        // 更新本地状态
        updateMyBot(botUuid, {
          bot_name: externalName,
          needsRefresh: false,
          externalName: undefined,
        });

        // 如果当前 driverBot 是这个 bot，也更新 driverBot
        const currentDriverBot = useBotNetworkStore.getState().driverBot;
        if (botUuid === currentDriverBot?.bot_uuid) {
          setDriverBot({ ...currentDriverBot, bot_name: externalName });
        }

        return true;
      } catch (error: any) {
        console.error('[useBotNetwork] Failed to refresh bot name:', error);
        handleNetworkError(error, {
          module: 'BotNetwork',
          action: '刷新 Bot 名称',
        });
        return false;
      }
    },
    [getMyBotByUuid, updateMyBot, setDriverBot],
  );

  /**
   * 初始化 Human Actor
   * 调用 /bcnproxy/me/ensure-human 接口
   */
  const initHumanActor = useCallback(
    async (botUuid: string): Promise<boolean> => {
      const bot = getMyBotByUuid(botUuid);
      if (!bot || bot.actor_kind !== 'human') {
        toast.error('Human Actor 不存在');
        return false;
      }

      try {
        const response = await BcnController.ensureHuman();

        if (response.error) {
          throw new Error(response.error);
        }

        // 更新本地状态，标记已初始化
        updateMyBot(botUuid, {
          needsHumanInit: false,
          visibility: 'public',
          status: 'online',
          is_online: computeIsOnline({
            status: 'online',
            visibility: 'public',
          }),
        } as any);

        toast.success('用户初始化成功');
        return true;
      } catch (error: any) {
        console.error('[useBotNetwork] Failed to init human actor:', error);
        handleNetworkError(error, {
          module: 'BotNetwork',
          action: '初始化 Human Actor',
        });
        return false;
      }
    },
    [getMyBotByUuid, updateMyBot],
  );

  return {
    // === State ===
    myBots,
    isLoadingMyBots,
    driverBot,
    discoveredBots,
    isLoadingDiscover,
    discoverPagination,
    recommendedBots,
    isLoadingRecommended,
    recommendedPagination,

    // === Actions ===
    initUnifiedBotTabs,
    initBotTabs,
    initFromUrl,
    initFromBcnMyBots,
    // loadMyBots,
    selectDriverBot,
    setBotVisibility,
    discoverBots,
    loadMoreDiscoveredBots,
    loadRecommendedBots,
    loadMoreRecommendedBots,
    refreshBotName,
    initHumanActor,

    // === Helpers ===
    getMyBotByUuid,
    getBotName,
    reset,
  };
}
