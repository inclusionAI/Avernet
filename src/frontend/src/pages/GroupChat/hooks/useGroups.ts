/**
 * useGroups - 群组管理业务逻辑 Hook
 *
 * 遵循三层架构：Hook层封装业务逻辑、API调用、错误处理和Toast
 * 仅负责群组列表、群组详情、消息历史的 HTTP API 调用
 * WebSocket 连接和消息管理由 useGroupChat hook 处理
 *
 * 重构说明：
 * - 移除 useUserStore、useBotStore 依赖
 * - 从 useBotNetworkStore 获取 driverBot
 */

import { useActor } from '@/hooks/useActor';
import type {
  CreateGroupParams,
  GroupInfo,
  GroupMemberRole,
} from '@/pages/GroupChat/types';
import * as BcnController from '@/services/backend-api/BcnController';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { useGroupChatStore } from '@/stores/groupChatStore';
import type { ActorBot } from '@/stores/actorStore';
import { handleNetworkError } from '@/utils/hooksErrorHandler';
import { Tracert } from '@/utils/tracert';
import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';

const buildGroupExtra = (
  group?: { context?: string; goal?: string; extra?: Record<string, any> },
  fallbackContext?: string,
): GroupInfo['extra'] => {
  const extra = { ...(group?.extra || {}) };
  const context = group?.context || extra.context || fallbackContext;
  const goal = group?.goal || extra.goal;

  if (context) extra.context = context;
  if (goal) extra.goal = goal;

  return Object.keys(extra).length > 0 ? extra : undefined;
};

/**
 * 群聊管理 Hook
 * 封装所有群聊相关的业务逻辑和 API 调用
 */
