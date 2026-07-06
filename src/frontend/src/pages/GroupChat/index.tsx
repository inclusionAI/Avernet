/**
 * 群聊页面入口
 *
 * 布局：顶部导航 + 聊天区域（左右布局）
 * - 顶部导航：左侧 Bot Tab（头像+名称+状态灯）+ 右侧操作按钮
 * - 聊天区域：左侧协作群列表 + 右侧聊天内容
 *
 * 数据传入方式：
 * 1. 外部传入 bots props（推荐）
 * 2. URL 参数 bot_uuid（独立部署场景）
 * 3. 从 useBotStore 自动获取（主应用集成场景）
 */

import { engineAdapterFactory } from '@/adapters/engine/EngineAdapterFactory';
import { useExt } from '@/capabilities';
import Empty from '@/components/Empty';
import { MobileHeader, TitleSwitcher } from '@/components/MobileHeader';
import { useMobileNav } from '@/contexts/MobileNavContext';
import { useBot } from '@/hooks/useBot';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import {
  BREAKPOINTS,
  useIsBelowMinWidth,
  useIsMobile,
} from '@/hooks/useMediaQuery';
import { AppExt } from '@/shell/extension';
import type { BotNetworkInfo, DriverBot } from '@/stores/botNetworkStore';
import { computeIsOnline, useBotNetworkStore } from '@/stores/botNetworkStore';
import { useBotStore } from '@/stores/botStore';
import { useGroupSessionStore } from '@/stores/groupSessionStore';
import { useUserStore } from '@/stores/userStore';
import { history, useSearchParams } from '@umijs/max';
import { MessageSquare, Network, Users } from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { toast } from 'sonner';
import BcnOnboardingGuide from './components/BcnOnboardingGuide';
import CreateGroupModal from './components/CreateGroupModal';
import FriendModal from './components/FriendModal';
import GroupChatPage from './components/GroupChatPage';
import GroupListPanel from './components/GroupListPanel';
import GroupSettingsDrawer from './components/GroupSettingsDrawer';
import ServiceInvocationSessionModal from './components/ServiceInvocationSessionModal';
import SessionListPanel from './components/SessionListPanel';
import SessionSettingsDrawer from './components/SessionSettingsDrawer';
import TopNavBar from './components/TopNavBar';
import { getBcnActiveOnlyPreference } from './constants';
import { useActor } from './hooks/useActor';
import { useBotNetwork } from './hooks/useBotNetwork';
import { useFriends } from './hooks/useFriends';
import { useGroupChatProviders } from './hooks/useGroupChatProviders';
import { useGroups } from './hooks/useGroups';
import { useGroupSessions } from './hooks/useGroupSessions';
import './index.less';
import type { BotTabItem, GroupInfo } from './types';

/** GroupChat 组件 Props */
interface GroupChatProps {
  /** 外部传入的 Bot 列表（推荐方式） */
  bots?: BotTabItem[];
  /** 默认选中的 Bot UUID */
  defaultBotUuid?: string;
  /** Bot 切换回调（可选，用于通知外部系统） */
  onBotChange?: (bot: {
    bot_uuid: string;
    bot_name: string;
    visibility?: string;
  }) => void;
}

/**
 * 从 useBotStore 的 bots 转换为 BotTabItem[] 格式
 * bot_uuid 格式：bot_id:entity_id
 *
 * 仅保留 adapter 配置 canJoinBcn=true 的 Bot（当前为云端 OC + 桌面 OC）。
 * 仅内部版使用（bcnProxyOnly=false）；开源版走 /bcnproxy/bots/my，不调此函数。
 */
function convertBotsToTabItems(
  bots: ReturnType<typeof useBotStore.getState>['bots'],
): BotTabItem[] {
  return bots
    .filter((bot) => bot.status === 'ACTIVE')
    .filter((bot) => {
      const engineType = bot.active_engine || bot.engine_types?.[0];
      if (!engineType) return false;
      const adapter = engineAdapterFactory.getAdapter(engineType, {
        templateType: bot.template_type,
      });
      return adapter.supports('canJoinBcn', {
        isDesktopBot: bot.bot_type === 'desktop',
        botType: bot.bot_type,
      });
    })
    .map((bot) => ({
      bot_uuid: `${bot.bot_id}:${bot.entity_id}`,
      bot_name: bot.bot_name,
      avatar_url: bot.ext?.avatar_url ?? undefined,
      summary: bot.bot_desc ?? undefined,
    }));
}

function toDriverBot(bot: BotNetworkInfo): DriverBot {
  return {
    bot_uuid: bot.bot_uuid,
    bot_name: bot.bot_name,
    visibility: bot.visibility,
    avatar_url: bot.avatar_url,
    status: bot.status,
    dynamic_status: bot.dynamic_status,
    is_online: bot.is_online,
  };
}

