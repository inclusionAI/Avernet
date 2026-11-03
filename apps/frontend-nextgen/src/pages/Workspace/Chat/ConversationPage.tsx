// ConversationPage —— 对话页(/workspace/chat,Task 8 组合页)。
// 组合:useHumanIdentity(Task 4 定式:固定登录 Human 身份,经页边界注入)+
// Task 4 会话 Hook(目录/会话/他人只读/URL 同步)+ Task 6 对话侧栏 +
// 只读面板 + 普通/Coding Bot 两个互斥的 Runtime 边界。
import type { ConversationBotSection } from '@/domain/conversation/types';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { FriendRequestConfirmModal } from '@/components/CollaborationSquare/FriendRequestConfirmModal';
import { history } from '@umijs/max';
import { getCapabilities } from '@/capabilities';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useSpaceContextStore } from '@/stores/spaceContextStore';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ReadOnlyConversationPanel } from '../components/ReadOnlyConversationPanel';
import { ConversationMobileSidebarDrawer } from './components/ConversationMobileSidebarDrawer';
import { ConversationSessionPlaceholder } from './components/ConversationSessionPlaceholder';
import { ConversationSidebar, type ConversationSidebarProps } from './components/ConversationSidebar';
import { CodingBotStageBoundary } from './CodingBotStageBoundary';
import { StandardConversationStageContainer } from './StandardConversationStageContainer';
import { useConversationHistory } from './hooks/useConversationHistory';
import { useConversationIdentityReset } from './hooks/useConversationIdentityReset';
import { useConversationMobileSidebar } from './hooks/useConversationMobileSidebar';
import { useConversationSelection } from './hooks/useConversationSelection';
import { useConversationDirectory } from './hooks/useConversationDirectory';
import { useConversationSessionPlaceholder } from './hooks/useConversationSessionPlaceholder';
import { useConversationSessions } from './hooks/useConversationSessions';
import { useConversationUrlSync } from './hooks/useConversationUrlSync';
import { useConversationDeepLinkSessionHydration } from './hooks/useConversationDeepLinkSessionHydration';
import { useManagedBotOthers } from './hooks/useManagedBotOthers';
import { useLegacyShareLinkGuard } from './hooks/useLegacyShareLinkGuard';

