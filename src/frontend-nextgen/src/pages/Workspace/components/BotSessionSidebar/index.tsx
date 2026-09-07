import { Button, Empty, Input, Skeleton } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import type { Identity } from '@/services/workspace/workspaceModel';
import { Info, Search } from 'lucide-react';
import { useState } from 'react';
import type { BotSessionPageMeta } from '../../hooks/useBotSessionMap';
import { ResizableWorkspaceSidebar } from '../ResizableWorkspaceSidebar';
import { WorkspacePrimaryTabs } from '../WorkspacePrimaryTabs';
import { WorkspaceSidebarCollapsedRail } from '../WorkspaceSidebarCollapsedRail';
import { BotListSection } from './BotListSection';

export interface BotSessionSidebarProps {
  view?: WorkspaceView;
  onViewChange?: (v: WorkspaceView) => void;
  availableViews?: WorkspaceView[];
  identities?: Identity[];
  activeIdentityId?: string | null;
  chatBots: ChatBotView[];
  hasAgentCodingBots?: boolean;
  friendBots: ChatBotView[];
  isMyBotsLoading: boolean;
  myBotsError?: string | null;
  onRetryMyBots?: () => void;
  isFriendBotsLoading: boolean;
  friendBotsError?: string | null;
  onRetryFriendBots?: () => void;
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
  onReloadBot?: (botId: string) => Promise<void>;
  onOpenBotWorkshop?: () => void;
  onOpenPublicBots: () => void;
}

/** 二级会话列表内容本体（不含 <aside> 外壳）。由内流 BotSessionSidebar 与 <lg 抽屉复用，保证两处一致。 */
export function BotSessionList(props: BotSessionSidebarProps) {
  const {
    view,
    onViewChange,
    availableViews,
    identities = [],
    activeIdentityId = null,
    chatBots,
    hasAgentCodingBots = false,
    friendBots,
    isMyBotsLoading,
    myBotsError,
    onRetryMyBots,
    isFriendBotsLoading,
    friendBotsError,
    onRetryFriendBots,
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
    onReloadBot,
    onOpenBotWorkshop,
    onOpenPublicBots,
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
    onReloadBot,
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted/20">
        <div className="sticky top-0 z-20 border-b border-border/70 bg-muted/20 pt-1 backdrop-blur-sm">
          {showViewSwitch && (
            <div className="flex h-10 items-center gap-2 px-[18px]">
              <WorkspacePrimaryTabs
                value={view as WorkspaceView}
                options={availableViews ?? []}
                onChange={onViewChange}
              />
            </div>
          )}
          <div className="my-2 px-[18px]">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="h-9 pl-9"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索 Bot 名称"
                aria-label="搜索 Bot"
              />
            </div>
          </div>
        </div>
        {(isSessionsLoading || isMyBotsLoading) && chatBots.length === 0 && friendBots.length === 0 ? (
          <div className="overflow-hidden border-y border-border bg-background">
            {[1, 2, 3].map((i) => (
              <Skeleton.Block key={i} className="h-14 w-full rounded-none border-b border-border last:border-b-0" />
            ))}
          </div>
        ) : !isUserIdentity && filteredMine.length === 0 && filteredFriends.length === 0 ? (
          <Empty
            compact
            title="暂无可协作的 Bot"
            description="可前往「公开 Bot」发现并添加 Bot。"
            action={
              <Button variant="secondary" size="sm" onClick={onOpenPublicBots}>
                前往公开 Bot
              </Button>
            }
          />
        ) : (
          <div>
            {(isUserIdentity || filteredMine.length > 0) && (
              <BotListSection
                title={managedBotTitle}
                sectionKey="mine"
                count={filteredMine.length}
                bots={filteredMine}
                isLoading={isMyBotsLoading}
                error={myBotsError}
                onRetry={onRetryMyBots}
                headerHint={
                  hasAgentCodingBots ? (
                    <TooltipProvider delayDuration={0}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label="AgentCoding Bot 使用提示"
                            className="h-7 w-7 rounded-md text-muted-foreground hover:text-primary"
                          >
                            <Info className="h-3.5 w-3.5" aria-hidden="true" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-[240px]">
                          <span>AgentCoding Bot 请前往 </span>
                          <a
                            href="/bot-workshop"
                            className="font-medium text-primary underline underline-offset-2"
                            onClick={(event) => {
                              event.stopPropagation();
                              if (onOpenBotWorkshop) {
                                event.preventDefault();
                                onOpenBotWorkshop();
                              }
                            }}
                          >
                            Bot 工坊
                          </a>
                          <span> 使用</span>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
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
              error={friendBotsError}
              onRetry={onRetryFriendBots}
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
    <ResizableWorkspaceSidebar
      ariaLabel="Bot 会话侧栏"
      collapsedContent={
        props.view && props.onViewChange ? (
          <WorkspaceSidebarCollapsedRail
            value={props.view}
            options={props.availableViews ?? ['chat', 'group']}
            onChange={props.onViewChange}
          />
        ) : undefined
      }
    >
      <BotSessionList {...props} />
    </ResizableWorkspaceSidebar>
  );
}
