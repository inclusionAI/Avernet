import { Button, Empty, Input, Segmented, Skeleton } from '@/components/ui';
import { WorkspaceIdentitySelector } from '@/components/Workspace/IdentitySelector';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { ChevronDown, ChevronRight, Search } from 'lucide-react';
import { useState } from 'react';
import { WorkspaceActionButton } from '../WorkspaceActionButton';
import { BotItem } from './BotItem';

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
  friendBots: ChatBotView[];
  isMyBotsLoading: boolean;
  isFriendBotsLoading: boolean;
  expandedBotIds: Record<string, true>;
  expandedBotSectionKey: Record<string, string>;
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  isSessionsLoading: boolean;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string, sectionKey?: string) => void;
  onSelectSession: (botId: string, sessionId: string) => void;
  onCreateSession: (botId: string) => void;
  onDeleteSession: (botId: string, sessionId: string) => Promise<boolean>;
  onRenameSession: (botId: string, sessionId: string, title: string) => Promise<boolean>;
  onClearSessionContext: (botId: string, sessionId: string) => Promise<boolean>;
  onToggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  onLoadFavorites: (botId: string) => Promise<void>;
  onCreateGroup: () => void;
  onAddFriend: () => void;
}

interface BotListSectionProps {
  title: string;
  sectionKey: string;
  count: number;
  bots: ChatBotView[];
  isLoading?: boolean;
  defaultCollapsed?: boolean;
  expandedBotIds: Record<string, true>;
  expandedBotSectionKey: Record<string, string>;
  sessionsByBotId: Record<string, BotChatSessionView[]>;
  selectedBotSessionId: string | null;
  onToggleBotExpanded: (botId: string, sectionKey?: string) => void;
  onSelectSession: (botId: string, sessionId: string) => void;
  onCreateSession: (botId: string) => void;
  onDeleteSession: (botId: string, sessionId: string) => Promise<boolean>;
  onRenameSession: (botId: string, sessionId: string, title: string) => Promise<boolean>;
  onClearSessionContext: (botId: string, sessionId: string) => Promise<boolean>;
  onToggleFavorite: (botId: string, sessionId: string) => Promise<boolean>;
  onLoadFavorites: (botId: string) => Promise<void>;
}

