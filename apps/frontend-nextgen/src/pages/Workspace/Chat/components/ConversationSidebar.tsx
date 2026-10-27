// 对话页二级侧栏:搜索 + 我的 Bot / 团队 Bot / 好友 Bot 三组目录(组名按 dmore index.html 侧栏实测);
// 团队 Bot 数据走独立 collaborations 目录(section=team,独立加载/错误/重试,他人会话开发中暂禁)。
// 本体(ConversationSidebarContent)与外壳分离:桌面端用 ResizableWorkspaceSidebar,
// <lg 由页面(任务 8)以 Drawer 包裹同一 Content,保证两端一致。
// 组件只消费 Store 状态与同步 setter / Hook 模型回调,不触达 Service。
import { Button, Empty, Input } from '@/components/ui';
import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation/types';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import type { ConversationState } from '@/stores/conversationStoreState';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { ResizableWorkspaceSidebar } from '../../components/ResizableWorkspaceSidebar';
import { ConversationCollapsedRail } from './ConversationCollapsedRail';
import type { ConversationCollapsedRailSection } from './ConversationCollapsedRail';
import { ConversationSidebarSection as SidebarSection } from './ConversationSidebarSection';
import { FriendItem, ManagedItem } from './ConversationSidebarItems';

export interface ConversationSidebarProps {
  managedBots: ConversationBotView[];
  /** 团队 Bot 第三分组(collaborations 目录,section=team;sprint spec 2026-10-10-conversation-team-bots)。 */
  teamBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  store: ConversationState;
  directory: ConversationDirectoryModel;
  sessions: ConversationSessionsModel;
  others: ManagedBotOthersModel;
  onOpenSession(botId: string, section: ConversationBotSection, sessionId: string, friendUserId?: string): void;
  /** 好友 Bot 空态文案内「公开 Bot」链接:跳公开 Bot 发现页(/collaboration-square/bots)。 */
  onOpenPublicBots(): void;
  /** 我的 Bot 空态文案内「Bot 管理」链接:跳 Bot 管理页(/bot-workshop)创建 Bot。 */
  onOpenBotWorkshop(): void;
  /** AgentCoding Bot 使用专用 coding-chat;与 Bot 工坊「去使用」保持一致。 */
  onOpenAgentCodingBot(bot: ConversationBotView['bot']): void;
  /** 「Bot 管理」菜单项回调(#2990 还原):带该 Bot 所属空间上下文跳转 Bot 管理页;
   *  好友 Bot 不提供(装配层只经 ManagedItem 传导,菜单项不渲染)。 */
  onOpenBotManagement?(bot: ConversationBotView['bot']): void;
  /** Bot 正在回复中的会话 id(#2990 还原):仅当前打开的会话;会话行标题左侧渲染 loading 指示。 */
  replyingSessionId: string | null;
}

/** 空态文案内跳转链接:文案即入口,点击直达对应菜单路由(2026-10-14 体验修复)。 */
function EmptyHintLink({ label, onClick }: { label: string; onClick(): void }) {
  return (
    <Button
      variant="link"
      size="sm"
      className="h-auto min-w-0 rounded-none p-0 text-xs font-normal text-primary no-underline underline-offset-2 hover:underline"
      onClick={onClick}
    >
      {label}
    </Button>
  );
}

function filterBots(bots: ConversationBotView[], keyword: string): ConversationBotView[] {
  if (!keyword) return bots;
  return bots.filter((view) => view.bot.displayName.toLowerCase().includes(keyword));
}