export function useGroups() {
  const driverBot = useBotNetworkStore((state) => state.driverBot);
  const {
    groups,
    activeGroupId,
    currentGroup,
    isLoadingGroups,
    isLoadingMoreGroups,
    hasMoreGroups,
    groupsTotal,
    isLoadingDetail,
    isCreatingGroup,
    isDeletingGroup,
    isAddingMember,
    isRemovingMember,
    messageCursor,
    botInfoMap,
    setGroups,
    appendGroups,
    addGroup,
    updateGroup,
    removeGroup,
    setActiveGroupId,
    setCurrentGroup,
    setLoadingGroups,
    setIsLoadingMoreGroups,
    setHasMoreGroups,
    setGroupsTotal,
    setLoadingDetail,
    setCreatingGroup,
    setBotInfoMap,
    getBotName,
    getGroupById,
    getActiveGroup,
    resetMessages,
    reset,
  } = useGroupChatStore();

  // ==================== 群组列表分页状态 ====================
  const GROUP_PAGE_SIZE = 20;
  const groupsOffsetRef = useRef(0);

  // ==================== 群组搜索 ====================
  const [groupSearchQuery, setGroupSearchQuery] = useState('');
  const [isSearchingGroups, setIsSearchingGroups] = useState(false);

  // ==================== 可用 Bot 列表分页状态 ====================
  const BOT_PAGE_SIZE = 20;
  const [availableBots, setAvailableBots] = useState<
    Array<{
      bot_uuid: string;
      bot_name: string;
      summary?: string;
      avatar_url?: string;
      domains?: string[];
      skills?: string[];
      visibility?: 'public' | 'protected';
      is_friend?: boolean | null;
      dynamic_status?: { status: string };
    }>
  >([]);
  const [isLoadingAvailableBots, setIsLoadingAvailableBots] = useState(false);
  const [hasMoreAvailableBots, setHasMoreAvailableBots] = useState(false);
  const [isLoadingMoreBots, setIsLoadingMoreBots] = useState(false);
  const availableBotsOffsetRef = useRef(0);

  // Actor hook（用于替换 discoverBots）
  const { loadActors } = useActor();

  const mapBotInfo = (
    bot: ActorBot,
  ): {
    bot_uuid: string;
    bot_name: string;
    summary?: string;
    avatar_url?: string;
    domains?: string[];
    skills?: string[];
    visibility?: 'public' | 'protected';
    is_friend?: boolean | null;
    dynamic_status?: { status: string };
  } => ({
    bot_uuid: bot.bot_uuid,
    bot_name: bot.bot_name || bot.capabilities?.name || bot.bot_uuid,
    summary: bot.summary ?? bot.capabilities?.description ?? undefined,
    avatar_url: bot.avatar_url,
    domains: bot.capabilities?.domains || [],
    skills:
      bot.capabilities?.skills?.map((s: { name: string }) => s.name) || [],
    visibility: (bot.visibility as 'public' | 'protected') ?? 'public',
    is_friend: bot.is_friend,
    dynamic_status: bot.dynamic_status,
  });

  /**
   * 转换 BCS 群组数据为前端格式
   */
  const transformGroupData = useCallback(
    (items: BcnController.GroupInfo[] | undefined): GroupInfo[] => {
      if (!items) return [];
      return items.map((group) => ({
        id: group.group_id,
        topic: group.label || '未命名群组',
        creatorId: '', // BCS 响应中没有 creatorId
        coordinatorBot: group.coordinator_bot,
        participants: group.participants.map((p) => ({
          id: p.bot_uuid,
          botUuid: p.bot_uuid,
          name: p.bot_name || p.bot_uuid,
          type: (p.actor_kind === 'human' ? 'user' : 'bot') as 'user' | 'bot',
          role: p.role as GroupMemberRole,
          actorKind: p.actor_kind,
          mode: p.mode,
        })),
        createdAt: group.created_at,
        updatedAt: group.updated_at,
        groupKind: group.group_kind,
        groupStrategy: group.group_strategy || 'chat',
        masterBot: group.master_bot,
        extra: buildGroupExtra(group),
        visibility: group.visibility || 'private',
      }));
    },
    [],
  );

  /**
   * 加载群聊列表（第一页）
   */
  const loadGroups = useCallback(
    async (botId?: string, searchQuery?: string) => {
      // 使用传入的 botId 或当前 driverBot
      const targetBotId = botId || driverBot?.bot_uuid;
      if (!targetBotId) {
        console.warn('[useGroups] driverBot is not set, skipping load');
        return;
      }

      try {
        setLoadingGroups(true);
        groupsOffsetRef.current = 0;
        setIsSearchingGroups(!!searchQuery);

        // 使用指定的 Bot ID 获取群组列表
        const response = await BcnController.getBotGroups({
          bot_uuid: targetBotId,
          limit: GROUP_PAGE_SIZE,
          offset: 0,
          q: searchQuery || undefined,
        });

        if (response.error) {
          throw new Error(response.error);
        }

        // 兼容不同的响应格式
        const items = response.items || response.groups;
        if (items) {
          const transformedGroups = transformGroupData(items);
          setGroups(transformedGroups);
          setGroupsTotal(response.total || items.length);
          setHasMoreGroups(items.length === GROUP_PAGE_SIZE);
          groupsOffsetRef.current = items.length;
          return transformedGroups;
        } else {
          setGroups([]);
          setGroupsTotal(0);
          setHasMoreGroups(false);
        }
      } catch (error: any) {
        console.error('[useGroups] Failed to load groups:', error);
        handleNetworkError(error, { module: 'Groups', action: '加载群聊列表' });
      } finally {
        setLoadingGroups(false);
      }
    },
    // 只依赖 driverBot?.bot_uuid 而不是整个 driverBot 对象
    [
      driverBot?.bot_uuid,
      setGroups,
      setLoadingGroups,
      setGroupsTotal,
      setHasMoreGroups,
      transformGroupData,
    ],
  );

  /**
   * 滚动加载更多群组
   */
  const loadMoreGroups = useCallback(
    async (botId?: string) => {
      // 使用传入的 botId 或当前 driverBot
      const targetBotId = botId || driverBot?.bot_uuid;
      if (!targetBotId || !hasMoreGroups || isLoadingMoreGroups) {
        return;
      }

      try {
        setIsLoadingMoreGroups(true);

        const response = await BcnController.getBotGroups({
          bot_uuid: targetBotId,
          limit: GROUP_PAGE_SIZE,
          offset: groupsOffsetRef.current,
          q: groupSearchQuery || undefined,
        });

        if (response.error) {
          throw new Error(response.error);
        }

        // 兼容不同的响应格式
        const items = response.items || response.groups;
        if (items && items.length > 0) {
          const transformedGroups = transformGroupData(items);
          appendGroups(transformedGroups);
          setHasMoreGroups(items.length === GROUP_PAGE_SIZE);
          groupsOffsetRef.current += items.length;
        } else {
          setHasMoreGroups(false);
        }
      } catch (error: any) {
        console.error('[useGroups] Failed to load more groups:', error);
        handleNetworkError(error, { module: 'Groups', action: '加载更多群组' });
      } finally {
        setIsLoadingMoreGroups(false);
      }
    },
    [
      driverBot?.bot_uuid,
      hasMoreGroups,
      isLoadingMoreGroups,
      appendGroups,
      setIsLoadingMoreGroups,
      setHasMoreGroups,
      transformGroupData,
    ],
  );

  /**
   * 搜索群组（按名称）
   */
  const searchGroups = useCallback(
    async (botId?: string, query?: string) => {
      setGroupSearchQuery(query || '');
      if (!query?.trim()) {
        // 清空搜索时重新加载列表
        await loadGroups(botId);
        return;
      }
      await loadGroups(botId, query);
    },
    [loadGroups],
  );

  /**
   * 加载群聊详情
   * @param groupId 群组 ID
   * @param options.addToGroupsList 如果群组不在列表中，是否自动添加到列表头部（用于视角切换后确保群组可见）
   */
  const loadGroupDetail = useCallback(
    async (groupId: string, options?: { addToGroupsList?: boolean }) => {
      try {
        setLoadingDetail(true);

        const response = await BcnController.getGroup({
          group_id: groupId,
        });

        if (response.error) {
          throw new Error(response.error);
        }

        if (response) {
          // API 返回的字段名可能不一致，需要兼容处理
          const group: GroupInfo = {
            id: response.group_id || (response as any).id || groupId,
            topic: response.label || '未命名',
            creatorId: '',
            coordinatorBot:
              response.coordinator_bot || (response as any).driver_bot,
            participants: (response.participants || []).map((p) => ({
              id: p.bot_uuid,
              botUuid: p.bot_uuid,
              name: p.bot_name || p.bot_uuid,
              type: (p.actor_kind === 'human' ? 'user' : 'bot') as
                | 'user'
                | 'bot',
              role: p.role as GroupMemberRole,
              actorKind: p.actor_kind,
              mode: p.mode,
            })),
            createdAt: (response as any).created_at || Date.now(),
            updatedAt: (response as any).updated_at || Date.now(),
            groupKind: response.group_kind,
            groupStrategy: response.group_strategy || 'chat',
            masterBot: response.master_bot,
            visibility: response.visibility,
            extra: buildGroupExtra(response),
          };
          console.log('[useGroups] loadGroupDetail transformed group:', group);
          setCurrentGroup(group);
          setActiveGroupId(groupId);

          // 如果群组不在列表中，自动添加到列表头部
          // 注意：必须校验当前 driver bot 确实是该群成员，否则
          // 仅凭 URL 携带的 groupId 会把 bot 未加入的群也注入列表
          // （桌面 bot getBotGroups 返回空、却仍渲染出群的根因）
          const isDriverBotMember =
            !!driverBot?.bot_uuid &&
            group.participants.some((p) => p.botUuid === driverBot.bot_uuid);
          if (
            options?.addToGroupsList &&
            isDriverBotMember &&
            !getGroupById(group.id)
          ) {
            addGroup(group);
          }
        }
      } catch (error: any) {
        console.error('[useGroups] Failed to load group detail:', error);
        handleNetworkError(error, { module: 'Groups', action: '加载群聊详情' });
      } finally {
        setLoadingDetail(false);
      }
    },
    [
      setCurrentGroup,
      setActiveGroupId,
      setLoadingDetail,
      addGroup,
      getGroupById,
      driverBot?.bot_uuid,
    ],
  );

  /**
   * 创建群组
   */
  const createGroup = useCallback(
    async (params: CreateGroupParams): Promise<GroupInfo | null> => {
      try {
        setCreatingGroup(true);
        const response = await BcnController.createGroup(params);
        if (response.error) {
          throw new Error(response.error);
        }

        if (response) {
          const driverBotId = response.driver_bot || params.driver_bot;
          const responseParticipants = response.participants || [];
          const newGroup: GroupInfo = {
            id: response.id || (response as any).group_id,
            topic: params.label,
            creatorId: '',
            coordinatorBot: driverBotId,
            participants: responseParticipants.map((participant) => {
              const botUuid =
                typeof participant === 'string'
                  ? participant
                  : participant.bot_uuid;
              const role =
                typeof participant === 'string'
                  ? botUuid === driverBotId
                    ? ('driver' as GroupMemberRole)
                    : ('consultant' as GroupMemberRole)
                  : ((participant.role || 'consultant') as GroupMemberRole);
              return {
                id: botUuid,
                botUuid: botUuid,
                name:
                  typeof participant === 'string'
                    ? botUuid
                    : participant.bot_name || botUuid,
                type: 'bot' as const,
                role,
              };
            }),
            createdAt: Date.now(),
            updatedAt: Date.now(),
            groupKind: response.group_kind,
            groupStrategy:
              response.group_strategy || params.group_strategy || 'chat',
            masterBot: params.master_bot,
            extra: buildGroupExtra(response as any, params.context),
          };
          addGroup(newGroup);
          // 埋点：创建群聊
          Tracert.click('c468610.d693569', {
            bot_count: params.participants?.length || 0,
          }); /* spm: 我的协作-群聊操作-创建群聊 */
          toast.success('协作群创建成功');
          return newGroup;
        }

        return null;
      } catch (error: any) {
        console.error('[useGroups] Failed to create group:', error);
        handleNetworkError(error, { module: 'Groups', action: '创建协作群' });
        return null;
      } finally {
        setCreatingGroup(false);
      }
    },
    [addGroup, setCreatingGroup],
  );

  /**
   * 更新群组
   */
  const updateGroupInfo = useCallback(
    async (groupId: string, data: Partial<GroupInfo>): Promise<boolean> => {
      try {
        updateGroup(groupId, data);
        toast.success('群组更新成功');
        return true;
      } catch (error: any) {
        console.error('[useGroups] Failed to update group:', error);
        handleNetworkError(error, { module: 'Groups', action: '更新协作群' });
        return false;
      }
    },
    [updateGroup],
  );

  /**
   * 删除群组
   */
  const deleteGroup = useCallback(
    async (groupId: string): Promise<boolean> => {
      if (!driverBot?.bot_uuid) {
        console.error('[useGroups] No driverBot available for delete');
        toast.error('删除失败：暂无可用 Bot');
        return false;
      }

      try {
        const response = await BcnController.deleteGroup({
          group_id: groupId,
          bot_id: driverBot.bot_uuid,
        });

        if (response?.error) {
          throw new Error(response.error);
        }

        // 从列表中移除
        removeGroup(groupId);

        // 如果删除的是当前选中的群组，清空当前选中
        if (activeGroupId === groupId) {
          setActiveGroupId('');
          setCurrentGroup(null);
        }

        toast.success('协作群已删除');
        // 埋点
        Tracert.click(
          'c468610.d693570',
        ); /* spm: 我的协作-群聊操作-删除协作群 */
        return true;
      } catch (error: any) {
        console.error('[useGroups] Failed to delete group:', error);
        handleNetworkError(error, { module: 'Groups', action: '删除协作群' });
        return false;
      }
    },
    [driverBot, activeGroupId, removeGroup, setActiveGroupId, setCurrentGroup],
  );

  /**
   * 选择群组
   */
  const selectGroup = useCallback(
    (groupId: string) => {
      setActiveGroupId(groupId);
      const group = getGroupById(groupId);
      if (group) {
        setCurrentGroup(group);
      }
    },
    [setActiveGroupId, getGroupById, setCurrentGroup],
  );

  /**
   * 清空当前群组
   */
  const clearCurrentGroup = useCallback(() => {
    setCurrentGroup(null);
    setActiveGroupId('');
    resetMessages();
  }, [setCurrentGroup, setActiveGroupId, resetMessages]);

  /**
   * 强制重新加载群组列表（重置缓存，忽略 loadedBotUuidRef）
   * 用于视角切换时确保群组列表刷新
   */
  const forceReloadGroups = useCallback(
    (botId?: string) => {
      groupsOffsetRef.current = 0;
      return loadGroups(botId);
    },
    [loadGroups],
  );

  /**
   * 加载可用 Bot 列表（第一页，打开弹窗时调用）
   * 使用 Actor API 替换 discoverBots
   */
  const loadAvailableBots = useCallback(
    async (collaborateBot?: string) => {
      const currentBotUuid = collaborateBot || driverBot?.bot_uuid;
      if (!currentBotUuid) {
        console.warn('[useGroups] No current bot UUID available');
        setAvailableBots([]);
        return;
      }

      setIsLoadingAvailableBots(true);
      availableBotsOffsetRef.current = 0;
      try {
        // 使用 Actor API（cooperatable_only=true 用于获取可协作 Bot）
        const response = await loadActors({
          currentBotUuid,
          cooperatableOnly: true,
          pageNo: 1,
          pageSize: BOT_PAGE_SIZE,
        });
        const responseBots = response.bots || [];
        const mapped = responseBots.map(mapBotInfo);
        setAvailableBots(mapped);
        setHasMoreAvailableBots(responseBots.length === BOT_PAGE_SIZE);
        availableBotsOffsetRef.current = responseBots.length;
      } catch (error: any) {
        console.error('[useGroups] Failed to load available bots:', error);
        handleNetworkError(error, {
          module: 'Groups',
          action: '加载 Bot 列表',
        });
        setAvailableBots([]);
      } finally {
        setIsLoadingAvailableBots(false);
      }
    },
    [driverBot, loadActors],
  );

  /**
   * 滚动加载更多可用 Bot
   * 使用 Actor API 替换 discoverBots
   */
  const loadMoreAvailableBots = useCallback(
    async (collaborateBot?: string) => {
      if (!hasMoreAvailableBots || isLoadingMoreBots) return;

      const currentBotUuid = collaborateBot || driverBot?.bot_uuid;
      if (!currentBotUuid) {
        console.warn('[useGroups] No current bot UUID available');
        return;
      }

      setIsLoadingMoreBots(true);
      try {
        const pageNo =
          Math.floor(availableBotsOffsetRef.current / BOT_PAGE_SIZE) + 1;
        // 使用 Actor API（cooperatable_only=true 用于获取可协作 Bot）
        const response = await loadActors({
          currentBotUuid,
          cooperatableOnly: true,
          pageNo,
          pageSize: BOT_PAGE_SIZE,
        });
        const responseBots = response.bots || [];
        setAvailableBots((prev) => [...prev, ...responseBots.map(mapBotInfo)]);
        setHasMoreAvailableBots(responseBots.length === BOT_PAGE_SIZE);
        availableBotsOffsetRef.current += responseBots.length;
      } catch (error: any) {
        console.error('[useGroups] Failed to load more bots:', error);
        handleNetworkError(error, {
          module: 'Groups',
          action: '加载更多 Bot',
        });
      } finally {
        setIsLoadingMoreBots(false);
      }
    },
    [hasMoreAvailableBots, isLoadingMoreBots, driverBot, loadActors],
  );

  /**
   * 加载 BCN 已注册的 Bot 信息（一次调用同时更新 UUID 列表和 infoMap）
   * 返回 UUID 列表，同时更新 botInfoMap
   */
  const loadBcnBots = useCallback(async (): Promise<string[]> => {
    try {
      console.log('[useGroups] Loading BCN bots...');
      const response = await BcnController.getBots({ onboarded: true });
      console.log('[useGroups] BCN response:', response);

      // BCN 接口直接返回数组
      const bots = response || [];
      if (bots.length > 0) {
        // 更新 botInfoMap
        const infoMap: Record<
          string,
          { bot_uuid: string; bot_name: string | null; summary?: string | null }
        > = {};
        bots.forEach((bot) => {
          infoMap[bot.bot_uuid] = {
            bot_uuid: bot.bot_uuid,
            bot_name: bot.capabilities.name,
            summary: bot.capabilities.summary,
          };
        });
        setBotInfoMap(infoMap);
        console.log('[useGroups] Loaded BCN bots:', bots.length, 'bots');
        console.log(
          '[useGroups] BCN bot UUIDs:',
          bots.map((b) => b.bot_uuid),
        );

        // 返回 UUID 列表
        return bots.map((bot) => bot.bot_uuid);
      }
      console.log('[useGroups] No bots in response');
      return [];
    } catch (error: any) {
      console.error('[useGroups] Failed to load BCN bots:', error);
      return [];
    }
  }, [setBotInfoMap]);

  /**
   * 获取 Bot BCN 详情
   * 用于检查 Bot 是否在 BCN 网络中且处于非隐藏状态
   */
  const getBotBCNDetail = useCallback(
    async (
      botUuid: string,
    ): Promise<
      | {
          success: true;
          bot_uuid: string;
          hidden: boolean;
          visibility?: string;
        }
      | { success: false }
    > => {
      try {
        const response = await BcnController.getBotBCNDetail(
          { bot_uuid: botUuid },
          { skipErrorHandler: true },
        );
        if (response) {
          return {
            success: true,
            bot_uuid: response.bot_uuid,
            hidden: response.capabilities.hidden,
            visibility: response.capabilities?.visibility ?? 'public',
          };
        }
        return { success: false };
      } catch (error: any) {
        console.error('[useGroups] Failed to get bot BCN detail:', error);
        return { success: false };
      }
    },
    [],
  );

  /**
   * 加载指定 Bot UUID 列表的信息
   * 用于加载当前群组成员的信息
   */
  const loadBotInfoByUuids = useCallback(
    async (botUuids: string[]) => {
      if (!botUuids.length) return;

      // 过滤掉已缓存的
      const uncachedUuids = botUuids.filter((uuid) => !botInfoMap[uuid]);
      if (!uncachedUuids.length) return;

      try {
        const response = await BcnController.getBots({ onboarded: true });
        // BCN 接口直接返回数组
        const bots = response || [];
        if (bots.length > 0) {
          const newInfoMap = { ...botInfoMap };
          bots
            .filter((bot) => uncachedUuids.includes(bot.bot_uuid))
            .forEach((bot) => {
              newInfoMap[bot.bot_uuid] = {
                bot_uuid: bot.bot_uuid,
                bot_name: bot.capabilities.name,
                summary: bot.capabilities.summary,
              };
            });
          setBotInfoMap(newInfoMap);
        }
      } catch (error: any) {
        console.error('[useGroups] Failed to load bot info by uuids:', error);
      }
    },
    [botInfoMap, setBotInfoMap],
  );

  return {
    // === State ===
    groups,
    activeGroupId,
    currentGroup,
    driverBot,
    isLoadingGroups,
    isLoadingMoreGroups,
    hasMoreGroups,
    groupsTotal,
    isLoadingDetail,
    isCreatingGroup,
    isDeletingGroup,
    isAddingMember,
    isRemovingMember,
    messageCursor,
    botInfoMap,

    // === Actions ===
    loadGroups,
    loadMoreGroups,
    loadGroupDetail,
    forceReloadGroups,
    createGroup,
    updateGroupInfo,
    deleteGroup,
    selectGroup,
    clearCurrentGroup,
    availableBots,
    isLoadingAvailableBots,
    hasMoreAvailableBots,
    isLoadingMoreBots,
    loadAvailableBots,
    loadMoreAvailableBots,
    loadBcnBots,
    loadBotInfoByUuids,
    getBotBCNDetail,
    reset,
    searchGroups,
    groupSearchQuery,
    isSearchingGroups,

    // === Helpers ===
    getGroupById,
    getActiveGroup,
    getBotName,
  };
}
