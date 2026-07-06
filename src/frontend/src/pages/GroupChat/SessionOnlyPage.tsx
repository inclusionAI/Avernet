/**
 * SessionOnlyPage - 纯会话聊天页面
 *
 * 仅展示 GroupChatPage 聊天区域，不包含 TopNavBar、GroupListPanel、SessionListPanel。
 * URL 参数：?bot_uuid={bot_uuid}&id={groupId}&session={sessionId}
 * 入口：InviteJoin 加入成功后跳转
 */

import { engineAdapterFactory } from '@/adapters/engine/EngineAdapterFactory';
import { useExt } from '@/capabilities';
import Empty from '@/components/Empty';
import { useBot } from '@/hooks/useBot';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { AppExt } from '@/shell/extension';
import { useBotStore } from '@/stores/botStore';
import { useUserStore } from '@/stores/userStore';
import { history, useSearchParams } from '@umijs/max';
import { AlertTriangle, ChevronLeft } from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import GroupChatPage from './components/GroupChatPage';
import { getBcnActiveOnlyPreference } from './constants';
import { useBotNetwork } from './hooks/useBotNetwork';
import { useGroupChatProviders } from './hooks/useGroupChatProviders';
import { useGroups } from './hooks/useGroups';
import { useGroupSessions } from './hooks/useGroupSessions';
import type { BotTabItem } from './types';