export default function ConversationPage(): JSX.Element {
  const { identity: human } = useHumanIdentity();
  const userId = human?.userId ?? null;

  // 登录用户变化才整仓重置;identityChanged 供切换瞬间停用旧用户会话装配(详见 hook)。
  const { identityChanged } = useConversationIdentityReset(userId);

  const directory = useConversationDirectory(userId);
  // 团队 Bot 独立目录(section=team)与会话模型合并消费:
  // 会话加载/选中/URL 同步等按「管理域全集」(managed + team)装配。
  const managedAndTeamBots = useMemo(
    () => [...directory.managedBots, ...directory.teamBots],
    [directory.managedBots, directory.teamBots],
  );
  const sessions = useConversationSessions({
    userId,
    managedBots: managedAndTeamBots,
    friendBots: directory.friendBots,
  });
  const placeholder = useConversationSessionPlaceholder({
    userId,
    managedBots: managedAndTeamBots,
    friendBots: directory.friendBots,
    sessions,
  });
  const others = useManagedBotOthers({
    userId,
    managedBots: managedAndTeamBots,
    enabled: true,
  });
  const hydrated = !directory.managedLoading && !directory.teamLoading && !directory.friendLoading;
  const { store, selection, origin, interactive, readonly, onRouteSelection, selectReadonlySession } =
    useConversationSelection({
      managedBots: managedAndTeamBots,
      friendBots: directory.friendBots,
      hydrated,
    });
  useConversationUrlSync({ hydrated, selection, onRouteSelection });
  // 老版分享链接落地守护：无好友关系时反查公开 Bot 并唤起好友申请弹窗（plan §2.2）。
  const legacyShareLinkKnownBotIds = useMemo(() => {
    const ids = new Set<string>();
    for (const view of managedAndTeamBots) ids.add(view.bot.botId);
    for (const view of directory.friendBots) ids.add(view.bot.botId);
    return ids;
  }, [managedAndTeamBots, directory.friendBots]);
  const legacyShareLinkFriendBotIds = useMemo(() => {
    const ids = new Set<string>();
    for (const view of directory.friendBots) ids.add(view.bot.botId);
    return ids;
  }, [directory.friendBots]);
  // 列表就绪判据须"非 loading 且无错误"：loading 态缓存一写入对象即存在，若据此创建会话，
  // 在途列表响应返回后将以不含新会话的旧列表覆盖前置缓存，新会话行从左侧消失、选中不可见。
  const legacyShareFriendSessions = store.friendBotSessionsByBotId[store.selectedBotId ?? ''];
  const legacyShareLinkGuard = useLegacyShareLinkGuard({
    hydrated,
    selectedBotId: store.selectedBotId,
    knownBotIds: legacyShareLinkKnownBotIds,
    friendBotIds: legacyShareLinkFriendBotIds,
    createSession: sessions.createSession,
    friendSessionsLoaded: Boolean(
      legacyShareFriendSessions && !legacyShareFriendSessions.loading && !legacyShareFriendSessions.error,
    ),
    selectedSection: store.selectedSection,
    userId,
  });
  useConversationDeepLinkSessionHydration({
    userId,
    managedBots: managedAndTeamBots,
    friendBots: directory.friendBots,
    hydrated,
  });

  // 交互式(mine)装配:身份与选择态全部来自登录 Human。登录用户切换瞬间
  // 停用旧用户会话,防止上一用户的缓存/选中串号。
  const interactiveBot = identityChanged || !userId ? null : interactive.bot;
  const selectedSession = interactive.session;
  const isCodingBot = interactiveBot?.isAgentCodingBot === true;
  const codingBotPreload = getCapabilities().getCodingBotConversationHost().value?.preload;

  // interactive.bot 只有在目标 Session 已解析后才存在，太晚。这里从目录推导当前
  // 选中 Bot，以便在“Bot 已识别、Session 未就绪”时就开始预载 qiankun 入口资产。
  const selectedCodingBot = useMemo(
    () =>
      managedAndTeamBots.find(
        (view) => view.section === store.selectedSection && view.bot.botId === store.selectedBotId,
      )?.bot ?? null,
    [managedAndTeamBots, store.selectedBotId, store.selectedSection],
  );
  const shouldPreloadCodingBot = selectedCodingBot?.isAgentCodingBot === true;

  useEffect(() => {
    if (shouldPreloadCodingBot) codingBotPreload?.();
  }, [codingBotPreload, shouldPreloadCodingBot]);

  // URL 已指定 Bot 但目录仍在解析时不能挂载普通 Runtime;否则 Coding Bot 会在
  // 目录返回前短暂初始化 useBotChat(null)。这个轻量 Stage 只等待目录结论。
  const selectedBotPending = store.selectedBotId !== null && interactiveBot === null;
  const chatBots = useMemo(
    () => [...managedAndTeamBots.map((view) => view.bot), ...directory.friendBots.map((view) => view.bot)],
    [managedAndTeamBots, directory.friendBots],
  );

  // 只读(others)装配:三元组不完整时 Hook 内部只清态不发请求。
  const selectionIsReadonly = origin === 'others' && store.selectedBotId !== null;
  const readonlyHistory = useConversationHistory({
    bot: selectionIsReadonly ? readonly.bot : null,
    friendUserId: selectionIsReadonly ? store.selectedFriendUserId : null,
    sessionId: selectionIsReadonly ? store.selectedSessionId : null,
  });

  // <lg 二级会话列表抽屉开关(桌面收起,对齐旧页行为)。
  const { open: mobileListOpen, setOpen: setMobileListOpen } = useConversationMobileSidebar();

  const handleOpenSession = (
    botId: string,
    section: ConversationBotSection,
    sessionId: string,
    friendUserId?: string,
  ) => {
    if (isManagedConversationSection(section) && friendUserId)
      return selectReadonlySession(botId, sessionId, friendUserId);
    if (section === 'friend') return sessions.selectFriendBotSession(botId, sessionId);
    sessions.selectMineSession(botId, sessionId);
  };
  // #2990 新增「Bot 管理」入口保留：带该 Bot 所属空间上下文跳转 Bot 管理页；Coding Bot
  // 本期不再跳 coding-chat 专用页，而是进入普通会话列表并挂载 embedded Stage。
  const openBotManagement = useCallback((bot: ChatBotView) => {
    const spaceId = bot.spaceId ? Number(bot.spaceId) : undefined;
    if (spaceId !== undefined && Number.isFinite(spaceId)) {
      useSpaceContextStore.getState().setCurrentSpaceId(spaceId);
    }
    history.push('/bot-workshop');
  }, []);
  // #2990 新增“当前会话回复中”状态保留。普通 Runtime 已拆到
  // StandardConversationStageContainer，通过状态回传供侧边栏会话行展示；Coding Bot
  // 子应用暂不透出同构状态，保持 null。
  const [standardChatRequesting, setStandardChatRequesting] = useState(false);
  const handleStandardChatRequestingChange = useCallback((isRequesting: boolean) => {
    setStandardChatRequesting(isRequesting);
  }, []);
  const replyingSessionId = standardChatRequesting ? (selectedSession?.sessionId ?? null) : null;
  const sidebarProps: ConversationSidebarProps = {
    managedBots: directory.managedBots,
    teamBots: directory.teamBots,
    friendBots: directory.friendBots,
    store,
    directory,
    sessions,
    others,
    onOpenSession: handleOpenSession,
    // 好友 Bot 空态文案内「公开 Bot」链接:跳公开 Bot 发现页。
    onOpenPublicBots: () => history.push('/collaboration-square/bots'),
    // 我的 Bot 空态文案内「Bot 管理」链接:跳 Bot 管理页创建 Bot。
    onOpenBotWorkshop: () => history.push('/bot-workshop'),
    onOpenBotManagement: openBotManagement,
    replyingSessionId,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
        <ConversationSidebar {...sidebarProps} />
        {/* <lg 二级会话列表抽屉:与桌面端共用同一 Content,选中即收起。 */}
        <ConversationMobileSidebarDrawer
          sidebarProps={sidebarProps}
          open={mobileListOpen}
          onOpenChange={(open) => setMobileListOpen(open)}
          onOpenSession={(botId, section, sessionId, friendUserId) => {
            handleOpenSession(botId, section, sessionId, friendUserId);
            setMobileListOpen(false);
          }}
        />
        <div className="relative flex h-full min-h-0 min-w-0 flex-1">
          {selectionIsReadonly ? (
            <ReadOnlyConversationPanel
              bot={readonly.bot}
              friend={readonly.friend}
              session={readonly.session}
              messages={readonlyHistory.messages}
              loading={readonlyHistory.loading}
              error={readonlyHistory.error}
              hasMore={readonlyHistory.hasMore}
              isLoadingMore={readonlyHistory.isLoadingMore}
              loadMoreError={readonlyHistory.loadMoreError}
              onRetry={readonlyHistory.retry}
              onLoadMore={() => void readonlyHistory.loadMore()}
              onOpenSessionList={() => setMobileListOpen(true)}
            />
          ) : placeholder ? (
            // 展开 Bot 后目标尚无选中会话:占位(加载/失败/空态/无效指定),
            // 不挂载任何消息 Runtime。
            <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={() => setMobileListOpen(true)} />
          ) : isCodingBot ? (
            selectedSession && (
              <CodingBotStageBoundary bot={interactiveBot} session={selectedSession} human={human} />
            )
          ) : selectedBotPending ? (
            <section aria-label="加载 Bot 会话" className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
              <ChatMessageSkeleton ariaLabel="加载 Bot 会话" />
            </section>
          ) : (
            <StandardConversationStageContainer
              bot={interactiveBot}
              session={selectedSession}
              human={human}
              section={store.selectedSection ?? 'managed'}
              chatBots={chatBots}
              sessions={sessions}
              onOpenSessionList={() => setMobileListOpen(true)}
              onChatRequestingChange={handleStandardChatRequestingChange}
            />
          )}
        </div>
      </div>
      {legacyShareLinkGuard.legacyBot && human && (
        <FriendRequestConfirmModal
          bot={legacyShareLinkGuard.legacyBot}
          actor={{ type: 'human', id: human.userId }}
          actorName={human.displayName || human.userId}
          onClose={legacyShareLinkGuard.onClose}
          onConfirm={legacyShareLinkGuard.onConfirm}
        />
      )}
    </div>
  );
}
