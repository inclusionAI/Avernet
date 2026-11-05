import { getCapabilities } from '@/capabilities';
import type { ConversationBotView, ConversationSessionListState } from '@/domain/conversation/types';
import { ConversationBotItem } from './ConversationBotItem';
import type { ConversationSidebarProps } from './ConversationSidebar';

const EMPTY_SESSION_LIST: ConversationSessionListState = {
  items: [],
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
};

const CODING_BOT_SESSION_TITLE_MAX_LENGTH = 32;

function codingBotSessionFeatures(bot: ConversationBotView['bot']) {
  return bot.isAgentCodingBot && getCapabilities().getCodingBotConversationHost().value?.runSessionAction
    ? {
        share: true as const,
        logs: true as const,
        dima: true as const,
        titleMaxLength: CODING_BOT_SESSION_TITLE_MAX_LENGTH,
      }
    : undefined;
}

/** 管理 Bot 行:归属(缺省 mine)与范围记忆均在 Store,缓存按 origin/scope 命中才展示。 */
export function ManagedItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, others, onOpenSession, onOpenBotManagement, replyingSessionId } = props;
  const origin = store.originByManagedBotId[botId] ?? 'mine';
  const scope = store.effectiveScopeByManagedBotId[botId] ?? 'all';
  const cache = store.sessionsByBotId[botId];
  const flatCodingOthers = view.bot.isAgentCodingBot === true && origin === 'others';
  const visibleSessions =
    cache && cache.origin === origin && cache.scope === scope && (origin === 'mine' || flatCodingOthers)
      ? cache.sessions
      : undefined;
  const selectedBot = store.selectedBotId === botId && store.selectedSection === view.section;
  // Browse-only managed/team Coding Bot 只能查看列表：已有 Session mutation 必须等
  // 目标 Bot Runtime/controller ready，入口先隐藏。Bot 行 + 是显式激活入口，保留可点。
  const hideCodingSessionActions = view.bot.isAgentCodingBot === true && !selectedBot;
  const othersArea =
    cache && cache.origin === 'others'
      ? {
          directory: cache.friendDirectory,
          hasCache: true,
          friendGroups: cache.friendDirectory.items.map(
            (user) =>
              cache.friendGroups[user.userId] ?? {
                friend: user,
                state: 'unloaded' as const,
                sessions: EMPTY_SESSION_LIST,
                expanded: false,
              },
          ),
          expandedFriendUserIds: store.expandedFriendUserIdsByBotId[botId] ?? {},
          selectedSessionId: selectedBot ? store.selectedSessionId : null,
          onToggleFriend: (friendUserId: string) => others.toggleFriend(botId, friendUserId),
          onSelectSession: (sessionId: string, friendUserId: string) =>
            onOpenSession(botId, view.section, sessionId, friendUserId),
          onRetryDirectory: () => others.retryDirectory(botId),
          onRetryFriendSessions: (friendUserId: string) => others.retryFriendSessions(botId, friendUserId),
          onLoadMoreFriendSessions: (friendUserId: string) => {
            void others.loadMoreFriendSessions(botId, friendUserId);
          },
        }
      : undefined;
  return (
    <ConversationBotItem
      bot={view.bot}
      section={view.section}
      sessionActions={
        hideCodingSessionActions
          ? undefined
          : {
              run: (sessionId: string, action: Parameters<typeof sessions.actions.run>[3]) =>
                sessions.actions.run(botId, view.section, sessionId, action),
              pending: sessions.actions.isPending(botId, view.section),
              features: codingBotSessionFeatures(view.bot),
            }
      }
      expanded={store.expandedBotIds[botId] === true}
      selectedSessionId={selectedBot ? store.selectedSessionId : null}
      botSelected={selectedBot}
      origin={origin}
      scope={scope}
      sessionKey={store.sessionKeyByBotId[botId] ?? ''}
      sessions={visibleSessions}
      others={othersArea}
      flatCodingOthers={flatCodingOthers}
      onToggle={() => sessions.toggleBot(botId, view.section)}
      onOriginChange={(next) => store.setManagedBotOrigin(botId, next)}
      onScopeChange={(next) => store.setManagedBotScope(botId, next)}
      onSearchSession={(nextKey) => {
        // 提交只落该 Bot 的过滤并展开其列表(mine/others 均按新条件重拉);不制造主舞台选中。
        store.setSessionKeyFilter(botId, nextKey);
        store.setExpandedBot(botId, true);
      }}
      createSessionPending={sessions.isCreatingSession(botId)}
      onCreateSession={() => void sessions.createSession(botId)}
      onSelectSession={(sessionId) => sessions.selectMineSession(botId, sessionId)}
      onLoadMore={() => void sessions.loadMoreSessions(botId, view.section, scope)}
      onToggleFavorite={
        hideCodingSessionActions
          ? undefined
          : (sessionId: string) => void sessions.favorites.toggleFavorite(botId, view.section, sessionId)
      }
      isFavoritePending={(sessionId) => sessions.favorites.isPending(botId, view.section, sessionId)}
      onOpenBotManagement={onOpenBotManagement}
      replyingSessionId={replyingSessionId}
    />
  );
}

/** 好友 Bot 行:无归属概念,交互会话一律走好友 Bot 模型回调;会话范围读 scopeByFriendBotId。 */
export function FriendItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, replyingSessionId } = props;
  const scope = store.scopeByFriendBotId[botId] ?? 'all';
  const selectedBot = store.selectedBotId === botId && store.selectedSection === 'friend';
  return (
    <ConversationBotItem
      bot={view.bot}
      section="friend"
      sessionActions={{
        run: (sessionId, action) => sessions.actions.run(botId, 'friend', sessionId, action),
        pending: sessions.actions.isPending(botId, 'friend'),
        features: codingBotSessionFeatures(view.bot),
      }}
      expanded={store.expandedBotIds[botId] === true}
      selectedSessionId={selectedBot ? store.selectedSessionId : null}
      botSelected={selectedBot}
      origin="mine"
      scope={scope}
      sessionKey={store.sessionKeyByBotId[botId] ?? ''}
      sessions={store.friendBotSessionsByBotId[botId]}
      onToggle={() => sessions.toggleBot(botId, 'friend')}
      onOriginChange={() => undefined}
      onScopeChange={(next) => store.setFriendBotScope(botId, next)}
      onSearchSession={(nextKey) => {
        // 提交只落好友 Bot 的过滤并展开其列表;不制造主舞台选中。
        store.setSessionKeyFilter(botId, nextKey);
        store.setExpandedBot(botId, true);
      }}
      onCreateSession={() => void sessions.createSession(botId)}
      onSelectSession={(sessionId) => sessions.selectFriendBotSession(botId, sessionId)}
      onLoadMore={() => void sessions.loadMoreSessions(botId, 'friend', scope)}
      onToggleFavorite={(sessionId) => void sessions.favorites.toggleFavorite(botId, 'friend', sessionId)}
      isFavoritePending={(sessionId) => sessions.favorites.isPending(botId, 'friend', sessionId)}
      replyingSessionId={replyingSessionId}
    />
  );
}