/**
 * 从 useBotStore 的 bots 转换为 BotTabItem[] 格式（内部版专用）
 * 开源版走 /bcnproxy/bots/my，不调此函数。
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

const SessionOnlyPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [searchParams] = useSearchParams();
  const urlBotUuid = searchParams.get('bot_uuid') || '';
  const groupId = searchParams.get('id') || '';
  const sessionId = searchParams.get('session') || '';

  // human 身份收口到 authAdapter（开源走 /me，内部读 __TERN__），写入 userStore
  useHumanIdentity();

  const [isInitialized, setIsInitialized] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  const isInitializingRef = useRef(false);

  const storeUserId = useUserStore((state) => state.userId);

  // Hooks
  // 内部版：调 loadBots + convertBotsToTabItems（/api 路径）
  // 开源版 bcnProxyOnly=true：不调 loadBots，走 /bcnproxy/bots/my
  const { bcnProxyOnly } = useExt(AppExt).features;
  const { loadBots } = useBot({ autoFetchTotalBotCount: false });
  const { initUnifiedBotTabs } = useBotNetwork();
  const { currentGroup, loadGroupDetail } = useGroups();
  const {
    currentSession,
    sessionMessages,
    isLoadingSessionMessages,
    isUpdatingSessionTitle,
    selectSession,
    loadSessionMessages,
    joinSession,
    leaveSession,
    updateSessionTitle,
    updateSessionMemberMode,
    refreshCurrentSession,
  } = useGroupSessions();
  const { disconnectAll } = useGroupChatProviders();

  // 参数校验
  const missingParams = !groupId;

  // 初始化：loadBots（内部版） → initUnifiedBotTabs → loadGroupDetail → selectSession
  // 开源版 bcnProxyOnly=true：不调 loadBots，initUnifiedBotTabs 走 /bcnproxy/bots/my
  useEffect(() => {
    if (isInitialized || isInitializingRef.current || missingParams) return;
    if (!storeUserId) return;

    const initialize = async () => {
      isInitializingRef.current = true;
      try {
        console.log('[SessionOnlyPage] Initializing...', {
          urlBotUuid,
          groupId,
          sessionId,
          bcnProxyOnly,
        });

        let localBotsForInit: BotTabItem[] = [];

        if (!bcnProxyOnly) {
          // 内部版：调 loadBots（/api），再从 store 转 BotTabItem
          await loadBots({ skipAutoActivate: true });
          const latestBots = useBotStore.getState().bots;
          localBotsForInit = convertBotsToTabItems(latestBots);
        }
        // 开源版：localBotsForInit 为空，走 Phase 0.5

        await initUnifiedBotTabs({
          localBots: localBotsForInit,
          targetBotUuid: urlBotUuid || undefined,
          activeOnly: getBcnActiveOnlyPreference(),
        });

        await loadGroupDetail(groupId, { addToGroupsList: true });

        if (sessionId) {
          selectSession(sessionId, {
            addToSessionsList: true,
            viewBotId: urlBotUuid || undefined,
          });
        }

        setIsInitialized(true);
      } catch (err: any) {
        console.error('[SessionOnlyPage] Init failed:', err);
        setInitError(err?.message || '初始化失败');
      } finally {
        isInitializingRef.current = false;
      }
    };

    initialize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInitialized, storeUserId, missingParams]);

  // Cleanup
  useEffect(() => {
    return () => {
      disconnectAll();
    };
  }, [disconnectAll]);

  // 移动端返回
  const handleBack = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    history.push(`/bcn/chat/list?${params.toString()}`);
  }, []);

  // 会话操作回调
  const handleJoinSession = useCallback(
    async (sid: string, actorId: string) => {
      return joinSession(sid, actorId);
    },
    [joinSession],
  );

  const handleLeaveSession = useCallback(
    async (sid: string, actorId: string) => {
      return leaveSession(sid, actorId);
    },
    [leaveSession],
  );

  const handleUpdateSessionTitle = useCallback(
    async (sid: string, title: string) => {
      return updateSessionTitle(sid, title);
    },
    [updateSessionTitle],
  );

  const handleUpdateSessionMemberMode = useCallback(
    async (sid: string, actorId: string, mode: 'auto' | 'muted') => {
      return updateSessionMemberMode(sid, actorId, mode);
    },
    [updateSessionMemberMode],
  );

  const handleRefreshSession = useCallback(
    async (sid: string) => {
      await refreshCurrentSession(sid);
    },
    [refreshCurrentSession],
  );

  // 手动重连时重新拉取会话消息(无 cursor,不清空,整体替换为后端最新历史)
  const handleReloadMessages = useCallback(
    async (sid: string) => {
      await loadSessionMessages(sid, urlBotUuid || undefined);
    },
    [loadSessionMessages, urlBotUuid],
  );

  const handleRefreshGroup = useCallback(() => {
    if (groupId) {
      loadGroupDetail(groupId);
    }
  }, [groupId, loadGroupDetail]);

  // 参数缺失
  if (missingParams) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-white">
        <Empty
          size="lg"
          icon={<AlertTriangle />}
          title="缺少必要参数"
          description="URL 中缺少 id（群组ID）参数"
        />
      </div>
    );
  }

  // 初始化错误
  if (initError) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-white">
        <Empty
          size="lg"
          icon={<AlertTriangle />}
          title="加载失败"
          description={initError}
        />
      </div>
    );
  }

  // Loading
  if (!isInitialized) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-white">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-lavender-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-slate-500">正在加载会话...</p>
        </div>
      </div>
    );
  }

  // 群组未找到
  if (!currentGroup) {
    return (
      <div className="flex items-center justify-center h-full w-full bg-white">
        <Empty
          size="lg"
          icon={<AlertTriangle />}
          title="群组不存在"
          description="无法加载指定的群组信息"
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full overflow-hidden">
      {/* 移动端返回按钮 */}
      {isMobile && (
        <header className="h-12 flex items-center px-3 border-b border-slate-200/60 bg-white shrink-0">
          <button
            type="button"
            onClick={handleBack}
            className="flex items-center justify-center w-10 h-10 -ml-2 rounded-lg text-slate-600 active:bg-slate-100"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <span className="flex-1 text-center text-sm font-medium text-slate-800 truncate pr-10">
            {currentGroup.topic || '会话'}
          </span>
        </header>
      )}

      {/* 聊天区域 */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <GroupChatPage
          group={currentGroup}
          messages={sessionMessages}
          isLoadingMessages={isLoadingSessionMessages}
          onReloadMessages={handleReloadMessages}
          isMobile={isMobile}
          onRefreshGroup={handleRefreshGroup}
          activeSession={currentSession}
          onJoinSession={handleJoinSession}
          onLeaveSession={handleLeaveSession}
          onUpdateSessionTitle={handleUpdateSessionTitle}
          isUpdatingSessionTitle={isUpdatingSessionTitle}
          onUpdateSessionMemberMode={handleUpdateSessionMemberMode}
          onRefreshSession={handleRefreshSession}
          onBackToSessionList={handleBack}
        />
      </div>
    </div>
  );
};

export default SessionOnlyPage;
