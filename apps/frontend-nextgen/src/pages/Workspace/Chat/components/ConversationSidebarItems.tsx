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

/** 管理 Bot 行:归属(缺省 mine)与范围记忆均在 Store,缓存按 origin/scope 命中才展示。 */
export function ManagedItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, others, onOpenSession, onOpenAgentCodingBot } = props;
  const origin = store.originByManagedBotId[botId] ?? 'mine';
  const scope = store.effectiveScopeByManagedBotId[botId] ?? 'all';
  const cache = store.sessionsByBotId[botId];
  const mineSessions = cache && cache.origin === 'mine' && cache.scope === scope ? cache.sessions : undefined;
  const selectedBot = store.selectedBotId === botId && store.selectedSection === view.section;
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
      sessionActions={{
        run: (sessionId, action) => sessions.actions.run(botId, view.section, sessionId, action),
        pending: sessions.actions.isPending(botId, view.section),
      }}
      expanded={store.expandedBotIds[botId] === true}
      selected={selectedBot}
      selectedSessionId={selectedBot ? store.selectedSessionId : null}
      origin={origin}
      scope={scope}
      sessions={mineSessions}
      others={othersArea}
      onToggle={() => sessions.toggleBot(botId, view.section)}
      onOriginChange={(next) => store.setManagedBotOrigin(botId, next)}
      onScopeChange={(next) => store.setManagedBotScope(botId, next)}
      onCreateSession={() => void sessions.createSession(botId)}
      onSelectSession={(sessionId) => sessions.selectMineSession(botId, sessionId)}
      onLoadMore={() => void sessions.loadMoreSessions(botId, view.section, scope)}
      onToggleFavorite={(sessionId) => void sessions.favorites.toggleFavorite(botId, view.section, sessionId)}
      isFavoritePending={(sessionId) => sessions.favorites.isPending(botId, view.section, sessionId)}
      onOpenAgentCodingBot={onOpenAgentCodingBot}
    />
  );
}

/** 好友 Bot 行:无归属概念,交互会话一律走好友 Bot 模型回调。 */
export function FriendItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, onOpenAgentCodingBot } = props;
  const selectedBot = store.selectedBotId === botId && store.selectedSection === 'friend';
  return (
    <ConversationBotItem
      bot={view.bot}
      section="friend"
      sessionActions={{
        run: (sessionId, action) => sessions.actions.run(botId, 'friend', sessionId, action),
        pending: sessions.actions.isPending(botId, 'friend'),
      }}
      expanded={store.expandedBotIds[botId] === true}
      selected={selectedBot}
      selectedSessionId={selectedBot ? store.selectedSessionId : null}
      origin="mine"
      scope="all"
      sessions={store.friendBotSessionsByBotId[botId]}
      onToggle={() => sessions.toggleBot(botId, 'friend')}
      onOriginChange={() => undefined}
      onScopeChange={() => undefined}
      onCreateSession={() => void sessions.createSession(botId)}
      onSelectSession={(sessionId) => sessions.selectFriendBotSession(botId, sessionId)}
      onLoadMore={() => void sessions.loadMoreSessions(botId, 'friend', 'all')}
      onToggleFavorite={(sessionId) => void sessions.favorites.toggleFavorite(botId, 'friend', sessionId)}
      isFavoritePending={(sessionId) => sessions.favorites.isPending(botId, 'friend', sessionId)}
      onOpenAgentCodingBot={onOpenAgentCodingBot}
    />
  );
}
