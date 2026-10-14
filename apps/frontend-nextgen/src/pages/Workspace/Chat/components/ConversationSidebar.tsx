// 对话页二级侧栏:搜索 + 我的 Bot / 团队 Bot / 好友 Bot 三组目录(组名按 dmore index.html 侧栏实测);
// 团队 Bot 数据走独立 collaborations 目录(section=team,独立加载/错误/重试,他人会话开发中暂禁)。
// 本体(ConversationSidebarContent)与外壳分离:桌面端用 ResizableWorkspaceSidebar,
// <lg 由页面(任务 8)以 Drawer 包裹同一 Content,保证两端一致。
// 组件只消费 Store 状态与同步 setter / Hook 模型回调,不触达 Service。
import { Empty, Input } from '@/components/ui';
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
  onOpenPublicBots(): void;
  /** 各分组标题行「+」添加入口:跳 Bot 管理页(/bot-workshop)创建 Bot。 */
  onOpenBotWorkshop(): void;
  /** AgentCoding Bot 使用专用 coding-chat;与 Bot 工坊「去使用」保持一致。 */
  onOpenAgentCodingBot(bot: ConversationBotView['bot']): void;
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto bg-muted/20">
        <div className="sticky top-0 z-20 bg-muted/20 pt-1 backdrop-blur-sm">
          {/* 搜索框（dmore DOM 实测）：h32 rx8 白底 + 1px #E4E4E7 边框 + #A1A1AA 放大镜 + #B9BEC5 占位/文字 12px。 */}
          <div className="px-4 pb-1">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-content-icon" />
              <Input
                className="h-8 rounded-lg border border-input bg-background pl-8 text-xs text-foreground placeholder:text-content-hint"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="请输入关键词"
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
              onOpenBotWorkshop={onOpenBotWorkshop}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.managed === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('managed', props.store.collapsedBotGroups.managed !== true)
              }
              dockSelectedBotId={props.store.selectedSection === 'managed' ? props.store.selectedBotId : null}
              onQuickOpen={(view) => {
                props.store.setBotGroupCollapsed('managed', false);
                // 未展开过才补 toggleBot(带会话懒加载);已展开的只恢复分组可见,不回弹成收起。
                if (!props.store.expandedBotIds[view.bot.botId]) {
                  props.sessions.toggleBot(view.bot.botId, 'managed');
                }
              }}
            />
            {/* 团队 Bot 第三分组:桶非空/搜索中/独立加载中/独立加载失败时渲染
                (collaborations 目录 loading/error/retry 须可见,2026-10-10 spec);
                settle 后空目录不渲染,避免空「团队 Bot」死分组(B2/B3 复原裁决)。 */}
            {(filteredTeam.length > 0 || isSearching || directory.teamLoading || directory.teamError) && (
              <SidebarSection
                title={teamTitle}
                loading={directory.teamLoading && teamBots.length === 0}
                error={directory.teamError}
                onRetry={directory.retryTeam}
                items={filteredTeam}
                emptyTitle="暂无团队 Bot"
                emptyHint="加入 Bot 协作团队后将在此展示。"
                isSearching={isSearching}
                onOpenPublicBots={undefined}
                onOpenBotWorkshop={onOpenBotWorkshop}
                renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
                collapsed={false}
                onToggleCollapsed={() => undefined}
              />
            )}
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
              onOpenBotWorkshop={onOpenBotWorkshop}
              renderItem={(view) => <FriendItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.friend === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('friend', props.store.collapsedBotGroups.friend !== true)
              }
              dockSelectedBotId={props.store.selectedSection === 'friend' ? props.store.selectedBotId : null}
              onQuickOpen={(view) => {
                props.store.setBotGroupCollapsed('friend', false);
                if (!props.store.expandedBotIds[view.bot.botId]) {
                  props.sessions.toggleBot(view.bot.botId, 'friend');
                }
              }}
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
  // 收起竖轨的数据面:管理/好友两组常驻(与展开态空态/CTA 收口一致),
  // 团队组仅在有成员时入轨(展开侧栏对空团队桶也不渲染死分组);加载中/失败态
  // 不进竖轨——收起条只做「快速抵达」,真态留在展开侧栏。
  const railSections: ConversationCollapsedRailSection[] = [
    { id: 'managed', label: '我的 Bot', items: managedBots },
    ...(teamBots.length > 0
      ? [{ id: 'team' as const, label: '团队 Bot', items: teamBots }]
      : []),
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
            // 与分组「泊位」onQuickOpen 同契约:恢复分组可见(下次展开即就位) +
            // 未展开过的 Bot 补 toggleBot 懒加载;侧栏本身保持收起不打扰舞台。
            if (view.section !== 'team') store.setBotGroupCollapsed(view.section, false);
            if (!store.expandedBotIds[view.bot.botId]) {
              props.sessions.toggleBot(view.bot.botId, view.section);
            }
          }}
          onExpand={expandSidebar}
          onOpenPublicBots={props.onOpenPublicBots}
          onOpenAgentCodingBot={props.onOpenAgentCodingBot}
        />
      )}
    >
      <ConversationSidebarContent {...props} />
    </ResizableWorkspaceSidebar>
  );
}
