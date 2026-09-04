import { Button, Empty, Input, Skeleton } from '@/components/ui';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { ArrowRight, Search } from 'lucide-react';
import { useState } from 'react';
import type { BotSessionPageMeta } from '../../hooks/useBotSessionMap';
import { WorkspaceActionButton } from '../WorkspaceActionButton';
import { WorkspacePrimaryTabs } from '../WorkspacePrimaryTabs';
import { BotListSection } from './BotListSection';

export interface BotSessionSidebarProps {
  view?: WorkspaceView;
  onViewChange?: (v: WorkspaceView) => void;
  availableViews?: WorkspaceView[];
  identities?: Identity[];
  activeIdentityId?: string | null;
  onChangeIdentity?: (id: string) => void;
  onOpenPermissions?: () => void;
  userAvatarUrl?: string;
  chatBots: ChatBotView[];
  hasAgentCodingBots?: boolean;
  friendBots: ChatBotView[];
  isMyBotsLoading: boolean;
  isFriendBotsLoading: boolean;
  expandedBotIds: Record<string, true>;
  expandedBotSectionKey: Record<string, string>;
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  favoriteSessionsByBotId?: Record<string, BotChatSessionView[]>;
  sessionPageMetaByBotId?: Record<string, BotSessionPageMeta>;
  favoriteSessionPageMetaByBotId?: Record<string, BotSessionPageMeta>;
  isSessionsLoading: boolean;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string, sectionKey?: string) => void;
  onSelectBot?: (bot: ChatBotView) => void;
  onSelectSession: (botId: string, sessionId: string) => void;
  onCreateSession: (botId: string) => void;
  onDeleteSession: (botId: string, sessionId: string) => Promise<boolean>;
  onRenameSession: (botId: string, sessionId: string, title: string) => Promise<boolean>;
  onClearSessionContext: (botId: string, sessionId: string) => Promise<boolean>;
  onToggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  onLoadFavorites: (botId: string) => Promise<void>;
  onLoadMoreSessions?: (botId: string, mode: 'all' | 'favorite') => Promise<void>;
  onManageBot?: (bot: ChatBotView) => void;
  onOpenBotWorkshop?: () => void;
  onCreateGroup: () => void;
  onAddFriend: () => void;
}