const GroupChat: React.FC<GroupChatProps> = ({
  bots: externalBots,
  defaultBotUuid,
  onBotChange,
}) => {
  const isMobile = useIsMobile();
  const isBelowMinWidth = useIsBelowMinWidth();

  // 获取打开导航抽屉的方法
  let openMobileNav: (() => void) | null = null;
  try {
    const nav = useMobileNav();
    openMobileNav = nav.openMobileNav;
  } catch {
    // 非移动端，忽略
  }

  // human 身份收口到 authAdapter（开源走 /me，内部读 __TERN__），写入 userStore
  useHumanIdentity();

  const [searchParams] = useSearchParams();
  const groupId = searchParams.get('id') || '';
  const urlBotUuid = searchParams.get('bot_uuid') || '';
  const urlSessionId = searchParams.get('session') || '';
  const skipBrain = searchParams.get('skipBrain') || '';

  /**
   * 构建带独立页面参数的 URL
   * 如果当前是独立页面模式（有 bot_uuid 参数），则在跳转时保留这些参数
   */
  const buildUrlWithStandaloneParams = useCallback(
    (path: string, extraParams?: Record<string, string>) => {
      const pathnamePrefix = window.location.pathname.startsWith('/bcn')
        ? '/bcn/chat'
        : '/group-chat';

      if (!urlBotUuid) {
        // 非独立页面模式，直接返回原路径
        return extraParams
          ? `${pathnamePrefix}${path}?${new URLSearchParams(
              extraParams,
            ).toString()}`
          : `${pathnamePrefix}${path}`;
      }

      // 独立页面模式，保留 bot_uuid 参数
      const params = new URLSearchParams();
      if (urlBotUuid) params.set('bot_uuid', urlBotUuid);

      // 添加额外参数
      if (extraParams) {
        Object.entries(extraParams).forEach(([key, value]) => {
          params.set(key, value);
        });
      }

      return `${pathnamePrefix}${path}?${params.toString()}`;
    },
    [urlBotUuid, skipBrain],
  );

  // === 从 useBotStore 获取 bots（内部版主应用集成场景） ===
  const storeBots = useBotStore((state) => state.bots);
  const activeBotId = useBotStore((state) => state.activeBotId);

  // BCN 闭包门控：开源版 bcnProxyOnly=true → 不从 /api 转 BotTabItem，
  // initUnifiedBotTabs 走 Phase 0.5 由 /bcnproxy/bots/my 驱动。
  // 内部版 bcnProxyOnly=false → 保留 convertBotsToTabItems(storeBots) 路径。
  const { bcnProxyOnly } = useExt(AppExt).features;

  const convertedBots = useMemo(
    () => (bcnProxyOnly ? [] : convertBotsToTabItems(storeBots)),
    [bcnProxyOnly, storeBots],
  );

  // 确定使用哪个数据源：外部传入 > 转换后的 store 数据
  const botsToUse = useMemo(() => {
    if (externalBots && externalBots.length > 0) {
      return externalBots;
    }
    return convertedBots;
  }, [externalBots, convertedBots]);

  // 确定默认选中的 bot_uuid
  const defaultBotUuidToUse = useMemo(() => {
    if (defaultBotUuid) return defaultBotUuid;
    // 从 activeBotId 转换
    if (activeBotId) {
      const activeBot = storeBots.find((b) => b.bot_id === activeBotId);
      if (activeBot) {
        return `${activeBot.bot_id}:${activeBot.entity_id}`;
      }
    }
    return undefined;
  }, [defaultBotUuid, activeBotId, storeBots]);

  // === 使用 Hooks ===
  const {
    myBots,
    isLoadingMyBots,
    driverBot,
    initUnifiedBotTabs,
    selectDriverBot,
    setBotVisibility,
    initHumanActor,
  } = useBotNetwork();

  const {
    unreadRequestCount,
    loadRequests,
    reset: resetFriendStore,
  } = useFriends();

  const {
    groups,
    currentGroup,
    isLoadingGroups,
    isLoadingMoreGroups,
    hasMoreGroups,
    loadGroups,
    loadMoreGroups,
    loadGroupDetail,
    // forceReloadGroups,
    deleteGroup,
    getBotName,
    clearCurrentGroup,
  } = useGroups();

  const { disconnectAll } = useGroupChatProviders();

  // === 会话管理 ===
  const {
    sessions,
    activeSessionId,
    isLoadingSessions,
    hasMoreSessions,
    currentSession,
    sessionMessages,
    isLoadingSessionMessages,
    isCreatingSession,
    isUpdatingSessionTitle,
    loadSessions,
    loadMoreSessions,
    searchSessions,
    createSession,
    selectSession,
    loadSessionMessages,
    joinSession,
    leaveSession,
    deleteSession,
    updateSessionMemberMode,
    updateSessionTitle,
    refreshCurrentSession,
    clearCurrentSession,
    resetSessions,
  } = useGroupSessions();

  // 状态
  const [searchQuery, setSearchQuery] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const loadGroupsRef = useRef(loadGroups);
  const userWentBackRef = useRef(false);
  // 标记当前会话列表是否处于搜索态，搜索态下不要把缺席的 activeSession 重新拉回列表
  const sessionSearchActiveRef = useRef(false);

  // 保持 loadGroupsRef 最新
  useEffect(() => {
    loadGroupsRef.current = loadGroups;
  }, [loadGroups]);

  // 搜索防抖，仅在搜索关键词变化时触发
  // 注意：bot 切换时的群组加载由下方的 driverBot effect 处理，不在此处重复触发
  useEffect(() => {
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }
    searchTimerRef.current = setTimeout(() => {
      if (driverBot?.bot_uuid) {
        loadGroupsRef.current(driverBot.bot_uuid, searchQuery);
      }
    }, 300); // 300ms 防抖

    return () => {
      if (searchTimerRef.current) {
        clearTimeout(searchTimerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery]);
  const [showSessionSettings, setShowSessionSettings] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsTargetGroup, setSettingsTargetGroup] =
    useState<GroupInfo | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showGroupSelector, setShowGroupSelector] = useState(false);
  const [showFriendModal, setShowFriendModal] = useState(false);
  const [
    showServiceInvocationSessionModal,
    setShowServiceInvocationSessionModal,
  ] = useState(false);
  const [groupListCollapsed, setGroupListCollapsed] = useState(false);
  const [sessionListCollapsed, setSessionListCollapsed] = useState(false);
  const [showSessionDrawer, setShowSessionDrawer] = useState(false);
  const [isInitialized, setIsInitialized] = useState(false);

  // === 初始化逻辑 ===

  // 优先级：/bcn/chat/list 路由 > URL 参数 bot_uuid > 外部传入 bots > useBotStore 数据
  // 使用 ref 避免重复执行
  const isInitializingRef = useRef(false);

  // 内部版：预加载 Bot 列表到 botStore.bots + botStore.allBots（/api/bots/by-owner-or-collaborator）。
  // 开源版 bcnProxyOnly=true：不调 loadBots，initUnifiedBotTabs 走 /bcnproxy/bots/my 并反写 botStore。
  const { loadBots } = useBot({ autoFetchTotalBotCount: false });

  // 使用 ref 保持 externalBots 最新值，避免初始化 effect 的闭包过期问题
  const externalBotsRef = useRef(externalBots);
  externalBotsRef.current = externalBots;

  // 订阅 useUserStore.userId，作为初始化 effect 的依赖。
  // 不能直接用 effectiveUserId（本地 state），因为 loadBots 是 useCallback，
  // 其 closure 中的 userId 来自 Zustand 订阅。必须在 React 重渲染、loadBots
  // 拿到新 closure 之后再调用，否则 loadBots 内部读到 undefined 直接 return。
  const storeUserId = useUserStore((state) => state.userId);

  useEffect(() => {
    if (isInitialized || isInitializingRef.current) return;

    // 等待 userStore.userId 就绪（React 已重渲染，loadBots closure 已更新）
    if (!storeUserId) {
      console.log('[GroupChat] Waiting for storeUserId...');
      return;
    }

    const initialize = async () => {
      isInitializingRef.current = true;
      console.log('[GroupChat] Initializing...', {
        hasExternalBots: !!externalBots?.length,
        bcnProxyOnly,
        urlBotUuid,
        defaultBotUuidToUse,
        storeUserId,
      });

      let localBotsForInit: BotTabItem[] = [];

      if (!bcnProxyOnly) {
        // 内部版：调 loadBots（/api），再从 store 转 BotTabItem
        await loadBots({ skipAutoActivate: true });
        const latestBots = useBotStore.getState().bots;
        const latestConverted = convertBotsToTabItems(latestBots);
        localBotsForInit =
          externalBotsRef.current && externalBotsRef.current.length > 0
            ? externalBotsRef.current
            : latestConverted;
      } else {
        // 开源版：不调 loadBots，仅传 externalBots；无则空数组走 Phase 0.5
        localBotsForInit =
          externalBotsRef.current && externalBotsRef.current.length > 0
            ? externalBotsRef.current
            : [];
      }

      console.log(
        '[GroupChat] localBotsForInit count:',
        localBotsForInit.length,
      );

      // 统一初始化：合并 local bots + BCN bots
      await initUnifiedBotTabs({
        localBots: localBotsForInit,
        targetBotUuid: urlBotUuid || undefined,
        activeOnly: getBcnActiveOnlyPreference(),
      });

      setIsInitialized(true);
      isInitializingRef.current = false;
    };

    initialize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInitialized, storeUserId]);

  // 当 driverBot 变化时加载群组列表和好友请求
  // 使用 ref 记录已加载的 botUuid，避免重复加载
  const loadedBotUuidRef = useRef<string | null>(null);
  // 使用 ref 记录已加载的 groupId，避免重复加载
  const loadedGroupIdRef = useRef<string | null>(null);
  // 使用 ref 记录已加载会话的 groupId，避免重复加载
  const loadedSessionGroupIdRef = useRef<string | null>(null);
  // 标记是否为视角切换（而非群组切换），用于会话恢复逻辑
  const isPerspectiveSwitchRef = useRef(false);

  useEffect(() => {
    const botUuid = driverBot?.bot_uuid;
    const visibility = driverBot?.visibility;

    // 如果已经加载过这个 bot 的群组，跳过
    if (!botUuid || loadedBotUuidRef.current === botUuid) {
      return;
    }

    // 判断是否为视角切换：
    // - 首次 mount：不是视角切换
    // - 前 bot 被过滤掉（如"仅显示活跃 Bot"开关导致不活跃 bot 从列表消失）：不是视角切换，
    //   应清空会话并重新选择第一个群
    // - 其他情况（用户手动切换 bot）：是视角切换，保留当前会话选中状态
    const isFirstMount = loadedBotUuidRef.current === null;
    const prevBotUuid = loadedBotUuidRef.current;
    const prevBotFilteredOut =
      prevBotUuid !== null && !myBots.some((b) => b.bot_uuid === prevBotUuid);
    const isPerspectiveSwitch = !isFirstMount && !prevBotFilteredOut;

    loadedBotUuidRef.current = botUuid;

    // Bot 切换时重置群组详情和会话的加载标记，确保重新加载
    loadedGroupIdRef.current = null;
    loadedSessionGroupIdRef.current = null;
    // 标记为视角切换（首次 mount 或被过滤掉时不标记，避免误恢复 store 残留的 activeSessionId）
    isPerspectiveSwitchRef.current = isPerspectiveSwitch;

    // 如果前 bot 被过滤掉（activeOnly 切换导致），清空当前会话和群组
    if (prevBotFilteredOut) {
      clearCurrentSession();
      resetSessions();
      clearCurrentGroup();
      // 清除 URL 中的 groupId，防止旧群组数据残留触发不必要的加载
      if (groupId) {
        history.replace(buildUrlWithStandaloneParams(''));
      }
    }

    // 加载群组列表
    loadGroups(botUuid).then((loadedGroups) => {
      if (loadedGroups && loadedGroups.length > 0) {
        // prevBotFilteredOut 时 URL 已清除，此时 groupId 仍为旧值（闭包），
        // 需要通过 prevBotFilteredOut 强制跳转到第一个群
        if (prevBotFilteredOut || !groupId) {
          history.replace(
            buildUrlWithStandaloneParams('/detail', {
              id: loadedGroups[0].id,
            }),
          );
        }
      }
    });

    if (visibility !== 'offline') {
      // 加载好友请求（检查是否有新的好友请求）
      loadRequests(botUuid, 'received');
    }
    // 只依赖关键属性，函数引用变化不触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driverBot?.bot_uuid, driverBot?.visibility]);

  // 加载群组详情和消息
  useEffect(() => {
    const botUuid = driverBot?.bot_uuid;
    if (!groupId || !botUuid || isLoadingGroups) {
      return;
    }

    // 如果已经加载过这个群组的详情，跳过
    if (loadedGroupIdRef.current === groupId) {
      return;
    }

    loadedGroupIdRef.current = groupId;

    // 群组列表加载完成后，检查目标群组是否在列表中
    const groupExists = groups.some((g) => g.id === groupId);
    if (groupExists) {
      loadGroupDetail(groupId);
    } else {
      // 群组不在当前列表中（视角切换后可能不在首页），通过 API 获取并添加到列表头部
      loadGroupDetail(groupId, { addToGroupsList: true });
    }
    // 只依赖关键值，函数引用变化不触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, groups.length, driverBot?.bot_uuid, isLoadingGroups]);

  // === 会话管理：选中群组时加载会话列表 ===

  // 标记 URL session 是否已应用，避免后续 effect 重复处理
  const urlSessionAppliedRef = useRef(false);

  useEffect(() => {
    if (!groupId) {
      clearCurrentSession();
      resetSessions();
      loadedSessionGroupIdRef.current = null;
      userWentBackRef.current = false;
      sessionSearchActiveRef.current = false;
      urlSessionAppliedRef.current = false;
      return;
    }

    // 等待 Bot 初始化完成，避免使用 store 中残留的旧 driverBot.bot_uuid 作为 viewBotId
    if (!isInitialized) return;

    // 群组切换或视角切换时加载会话
    if (loadedSessionGroupIdRef.current !== groupId) {
      userWentBackRef.current = false;
      sessionSearchActiveRef.current = false;
      urlSessionAppliedRef.current = false;
      const savedSessionId = activeSessionId;
      const isPerspectiveSwitch = isPerspectiveSwitchRef.current;

      if (isPerspectiveSwitch) {
        // 视角切换：保留当前会话选中状态，重新加载会话列表后恢复选中
        isPerspectiveSwitchRef.current = false;
        loadedSessionGroupIdRef.current = groupId;
        loadSessions(groupId, 0, driverBot?.bot_uuid).then(() => {
          if (savedSessionId) {
            const currentSessions = useGroupSessionStore.getState().sessions;
            const exists = currentSessions.some(
              (s) => s.sessionId === savedSessionId,
            );
            selectSession(savedSessionId, {
              addToSessionsList: !exists,
              viewBotId: driverBot?.bot_uuid,
            });
          }
        });
      } else {
        // 群组切换：清空当前会话，重新加载，加载完成后按优先级选中
        loadedSessionGroupIdRef.current = groupId;
        clearCurrentSession();
        resetSessions();
        loadSessions(groupId, 0, driverBot?.bot_uuid).then(() => {
          const currentSessions = useGroupSessionStore.getState().sessions;

          // 优先级 1：URL 指定了 session → 选中它
          if (urlSessionId) {
            const exists = currentSessions.some(
              (s) => s.sessionId === urlSessionId,
            );
            selectSession(urlSessionId, {
              addToSessionsList: !exists,
              viewBotId: driverBot?.bot_uuid,
            });
            urlSessionAppliedRef.current = true;
            return;
          }

          // 优先级 2：只有一个会话时自动选中
          if (currentSessions.length === 1) {
            selectSession(currentSessions[0].sessionId, {
              viewBotId: driverBot?.bot_uuid,
            });
          }
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, driverBot?.bot_uuid, isInitialized]);

  // 兜底：sessions 通过 loadMore/搜索等方式更新后，如果仍未选中且符合条件则自动选中
  useEffect(() => {
    // URL session 已在 loadSessions 回调中处理过，跳过
    if (urlSessionAppliedRef.current) return;
    // 用户主动返回会话列表，不自动选中
    if (userWentBackRef.current) return;

    if (
      !activeSessionId &&
      sessions.length === 1 &&
      !isLoadingSessions &&
      groupId &&
      !urlSessionId
    ) {
      selectSession(sessions[0].sessionId, {
        viewBotId: driverBot?.bot_uuid,
      });
    }
  }, [
    sessions.length,
    activeSessionId,
    isLoadingSessions,
    groupId,
    selectSession,
    driverBot?.bot_uuid,
    urlSessionId,
  ]);

  // 确保当前选中的会话在列表中（视角切换后可能不在首页）
  useEffect(() => {
    if (
      !activeSessionId ||
      !groupId ||
      isLoadingSessions ||
      sessions.length === 0
    ) {
      return;
    }

    // 搜索态下,如果 activeSession 不在结果里,交给 SessionListPanel 的 onClearSession 处理,
    // 不要在这里把会话重新拉回列表,否则会与清除选中状态打架
    if (sessionSearchActiveRef.current) {
      return;
    }

    const sessionExists = sessions?.some(
      (s) => s.sessionId === activeSessionId,
    );
    if (!sessionExists) {
      // 会话不在列表中，通过 API 获取并添加到列表头部
      selectSession(activeSessionId, {
        addToSessionsList: true,
        viewBotId: driverBot?.bot_uuid,
      });
    }
    // selectSession 是稳定的 useCallback，不需要加入依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId, groupId, sessions.length, isLoadingSessions]);

  // 判断是否显示会话列表面板
  const showSessionList = useMemo(() => {
    if (!currentGroup) return false;
    // 任务协作群和自由聊天群均显示会话列表
    return true;
  }, [currentGroup]);

  // 离开页面时断开所有连接
  useEffect(() => {
    return () => {
      disconnectAll();
    };
  }, [disconnectAll]);

  // 点击群组
  const handleGroupClick = (id: string) => {
    if (id !== groupId) {
      // 关闭副屏
      if (window.aixBridge?.closePanelForce) {
        window.aixBridge.closePanelForce();
      }
      history.push(buildUrlWithStandaloneParams('/detail', { id }));
    }
  };

  // === 会话操作回调 ===
  const handleSelectSession = useCallback(
    (sessionId: string) => {
      userWentBackRef.current = false;
      selectSession(sessionId, { viewBotId: driverBot?.bot_uuid });
    },
    [selectSession, driverBot?.bot_uuid],
  );

  const handleCreateSession = useCallback(async () => {
    if (!groupId) return;
    if (currentGroup?.groupStrategy === 'state_machine') {
      setShowServiceInvocationSessionModal(true);
      return;
    }

    await createSession(
      groupId,
      {
        session_kind: 'chat',
        session_title: '新会话',
        created_by: driverBot?.bot_uuid,
      },
      { viewBotId: driverBot?.bot_uuid },
    );
  }, [
    groupId,
    currentGroup?.groupStrategy,
    createSession,
    driverBot?.bot_uuid,
  ]);

  const handleCreateServiceInvocationSession = useCallback(
    async ({ title, query }: { title: string; query: string }) => {
      if (!groupId) return false;
      const result = await createSession(
        groupId,
        {
          session_kind: 'service_invocation',
          session_title: title,
          created_by: driverBot?.bot_uuid,
          input: { query },
        },
        { viewBotId: driverBot?.bot_uuid },
      );
      return !!result;
    },
    [groupId, createSession, driverBot?.bot_uuid],
  );

  const serviceInvocationDefaultQuery = useMemo(() => {
    if (currentGroup?.groupStrategy !== 'state_machine') return '';
    return (
      currentGroup.extra?.context ||
      currentGroup.extra?.goal ||
      ''
    ).trim();
  }, [
    currentGroup?.groupStrategy,
    currentGroup?.extra?.context,
    currentGroup?.extra?.goal,
  ]);

  const handleLoadMoreSessions = useCallback(() => {
    if (groupId) {
      loadMoreSessions(groupId, driverBot?.bot_uuid);
    }
  }, [groupId, loadMoreSessions, driverBot?.bot_uuid]);

  const handleSearchSessions = useCallback(
    (query: string) => {
      if (groupId) {
        sessionSearchActiveRef.current = !!query?.trim();
        searchSessions(groupId, query, driverBot?.bot_uuid);
      }
    },
    [groupId, searchSessions, driverBot?.bot_uuid],
  );

  const handleOpenGroupSettings = useCallback(
    (id: string) => {
      const target = groups.find((g) => g.id === id) || currentGroup;
      setSettingsTargetGroup(target);
      setShowSettings(true);
    },
    [groups, currentGroup],
  );

  const handleOpenSessionSettings = useCallback(
    (sessionId: string) => {
      handleSelectSession(sessionId);
      setShowSessionSettings(true);
    },
    [handleSelectSession],
  );

  const handleBackToSessionList = useCallback(() => {
    userWentBackRef.current = true;
    clearCurrentSession();
  }, [clearCurrentSession]);

  const handleJoinSession = useCallback(
    async (sessionId: string, actorId: string) => {
      return joinSession(sessionId, actorId);
    },
    [joinSession],
  );

  const handleLeaveSession = useCallback(
    async (sessionId: string, actorId: string) => {
      return leaveSession(sessionId, actorId);
    },
    [leaveSession],
  );

  const handleUpdateSessionTitle = useCallback(
    async (sessionId: string, title: string) => {
      return updateSessionTitle(sessionId, title);
    },
    [updateSessionTitle],
  );

  const handleUpdateSessionMemberMode = useCallback(
    async (sessionId: string, actorId: string, mode: 'auto' | 'muted') => {
      return updateSessionMemberMode(sessionId, actorId, mode);
    },
    [updateSessionMemberMode],
  );

  const handleRefreshSession = useCallback(
    async (sessionId: string) => {
      await refreshCurrentSession(sessionId);
    },
    [refreshCurrentSession],
  );

  // 手动重连时重新拉取会话消息（无 cursor,不清空,直接整体替换为后端最新历史）
  // viewBotId 与首次加载一致(driverBot.bot_uuid),避免拉到不同视角的消息
  const handleReloadMessages = useCallback(
    async (sessionId: string) => {
      await loadSessionMessages(sessionId, driverBot?.bot_uuid);
    },
    [loadSessionMessages, driverBot?.bot_uuid],
  );

  // 创建群组成功
  const handleCreateSuccess = async (group: GroupInfo) => {
    setShowCreateModal(false);
    if (group.coordinatorBot) {
      const bot = myBots.find((b) => b.bot_uuid === group.coordinatorBot);
      if (bot) {
        selectDriverBot(toDriverBot(bot));
        // 通知外部系统
        onBotChange?.({
          bot_uuid: bot.bot_uuid,
          bot_name: bot.bot_name,
          visibility: bot.visibility,
        });
      }
      await loadGroups(group.coordinatorBot);
    }
    history.push(buildUrlWithStandaloneParams('/detail', { id: group.id }));
  };

  // Bot 切换处理
  const handleBotSwitch = (botUuid: string) => {
    if (botUuid === driverBot?.bot_uuid) return;
    const selectedBot = myBots.find((b) => b.bot_uuid === botUuid);
    if (selectedBot) {
      // 关闭副屏
      if (window.aixBridge?.closePanelForce) {
        window.aixBridge.closePanelForce();
      }
      clearCurrentGroup();
      // 重置好友相关状态，避免显示上一个 Bot 的数据
      resetFriendStore();
      selectDriverBot(toDriverBot(selectedBot));
      // 通知外部系统
      onBotChange?.({
        bot_uuid: botUuid,
        bot_name: selectedBot.bot_name,
        visibility: selectedBot.visibility,
      });
      if (groupId) {
        history.replace(buildUrlWithStandaloneParams(''));
      }
    }
  };

  // 获取当前选中的 bot UUID
  const selectedBotUuid = driverBot?.bot_uuid || null;

  // 构建当前 Bot 信息（用于 BotInfoCard）
  const currentBotInfo = useMemo(() => {
    const currentMyBot = myBots.find((b) => b.bot_uuid === driverBot?.bot_uuid);
    if (!currentMyBot) return null;
    return {
      bot_uuid: currentMyBot.bot_uuid,
      bot_name: currentMyBot.bot_name,
      avatar_url: currentMyBot.avatar_url,
      visibility: currentMyBot.visibility,
      is_online: currentMyBot.is_online,
      status: currentMyBot.status,
    };
  }, [myBots, driverBot]);

  // 判断当前卡片类型：根据选中 Bot 的 actor_kind
  const currentCardType = useMemo(() => {
    const currentMyBot = myBots.find(
      (b: BotNetworkInfo) => b.bot_uuid === driverBot?.bot_uuid,
    );
    // 判断 Actor 类型：优先使用 actor_kind，否则根据 bot_id/bot_uuid 前缀判断
    const actor_kind =
      currentMyBot?.actor_kind ||
      (currentMyBot?.bot_uuid?.startsWith('human_') ? 'human' : 'bot');
    return actor_kind;
  }, [myBots, driverBot]);

  // 获取当前 Bot 完整信息（用于 LicenceInfo）
  const currentBotDetail = useMemo(() => {
    // 从 storeBots 中找到当前选中的 Bot
    return (
      storeBots.find((b) => b.bot_id === driverBot?.bot_uuid?.split(':')[0]) ||
      null
    );
  }, [storeBots, driverBot]);

  // 使用 Actor Hook
  const { updateActorStatus: updateBotStatus } = useActor();

  // 判断用户是否需要加入 BCN 网络（Human Actor 未初始化）
  const needsBcnInit = useMemo(() => {
    return myBots.some(
      (bot) => bot.actor_kind === 'human' && bot.needsHumanInit,
    );
  }, [myBots]);

  // BCN 加入中状态
  const [isJoiningBcn, setIsJoiningBcn] = useState(false);

  // 处理加入 BCN 网络
  const handleJoinBcn = useCallback(async () => {
    const humanBot = myBots.find(
      (bot) => bot.actor_kind === 'human' && bot.needsHumanInit,
    );
    if (!humanBot) return;

    setIsJoiningBcn(true);
    try {
      const success = await initHumanActor(humanBot.bot_uuid);
      if (success) {
        // 重新初始化 Bot 列表以获取最新状态
        await initUnifiedBotTabs({ localBots: botsToUse });
      }
    } finally {
      setIsJoiningBcn(false);
    }
  }, [myBots, initHumanActor, initUnifiedBotTabs, botsToUse]);

  // 处理 Bot/Actor 状态切换
  const handleBotStatusChange = useCallback(
    async (status: 'online' | 'hidden') => {
      if (!driverBot?.bot_uuid) {
        toast.error('当前没有选中的 Bot');
        return;
      }

      const success = await updateBotStatus(driverBot.bot_uuid, status);
      if (success) {
        // 同步更新本地 store 中对应 bot 的 status 和 is_online
        const updatedIsOnline = computeIsOnline({
          status,
          visibility: driverBot.visibility,
          dynamic_status: driverBot.dynamic_status,
        });
        useBotNetworkStore.getState().updateMyBot(driverBot.bot_uuid, {
          status,
          is_online: updatedIsOnline,
        } as any);
        // 同步更新 driverBot（包括 dynamic_status和is_online）
        const currentDriver = useBotNetworkStore.getState().driverBot;
        if (currentDriver?.bot_uuid === driverBot.bot_uuid) {
          useBotNetworkStore.getState().setDriverBot({
            ...currentDriver,
            status,
            is_online: updatedIsOnline,
          });
        }
      }
    },
    [driverBot?.bot_uuid, updateBotStatus],
  );

  // 处理允许被添加为好友
  const handleAllowAddFriendChange = useCallback(
    async (value: boolean) => {
      if (!driverBot?.bot_uuid) return;

      // private: 不允许被添加
      // protected/public: 允许被添加
      const newVisibility = value ? 'protected' : 'private';
      await setBotVisibility(driverBot.bot_uuid, newVisibility);
    },
    [driverBot?.bot_uuid, setBotVisibility],
  );

  // 处理好友确认选项
  const handleRequireConfirmationChange = useCallback(
    async (value: boolean) => {
      if (!driverBot?.bot_uuid) return;

      // 桌面 Bot 不允许设置「无需确认」，强制保持 protected
      if (!value && currentBotDetail?.bot_type === 'desktop') {
        toast.info('桌面 Bot 不支持「无需确认」');
        return;
      }

      // protected: 允许被添加 + 需要确认
      // public: 允许被添加 + 无需确认
      const newVisibility = value ? 'protected' : 'public';
      await setBotVisibility(driverBot.bot_uuid, newVisibility);
    },
    [driverBot?.bot_uuid, setBotVisibility, currentBotDetail?.bot_type],
  );

  // 渲染聊天内容区域
  const renderChatContent = () => {
    if (currentGroup) {
      // 会话列表存在但未选中会话时，提示用户选择会话
      if (showSessionList && !activeSessionId) {
        return (
          <Empty
            size="lg"
            icon={<MessageSquare />}
            title="请选择一个会话"
            description="从左侧会话列表中选择或创建一个会话开始聊天"
            className="h-full w-full bg-white"
          />
        );
      }

      return (
        <GroupChatPage
          group={currentGroup}
          messages={sessionMessages}
          isLoadingMessages={isLoadingSessionMessages}
          onReloadMessages={handleReloadMessages}
          isMobile={isMobile}
          onRefreshGroup={() => {
            if (currentGroup?.id && driverBot?.bot_uuid) {
              loadGroupDetail(currentGroup.id);
            }
          }}
          needsBcnInit={needsBcnInit}
          onJoinBcn={handleJoinBcn}
          isJoiningBcn={isJoiningBcn}
          activeSession={currentSession}
          onJoinSession={handleJoinSession}
          onLeaveSession={handleLeaveSession}
          onUpdateSessionTitle={handleUpdateSessionTitle}
          isUpdatingSessionTitle={isUpdatingSessionTitle}
          onBackToSessionList={handleBackToSessionList}
          onUpdateSessionMemberMode={handleUpdateSessionMemberMode}
          onRefreshSession={handleRefreshSession}
        />
      );
    }

    return (
      <Empty
        size="lg"
        icon={<Users />}
        title={isMobile ? '暂无协作群' : '选中一个协作群'}
        description={
          isMobile
            ? '点击右上角 + 号创建新协作群'
            : '从左侧列表中选中一个协作群开始讨论'
        }
        className="h-full bg-white"
      />
    );
  };

  // 渲染设置抽屉
  const renderSettingsDrawer = () => {
    const targetGroup = settingsTargetGroup || currentGroup;
    return (
      <>
        {targetGroup && (
          <GroupSettingsDrawer
            open={showSettings}
            onClose={() => {
              setShowSettings(false);
              setSettingsTargetGroup(null);
            }}
            group={targetGroup}
            onDelete={deleteGroup}
          />
        )}
      </>
    );
  };

  // 渲染弹窗
  const renderModals = () => (
    <>
      {/* 创建群组弹窗 */}
      {driverBot && (
        <CreateGroupModal
          open={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSuccess={handleCreateSuccess}
          actorKind={currentCardType}
        />
      )}

      {/* 添加好友弹窗 */}
      <FriendModal
        open={showFriendModal}
        onClose={() => setShowFriendModal(false)}
      />

      <ServiceInvocationSessionModal
        open={showServiceInvocationSessionModal}
        loading={isCreatingSession}
        defaultQuery={serviceInvocationDefaultQuery}
        onClose={() => setShowServiceInvocationSessionModal(false)}
        onConfirm={handleCreateServiceInvocationSession}
      />
    </>
  );

  // BCN 刷新状态处理
  const handleBcnRefresh = useCallback(async () => {
    const bots = await initUnifiedBotTabs({
      localBots: botsToUse,
      targetBotUuid: urlBotUuid || undefined,
    });
    if (bots.length > 0) {
      toast.success('BCN 接入检测成功');
    } else {
      toast.info('暂未检测到 BCN 接入，请确认完成接入步骤后重试');
    }
  }, [initUnifiedBotTabs, botsToUse, urlBotUuid]);

  // 渲染空状态（无 Bot 数据）
  const renderEmptyState = () => {
    // 非 props 嵌入场景（无 externalBots），展示 BCN 接入引导
    if (!externalBots || externalBots.length === 0) {
      return (
        <BcnOnboardingGuide
          onRefresh={handleBcnRefresh}
          isRefreshing={isLoadingMyBots}
        />
      );
    }

    return (
      <div className="flex h-full w-full flex-col bg-white">
        <div className="flex items-center border-b border-slate-100 px-4 py-2">
          <span className="text-sm text-slate-400">暂无 Bot 数据</span>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <Empty
            size="lg"
            icon={<Users />}
            title="需要 Bot 数据"
            description="请确保您有已激活的 Bot，或通过 props 传入 bots 数据"
            className="h-full"
          />
        </div>
      </div>
    );
  };

  // 跟踪是否已展示过主内容（避免搜索等后续操作再显示全局 loading）
  const hasShownMainContent = useRef(false);

  // 判断是否需要显示全局 loading
  // 场景1: 初始化未完成
  // 场景2: 初始化完成但有 Bot 且正在首次加载群组列表（避免显示空状态后又变 loading）
  // 注意：一旦主内容已展示过，后续搜索加载不应再触发全局 loading，应由列表组件内部处理
  const showGlobalLoading =
    !isInitialized ||
    (myBots.length > 0 &&
      isLoadingGroups &&
      groups.length === 0 &&
      !hasShownMainContent.current);

  // 主内容首次展示后，标记不再显示全局 loading
  if (!showGlobalLoading) {
    hasShownMainContent.current = true;
  }

  if (showGlobalLoading) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-white">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-lavender-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-slate-500">正在加载...</p>
        </div>
      </div>
    );
  }

  // 初始化完成但无 Bot 数据（含刷新中）：BCN 路由保持显示 guide，避免 loading 时闪烁主布局
  if (myBots.length === 0) {
    return renderEmptyState();
  }

  // 移动端布局
  if (isMobile) {
    return (
      <div
        className="flex flex-col h-full"
        style={{
          minWidth: isBelowMinWidth ? `${BREAKPOINTS.minWidth}px` : undefined,
        }}
      >
        <MobileHeader
          onMenuClick={() => {
            if (openMobileNav) {
              openMobileNav();
            } else {
              setShowGroupSelector(true);
            }
          }}
          centerContent={
            <TitleSwitcher
              title={currentGroup?.topic || '选择协作群'}
              onClick={() => setShowGroupSelector(true)}
              data-aspm-click="ca114903.da194319"
              data-aspm-desc="GroupChat-选择协作群"
              data-aspm-param={``}
              data-aspm-expo
            />
          }
          rightContent={
            currentGroup ? (
              <button
                type="button"
                onClick={() => setShowSessionDrawer(true)}
                className="flex items-center justify-center w-10 h-10 rounded-lg text-slate-700 active:bg-slate-100 transition-colors"
                aria-label="会话列表"
              >
                <MessageSquare size={20} />
              </button>
            ) : undefined
          }
        />

        {/* BotTabs 独立行 */}
        <div className="w-full overflow-hidden flex-shrink-0">
          <TopNavBar
            myBots={myBots}
            botsToUse={botsToUse}
            selectedBotUuid={selectedBotUuid}
            isLoadingMyBots={isLoadingMyBots}
            onBotSwitch={handleBotSwitch}
          />
        </div>

        <div className="flex-1 overflow-hidden flex min-h-0 relative">
          {renderChatContent()}
          {myBots.length === 0 && (
            <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/60 backdrop-blur-[2px]">
              <div className="flex flex-col items-center gap-3 text-slate-400">
                <Network className="w-8 h-8" />
                <div className="flex flex-col items-center gap-1">
                  <span className="text-sm font-medium text-slate-600">
                    还没有 Bot
                  </span>
                  <span className="text-xs text-center px-4">
                    点击上方「创建 Bot」按钮，将你的 Bot 接入协作网络
                  </span>
                </div>
              </div>
            </div>
          )}
          {myBots.length > 0 && driverBot?.visibility === 'offline' && (
            <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/60 backdrop-blur-[2px]">
              <div className="flex flex-col items-center gap-2 text-slate-400">
                <Network className="w-6 h-6" />
                <span className="text-sm text-center px-4">
                  当前 Bot 尚未加入协作网络，请先在顶部完成入网操作
                </span>
              </div>
            </div>
          )}
        </div>

        {/* 会话设置抽屉（仅在有当前会话时挂载） */}
        {currentSession && (
          <SessionSettingsDrawer
            open={showSessionSettings}
            onClose={() => setShowSessionSettings(false)}
            session={currentSession}
            onDelete={async (sessionId) => {
              if (!driverBot?.bot_uuid) return false;
              return deleteSession(sessionId, driverBot.bot_uuid);
            }}
          />
        )}

        {renderSettingsDrawer()}
        {renderModals()}

        {/* 移动端群聊切换抽屉 */}
        <GroupListPanel
          isMobile
          groups={groups}
          selectedGroupId={groupId}
          isLoading={isLoadingGroups}
          hasMore={hasMoreGroups}
          isLoadingMore={isLoadingMoreGroups}
          onLoadMore={loadMoreGroups}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onGroupClick={handleGroupClick}
          drawerOpen={showGroupSelector}
          onDrawerOpenChange={setShowGroupSelector}
          getBotName={getBotName}
          unreadRequestCount={unreadRequestCount}
          onAddFriend={() => setShowFriendModal(true)}
          onCreateGroup={() => setShowCreateModal(true)}
          onOpenGroupSettings={handleOpenGroupSettings}
          onLoadGroups={() =>
            driverBot?.bot_uuid && loadGroups(driverBot.bot_uuid)
          }
          currentBot={currentBotInfo}
          bot={currentCardType === 'human' ? null : currentBotDetail}
          cardType={currentCardType}
          onBotStatusChange={handleBotStatusChange}
          onAllowAddFriendChange={handleAllowAddFriendChange}
          onRequireConfirmationChange={handleRequireConfirmationChange}
        />

        {/* 移动端会话列表抽屉 */}
        {showSessionList && currentGroup && (
          <SessionListPanel
            sessions={sessions}
            activeSessionId={activeSessionId}
            isLoading={isLoadingSessions}
            hasMore={hasMoreSessions}
            isCreating={isCreatingSession}
            onSelectSession={handleSelectSession}
            onCreateSession={handleCreateSession}
            onLoadMore={handleLoadMoreSessions}
            onSearch={handleSearchSessions}
            onClearSession={clearCurrentSession}
            isMobile
            actorKind={currentCardType}
            onOpenSessionSettings={handleOpenSessionSettings}
            drawerOpen={showSessionDrawer}
            onDrawerOpenChange={setShowSessionDrawer}
          />
        )}
      </div>
    );
  }

  // 桌面端布局
  return (
    <div className="flex flex-col h-full w-full overflow-hidden rounded-b-xl">
      {/* 顶部导航栏 */}
      <TopNavBar
        myBots={myBots}
        botsToUse={botsToUse}
        selectedBotUuid={selectedBotUuid}
        isLoadingMyBots={isLoadingMyBots}
        onBotSwitch={handleBotSwitch}
      />

      {/* 聊天区域：左右布局 */}
      <div className="flex flex-1 min-h-0 relative">
        {/* 左侧：群组列表 */}
        <GroupListPanel
          groups={groups}
          selectedGroupId={groupId}
          isLoading={isLoadingGroups}
          hasMore={hasMoreGroups}
          isLoadingMore={isLoadingMoreGroups}
          onLoadMore={loadMoreGroups}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onGroupClick={handleGroupClick}
          getBotName={getBotName}
          unreadRequestCount={unreadRequestCount}
          onAddFriend={() => setShowFriendModal(true)}
          onCreateGroup={() => setShowCreateModal(true)}
          onOpenGroupSettings={handleOpenGroupSettings}
          onLoadGroups={() =>
            driverBot?.bot_uuid && loadGroups(driverBot.bot_uuid)
          }
          currentBot={currentBotInfo}
          bot={currentCardType === 'human' ? null : currentBotDetail}
          cardType={currentCardType}
          onBotStatusChange={handleBotStatusChange}
          onAllowAddFriendChange={handleAllowAddFriendChange}
          onRequireConfirmationChange={handleRequireConfirmationChange}
          collapsed={groupListCollapsed}
          onCollapsed={setGroupListCollapsed}
        />

        {/* 中间：会话列表（任务协作群和自由聊天群均显示） */}
        {showSessionList && currentGroup && (
          <SessionListPanel
            sessions={sessions}
            activeSessionId={activeSessionId}
            isLoading={isLoadingSessions}
            hasMore={hasMoreSessions}
            isCreating={isCreatingSession}
            onSelectSession={handleSelectSession}
            onCreateSession={handleCreateSession}
            onLoadMore={handleLoadMoreSessions}
            onSearch={handleSearchSessions}
            onClearSession={clearCurrentSession}
            isMobile={false}
            actorKind={currentCardType}
            onOpenSessionSettings={handleOpenSessionSettings}
            collapsed={sessionListCollapsed}
            onCollapsed={setSessionListCollapsed}
          />
        )}

        {/* 右侧：聊天区域 */}
        <div className="flex-1 min-w-0 overflow-hidden">
          {renderChatContent()}
        </div>

        {/* 无 Bot / 未入网 Bot 蒙层：覆盖 TopNavBar 以下整个区域 */}
        {myBots.length === 0 && (
          <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/60 backdrop-blur-[2px]">
            <div className="flex flex-col items-center gap-3 text-slate-400">
              <Network className="w-8 h-8" />
              <div className="flex flex-col items-center gap-1">
                <span className="text-sm font-medium text-slate-600">
                  还没有 Bot
                </span>
                <span className="text-xs text-center px-4">
                  点击上方「创建 Bot」按钮，将你的 Bot 接入协作网络
                </span>
              </div>
            </div>
          </div>
        )}
        {myBots.length > 0 && driverBot?.visibility === 'offline' && (
          <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/60 backdrop-blur-[2px]">
            <div className="flex flex-col items-center gap-2 text-slate-400">
              <Network className="w-6 h-6" />
              <span className="text-sm">
                当前 Bot 尚未加入协作网络，请先在顶部完成入网操作
              </span>
            </div>
          </div>
        )}
      </div>

      {/* 会话设置抽屉（仅在有当前会话时挂载） */}
      {currentSession && (
        <SessionSettingsDrawer
          open={showSessionSettings}
          onClose={() => setShowSessionSettings(false)}
          session={currentSession}
          onDelete={async (sessionId) => {
            if (!driverBot?.bot_uuid) return false;
            return deleteSession(sessionId, driverBot.bot_uuid);
          }}
        />
      )}

      {renderSettingsDrawer()}
      {renderModals()}
    </div>
  );
};

export default GroupChat;
