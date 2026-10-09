// 对话页二级侧栏:搜索 + {用户}管理的 Bot / {用户}的好友 Bot 两组目录。
// 本体(ConversationSidebarContent)与外壳分离:桌面端用 ResizableWorkspaceSidebar,
// <lg 由页面(任务 8)以 Drawer 包裹同一 Content,保证两端一致。
// 组件只消费 Store 状态与同步 setter / Hook 模型回调,不触达 Service。
import { Button, Empty, Input, Skeleton } from '@/components/ui';
import type {
  ConversationBotSection,
  ConversationBotView,
  ConversationSessionListState,
} from '@/domain/conversation/types';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import type { ConversationState } from '@/stores/conversationStoreState';
import { Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { ListErrorState } from '../../components/ListErrorState';
import { ResizableWorkspaceSidebar } from '../../components/ResizableWorkspaceSidebar';
import { ConversationBotItem } from './ConversationBotItem';

export interface ConversationSidebarProps {
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  store: ConversationState;
  directory: ConversationDirectoryModel;
  sessions: ConversationSessionsModel;
  others: ManagedBotOthersModel;
  onOpenSession(botId: string, section: ConversationBotSection, sessionId: string, friendUserId?: string): void;
  onOpenPublicBots(): void;
  onOpenBotWorkshop(): void;
}

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

function filterBots(bots: ConversationBotView[], keyword: string): ConversationBotView[] {
  if (!keyword) return bots;
  return bots.filter((view) => view.bot.displayName.toLowerCase().includes(keyword));
}

function SidebarSection(props: {
  title: string;
  loading: boolean;
  error: string | null;
  onRetry(): void;
  items: ConversationBotView[];
  emptyTitle: string;
  emptyHint: string;
  isSearching: boolean;
  onOpenPublicBots(): void;
  renderItem(view: ConversationBotView): ReactNode;
}) {
  const { title, loading, error, onRetry, items } = props;
  const { emptyTitle, emptyHint, isSearching, onOpenPublicBots, renderItem } = props;
  return (
    <div className="py-2" role="group" aria-label={title}>
      <p className="px-4 pb-1 pt-1 text-xs font-medium text-muted-foreground">{title}</p>
      {loading ? (
        <div className="overflow-hidden">
          {[1, 2, 3].map((i) => (
            <Skeleton.Block key={i} className="h-14 w-full rounded-none" />
          ))}
        </div>
      ) : error ? (
        <ListErrorState message={error} onRetry={onRetry} />
      ) : items.length === 0 ? (
        isSearching ? null : (
          <Empty
            compact
            title={emptyTitle}
            description={emptyHint}
            action={
              <Button variant="secondary" size="sm" onClick={onOpenPublicBots}>
                前往公开 Bot
              </Button>
            }
          />
        )
      ) : (
        items.map(renderItem)
      )}
    </div>
  );
}

/** 管理 Bot 行:归属(缺省 mine)与范围记忆均在 Store,缓存按 origin/scope 命中才展示。 */
function ManagedItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, others, onOpenSession, onOpenBotWorkshop } = props;
  const origin = store.originByManagedBotId[botId] ?? 'mine';
  const scope = store.effectiveScopeByManagedBotId[botId] ?? 'all';
  const cache = store.sessionsByBotId[botId];
  const mineSessions = cache && cache.origin === 'mine' && cache.scope === scope ? cache.sessions : undefined;
  const selectedBot = store.selectedBotId === botId && store.selectedSection === 'managed';
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
            onOpenSession(botId, 'managed', sessionId, friendUserId),
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
      section="managed"
      expanded={store.expandedBotIds[botId] === true}
      selected={selectedBot}
      selectedSessionId={selectedBot ? store.selectedSessionId : null}
      origin={origin}
      scope={scope}
      sessions={mineSessions}
      others={othersArea}
      onToggle={() => sessions.toggleBot(botId, 'managed')}
      onOriginChange={(next) => store.setManagedBotOrigin(botId, next)}
      onScopeChange={(next) => store.setManagedBotScope(botId, next)}
      onCreateSession={() => void sessions.createSession(botId)}
      onSelectSession={(sessionId) => sessions.selectMineSession(botId, sessionId)}
      onLoadMore={() => void sessions.loadMoreSessions(botId, 'managed', scope)}
      onToggleFavorite={(sessionId) => void sessions.favorites.toggleFavorite(botId, 'managed', sessionId)}
      isFavoritePending={(sessionId) => sessions.favorites.isPending(botId, 'managed', sessionId)}
      onOpenBotWorkshop={onOpenBotWorkshop}
    />
  );
}