/** 二级会话列表内容本体（不含 <aside> 外壳）。由内流 BotSessionSidebar 与 <lg 抽屉复用，保证两处一致。 */
export function BotSessionList(props: BotSessionSidebarProps) {
  const {
    view,
    onViewChange,
    availableViews,
    identities = [],
    activeIdentityId = null,
    onChangeIdentity = () => {},
    onOpenPermissions = () => {},
    userAvatarUrl,
    chatBots,
    hasAgentCodingBots = false,
    friendBots,
    isMyBotsLoading,
    isFriendBotsLoading,
    expandedBotIds,
    expandedBotSectionKey,
    sessionsByBotId,
    favoriteSessionsByBotId,
    sessionPageMetaByBotId,
    favoriteSessionPageMetaByBotId,
    isSessionsLoading,
    selectedBotSessionId,
    onToggleBotExpanded,
    onSelectBot = () => {},
    onSelectSession,
    onCreateSession,
    onDeleteSession,
    onRenameSession,
    onClearSessionContext,
    onToggleFavorite,
    onLoadFavorites,
    onLoadMoreSessions,
    onManageBot,
    onOpenBotWorkshop = () => {},
    onCreateGroup,
    onAddFriend,
  } = props;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const filteredMine = chatBots.filter((b) => !keyword || b.displayName.toLowerCase().includes(keyword));
  const filteredFriends = friendBots.filter((b) => !keyword || b.displayName.toLowerCase().includes(keyword));
  const activeIdentity = identities.find((identity) => identity.id === activeIdentityId);
  const activeIdentityName = activeIdentity?.name;
  const isUserIdentity = activeIdentity?.kind === 'user';
  const managedBotTitle = activeIdentityName ? `${activeIdentityName}管理的 Bot` : '已管理 Bot';
  const friendBotTitle = activeIdentityName ? `${activeIdentityName}的好友 Bot` : '好友 Bot';
  const showViewSwitch = availableViews && availableViews.length > 0 && view && onViewChange;
  const sectionProps = {
    expandedBotIds,
    expandedBotSectionKey,
    sessionsByBotId,
    favoriteSessionsByBotId,
    sessionPageMetaByBotId,
    favoriteSessionPageMetaByBotId,
    selectedBotSessionId,
    onToggleBotExpanded,
    onSelectBot,
    onSelectSession,
    onCreateSession,
    onDeleteSession,
    onRenameSession,
    onClearSessionContext,
    onToggleFavorite,
    onLoadFavorites,
    onLoadMoreSessions,
    onManageBot,
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-[18px] pb-3 pt-4">
        <WorkspaceIdentitySelector
          identities={identities}
          activeId={activeIdentityId}
          onChange={onChangeIdentity}
          onOpenPermissions={onOpenPermissions}
          userAvatarUrl={userAvatarUrl}
          layout="sidebar"
        />
      </div>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted">
        {showViewSwitch && (
          <div className="sticky top-0 z-20 mb-2 flex items-center gap-2 bg-muted px-[18px]">
            <WorkspacePrimaryTabs
              value={view as WorkspaceView}
              options={availableViews ?? []}
              onChange={onViewChange}
            />
            <WorkspaceActionButton onAddFriend={onAddFriend} onCreateGroup={onCreateGroup} />
          </div>
        )}
        <div className="my-2 px-[18px]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-9"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索 Bot 名称"
              aria-label="搜索 Bot"
            />
          </div>
        </div>
        {(isSessionsLoading || isMyBotsLoading) && chatBots.length === 0 && friendBots.length === 0 ? (
          <div className="overflow-hidden border-y border-border bg-background">
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
            ))}
          </div>
        ) : !isUserIdentity && filteredMine.length === 0 && filteredFriends.length === 0 ? (
          <Empty compact title="暂无可协作的 Bot" description="可在「添加好友」中搜索并添加 Bot。" />
        ) : (
          <div>
            {(isUserIdentity || filteredMine.length > 0) && (
              <BotListSection
                title={managedBotTitle}
                sectionKey="mine"
                count={filteredMine.length}
                bots={filteredMine}
                isLoading={isMyBotsLoading}
                footer={
                  hasAgentCodingBots ? (
                    <Button
                      variant="ghost"
                      className="group mx-[18px] mb-3 mt-3 flex h-auto w-[calc(100%-36px)] cursor-pointer items-center justify-between gap-3 rounded-md border border-primary/20 bg-primary/5 px-3 py-2.5 text-left text-foreground transition-colors hover:bg-primary/10"
                      onClick={onOpenBotWorkshop}
                    >
                      <span className="text-xs font-medium leading-5 text-foreground">
                        AgentCoding Bot 请前往 Bot 工坊使用
                      </span>
                      <ArrowRight
                        className="h-4 w-4 shrink-0 text-foreground/70 transition-colors group-hover:text-primary"
                        aria-hidden="true"
                      />
                    </Button>
                  ) : null
                }
                {...sectionProps}
              />
            )}
            <BotListSection
              title={friendBotTitle}
              sectionKey="friend"
              count={filteredFriends.length}
              bots={filteredFriends}
              isLoading={isFriendBotsLoading}
              {...sectionProps}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/** 内流会话列表外壳。≥lg 在流内；<lg hidden，由 Workspace 抽屉呈现同一 BotSessionList。 */
export function BotSessionSidebar(props: BotSessionSidebarProps) {
  return (
    <aside className="hidden w-[360px] shrink-0 flex-col overflow-hidden border-r border-border bg-muted/20 lg:flex">
      <BotSessionList {...props} />
    </aside>
  );
}
