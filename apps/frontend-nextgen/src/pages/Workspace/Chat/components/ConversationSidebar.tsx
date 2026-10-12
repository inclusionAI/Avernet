// 对话页二级侧栏:搜索 + {用户}管理的 Bot / {用户}的团队 Bot / {用户}的好友 Bot 三组目录。
// 本体(ConversationSidebarContent)与外壳分离:桌面端用 ResizableWorkspaceSidebar,
// <lg 由页面(任务 8)以 Drawer 包裹同一 Content,保证两端一致。
// 组件只消费 Store 状态与同步 setter / Hook 模型回调,不触达 Service。
import { Button, Empty, Input, Skeleton } from '@/components/ui';
import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation/types';
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
import { FriendItem, ManagedItem } from './ConversationSidebarItems';

export interface ConversationSidebarProps {
  teamBots: ConversationBotView[];
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  store: ConversationState;
  directory: ConversationDirectoryModel;
  sessions: ConversationSessionsModel;
  others: ManagedBotOthersModel;
  onOpenSession(botId: string, section: ConversationBotSection, sessionId: string, friendUserId?: string): void;
  onOpenPublicBots(): void;
  /** AgentCoding Bot 使用专用 coding-chat;与 Bot 工坊「去使用」保持一致。 */
  onOpenAgentCodingBot(bot: ConversationBotView['bot']): void;
}

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
  onOpenPublicBots?(): void;
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
              onOpenPublicBots && (
                <Button variant="secondary" size="sm" onClick={onOpenPublicBots}>
                  前往公开 Bot
                </Button>
              )
            }
          />
        )
      ) : (
        items.map(renderItem)
      )}
    </div>
  );
}

export function ConversationSidebarContent(props: ConversationSidebarProps) {
  const { managedBots, teamBots, friendBots, directory, onOpenPublicBots } = props;
  const { identity } = useHumanIdentity();
  const userName = identity?.displayName;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const isSearching = keyword.length > 0;
  const filteredManaged = filterBots(managedBots, keyword);
  const filteredTeam = filterBots(teamBots, keyword);
  const filteredFriend = filterBots(friendBots, keyword);
  const managedTitle = userName ? `${userName}管理的 Bot` : '已管理 Bot';
  const teamTitle = userName ? `${userName}的团队 Bot` : '团队 Bot';
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
        </div>
        {isSearching && filteredManaged.length === 0 && filteredTeam.length === 0 && filteredFriend.length === 0 ? (
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
              title={teamTitle}
              loading={directory.teamLoading && teamBots.length === 0}
              error={directory.teamError}
              onRetry={directory.retryTeam}
              items={filteredTeam}
              emptyTitle="暂无团队 Bot"
              emptyHint="加入 Bot 协作团队后将在此展示。"
              isSearching={isSearching}
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