function BotListSection({
  title,
  sectionKey,
  count,
  bots,
  isLoading,
  defaultCollapsed,
  expandedBotIds,
  expandedBotSectionKey,
  sessionsByBotId,
  selectedBotSessionId,
  onToggleBotExpanded,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  onClearSessionContext,
  onToggleFavorite,
  onLoadFavorites,
}: BotListSectionProps) {
  const [collapsed, setCollapsed] = useState(!!defaultCollapsed);

  return (
    <div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        aria-controls={`bot-section-${sectionKey}`}
        className="mb-1 flex w-full items-center gap-1 rounded-md px-1 py-1 text-xs font-medium text-foreground hover:bg-primary/5"
      >
        {collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="flex-1 text-left">
          {title} <span className="text-muted-foreground">({count})</span>
        </span>
      </Button>
      {!collapsed && (
        <div id={`bot-section-${sectionKey}`}>
          {isLoading ? (
            <div className="overflow-hidden rounded-lg border border-border bg-card">
              {[1, 2].map((i) => (
                <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
              ))}
            </div>
          ) : bots.length === 0 ? (
            <div className="flex min-h-[80px] items-center justify-center py-4">
              <span className="text-xs text-muted-foreground">暂无 Bot</span>
            </div>
          ) : (
            <div className="space-y-2">
              {bots.map((bot) => (
                <BotItem
                  key={`${sectionKey}:${bot.botId}`}
                  bot={bot}
                  expanded={!!expandedBotIds[bot.botId] && expandedBotSectionKey[bot.botId] === sectionKey}
                  sessions={sessionsByBotId[bot.botId]}
                  selectedBotSessionId={selectedBotSessionId}
                  onToggleBotExpanded={(botId) => onToggleBotExpanded(botId, sectionKey)}
                  onSelectSession={onSelectSession}
                  onCreateSession={onCreateSession}
                  onDeleteSession={onDeleteSession}
                  onRenameSession={onRenameSession}
                  onClearSessionContext={onClearSessionContext}
                  onToggleFavorite={onToggleFavorite}
                  onLoadFavorites={onLoadFavorites}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
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
    friendBots,
    isMyBotsLoading,
    isFriendBotsLoading,
    expandedBotIds,
    expandedBotSectionKey,
    sessionsByBotId,
    isSessionsLoading,
    selectedBotSessionId,
    onToggleBotExpanded,
    onSelectSession,
    onCreateSession,
    onDeleteSession,
    onRenameSession,
    onClearSessionContext,
    onToggleFavorite,
    onLoadFavorites,
    onCreateGroup,
    onAddFriend,
  } = props;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const filteredMine = chatBots.filter((b) => !keyword || b.displayName.toLowerCase().includes(keyword));
  const filteredFriends = friendBots.filter((b) => !keyword || b.displayName.toLowerCase().includes(keyword));
  const activeIdentityName = identities.find((identity) => identity.id === activeIdentityId)?.name;
  const managedBotTitle = activeIdentityName ? `${activeIdentityName}管理的 Bot` : '已管理 Bot';
  const friendBotTitle = activeIdentityName ? `${activeIdentityName}的好友 Bot` : '好友 Bot';
  const showViewSwitch = availableViews && availableViews.length > 0 && view && onViewChange;
  const tabOptions = (availableViews ?? []).map((v) => ({ value: v, label: v === 'chat' ? '对话' : '协作群' }));
  const sectionProps = {
    expandedBotIds,
    expandedBotSectionKey,
    sessionsByBotId,
    selectedBotSessionId,
    onToggleBotExpanded,
    onSelectSession,
    onCreateSession,
    onDeleteSession,
    onRenameSession,
    onClearSessionContext,
    onToggleFavorite,
    onLoadFavorites,
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 pb-2">
        <WorkspaceIdentitySelector
          identities={identities}
          activeId={activeIdentityId}
          onChange={onChangeIdentity}
          onOpenPermissions={onOpenPermissions}
          userAvatarUrl={userAvatarUrl}
        />
      </div>
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        {showViewSwitch && (
          <div className="mb-2 flex items-center gap-2">
            <Segmented<WorkspaceView>
              className="h-9 min-w-0 flex-1 border border-border/70 bg-muted/70"
              activeOptionClassName="bg-primary/10 font-medium text-primary shadow-none hover:bg-primary/15 hover:text-primary"
              inactiveOptionClassName="bg-background/60 text-foreground/80 hover:bg-background hover:text-foreground"
              value={view as WorkspaceView}
              options={tabOptions}
              onChange={onViewChange}
            />
            <WorkspaceActionButton onAddFriend={onAddFriend} onCreateGroup={onCreateGroup} />
          </div>
        )}
        <div className="my-2 px-1">
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
          <div className="overflow-hidden rounded-lg border border-border bg-card">
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
            ))}
          </div>
        ) : filteredMine.length === 0 && filteredFriends.length === 0 ? (
          <Empty compact title="暂无可协作的 Bot" description="可在「添加好友」中搜索并添加 Bot。" />
        ) : (
          <div className="space-y-2">
            {filteredMine.length > 0 && (
              <BotListSection
                title={managedBotTitle}
                sectionKey="mine"
                count={filteredMine.length}
                bots={filteredMine}
                isLoading={isMyBotsLoading}
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
    <aside className="hidden w-[340px] shrink-0 flex-col overflow-hidden border-r border-border bg-muted/30 p-2 lg:flex">
      <BotSessionList {...props} />
    </aside>
  );
}