export function ConversationSidebarContent(props: ConversationSidebarProps) {
  const { managedBots, teamBots, friendBots, directory, onOpenPublicBots, onOpenBotWorkshop } = props;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const isSearching = keyword.length > 0;
  const filteredManaged = filterBots(managedBots, keyword);
  const filteredTeam = filterBots(teamBots, keyword);
  const filteredFriend = filterBots(friendBots, keyword);
  // 组名与分组顺序按 v2 设计稿对齐(dmore index.html 侧栏实测):我的 Bot / 团队 Bot / 好友 Bot。
  const managedTitle = '我的 Bot';
  const teamTitle = '团队 Bot';
  const friendTitle = '好友 Bot';
  // 空态提示(文案即入口):我的 Bot 均为用户创建并管理,引导去 Bot 管理创建
  // (2026-10-14 体验修复,不再误导去公开 Bot 添加);好友 Bot 引导去公开 Bot 发现添加。
  const managedEmptyHint = (
    <>
      可前往
      <EmptyHintLink label="「Bot 管理」" onClick={onOpenBotWorkshop} />
      创建新 Bot。
    </>
  );
  const friendEmptyHint = (
    <>
      可前往
      <EmptyHintLink label="「公开 Bot」" onClick={onOpenPublicBots} />
      发现并添加好友 Bot。
    </>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted/20">
        <div className="sticky top-0 z-20 bg-muted/20 pt-2 backdrop-blur-sm">
          {/* 搜索框（dmore DOM 实测）：h32 rx8 白底 + 1px #E4E4E7 边框 + #A1A1AA 放大镜 + #B9BEC5 占位/文字 12px。
              pt-2 / pb-2 呼吸（2026-10-22 体验精修）：行间铅线拆除改为留白分组后，
              检索口与其下目录保持同款空气感，不再贴着第一个分组标题。 */}
          <div className="px-4 pb-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-content-icon" />
              <Input
                className="h-8 rounded-lg border border-input bg-background pl-8 text-xs text-foreground placeholder:text-content-hint"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="请输入Bot名称"
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
              emptyHint={managedEmptyHint}
              isSearching={isSearching}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.managed === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('managed', props.store.collapsedBotGroups.managed !== true)
              }
            />
            {/* 团队目录为空时仍保留分类，与我的/好友 Bot 的空态、收起行为一致。 */}
            <SidebarSection
              title={teamTitle}
              loading={directory.teamLoading && teamBots.length === 0}
              error={directory.teamError}
              onRetry={directory.retryTeam}
              items={filteredTeam}
              emptyTitle="暂无运行中的团队 Bot"
              isSearching={isSearching}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              infoHint="通过团队成员身份获取的 Bot"
              collapsed={!isSearching && props.store.collapsedBotGroups.team === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('team', props.store.collapsedBotGroups.team !== true)
              }
            />
            <SidebarSection
              title={friendTitle}
              loading={directory.friendLoading && friendBots.length === 0}
              error={directory.friendError}
              onRetry={directory.retryFriend}
              items={filteredFriend}
              emptyTitle="暂无好友 Bot"
              emptyHint={friendEmptyHint}
              isSearching={isSearching}
              renderItem={(view) => <FriendItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.friend === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('friend', props.store.collapsedBotGroups.friend !== true)
              }
            />
          </>
        )}
      </div>
    </div>
  );
}

/** 桌面端(≥lg)可拖宽侧栏外壳;<lg 隐藏,由页面用 Drawer 包裹 Content 呈现。 */
export function ConversationSidebar(props: ConversationSidebarProps) {
  const { managedBots, teamBots, friendBots, store } = props;
  // 整栏收起时保留全部三类目录；空目录提供展开入口，与完整侧栏保持一致。
  const railSections: ConversationCollapsedRailSection[] = [
    { id: 'managed', label: '我的 Bot', items: managedBots },
    { id: 'team', label: '团队 Bot', items: teamBots },
    { id: 'friend', label: '好友 Bot', items: friendBots },
  ];
  return (
    <ResizableWorkspaceSidebar
      ariaLabel="会话侧栏"
      collapsedContent={(expandSidebar) => (
        <ConversationCollapsedRail
          sections={railSections}
          selectedBotId={store.selectedBotId}
          selectedSection={store.selectedSection}
          onQuickOpen={(view) => {
            if (view.bot.isAgentCodingBot) {
              props.onOpenAgentCodingBot(view.bot);
              return;
            }
            // 与分组展开同契约:恢复分组可见(下次展开即就位) +
            // 未展开过的 Bot 补 toggleBot 懒加载;侧栏本身保持收起不打扰舞台。
            if (view.section !== 'team') store.setBotGroupCollapsed(view.section, false);
            if (!store.expandedBotIds[view.bot.botId]) {
              props.sessions.toggleBot(view.bot.botId, view.section);
            }
          }}
          onExpand={expandSidebar}
          onOpenAgentCodingBot={props.onOpenAgentCodingBot}
        />
      )}
    >
      <ConversationSidebarContent {...props} />
    </ResizableWorkspaceSidebar>
  );
}