/** 好友 Bot 行:无归属概念,交互会话一律走好友 Bot 模型回调。 */
function FriendItem({ view, props }: { view: ConversationBotView; props: ConversationSidebarProps }) {
  const botId = view.bot.botId;
  const { store, sessions, onOpenBotWorkshop } = props;
  const selectedBot = store.selectedBotId === botId && store.selectedSection === 'friend';
  return (
    <ConversationBotItem
      bot={view.bot}
      section="friend"
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
      onOpenBotWorkshop={onOpenBotWorkshop}
    />
  );
}

export function ConversationSidebarContent(props: ConversationSidebarProps) {
  const { managedBots, friendBots, directory, onOpenPublicBots, onOpenBotWorkshop } = props;
  const { identity } = useHumanIdentity();
  const userName = identity?.displayName;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const isSearching = keyword.length > 0;
  const filteredManaged = filterBots(managedBots, keyword);
  const filteredFriend = filterBots(friendBots, keyword);
  const managedTitle = userName ? `${userName}管理的 Bot` : '已管理 Bot';
  const friendTitle = userName ? `${userName}的好友 Bot` : '好友 Bot';

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted/20">
        <div className="sticky top-0 z-20 border-b border-border/70 bg-muted/20 pt-1 backdrop-blur-sm">
          <div className="my-2 px-4">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="h-9 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索 Bot"
                aria-label="搜索 Bot"
              />
            </div>
          </div>
          {directory.hasAgentCodingBots && (
            <div className="flex items-center gap-1 px-4 pb-2 text-xs text-muted-foreground">
              <span>AgentCoding Bot 请前往</span>
              <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={onOpenBotWorkshop}>
                Bot 工坊
              </Button>
              <span>使用</span>
            </div>
          )}
        </div>
        {isSearching && filteredManaged.length === 0 && filteredFriend.length === 0 ? (
          <Empty compact title="未找到匹配的 Bot" />
        ) : (
          <>
            <SidebarSection
              title={managedTitle}
              loading={directory.managedLoading && managedBots.length === 0}
              error={directory.managedError}
              onRetry={directory.retryManaged}
              items={filteredManaged}
              emptyTitle="暂无管理的 Bot"
              emptyHint="可前往「公开 Bot」发现并添加 Bot。"
              isSearching={isSearching}
              onOpenPublicBots={onOpenPublicBots}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
            />
            <SidebarSection
              title={friendTitle}
              loading={directory.friendLoading && friendBots.length === 0}
              error={directory.friendError}
              onRetry={directory.retryFriend}
              items={filteredFriend}
              emptyTitle="暂无好友 Bot"
              emptyHint="可前往「公开 Bot」发现并添加好友 Bot。"
              isSearching={isSearching}
              onOpenPublicBots={onOpenPublicBots}
              renderItem={(view) => <FriendItem key={view.bot.botId} view={view} props={props} />}
            />
          </>
        )}
      </div>
    </div>
  );
}

/** 桌面端(≥lg)可拖宽侧栏外壳;<lg 隐藏,由页面用 Drawer 包裹 Content 呈现。 */
export function ConversationSidebar(props: ConversationSidebarProps) {
  return (
    <ResizableWorkspaceSidebar ariaLabel="会话侧栏">
      <ConversationSidebarContent {...props} />
    </ResizableWorkspaceSidebar>
  );
}
