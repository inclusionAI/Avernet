// 对话页二级侧栏:搜索 + 我管理的 Bot / 我协作的 Bot / 好友 Bot 三组目录(组名按 dmore index.html 侧栏实测);
// 协作组数据走独立 collaborations 目录(section=team,独立加载/错误/重试,他人会话开发中暂禁)。
// 本体(ConversationSidebarContent)与外壳分离:桌面端用 ResizableWorkspaceSidebar,
// <lg 由页面(任务 8)以 Drawer 包裹同一 Content,保证两端一致。
// 组件只消费 Store 状态与同步 setter / Hook 模型回调,不触达 Service。
// 快捷入口组件集(HeaderActionButton/CreateBotEntryButton)拆至 ConversationSidebarQuickEntries
// (2026-11-05 TC-G005 文件体积门禁,与 Section/Items 同款拆分惯例)。
import { Button, Empty, Input } from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import type { ConversationBotSection, ConversationBotView } from '@/domain/conversation/types';
import type { ConversationDirectoryModel } from '@/pages/Workspace/Chat/hooks/useConversationDirectory';
import type { ConversationSessionsModel } from '@/pages/Workspace/Chat/hooks/useConversationSessions';
import type { ManagedBotOthersModel } from '@/pages/Workspace/Chat/hooks/useManagedBotOthers';
import type { ConversationState } from '@/stores/conversationStoreState';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { Search, UserPlus } from 'lucide-react';
import { useState } from 'react';
import { ResizableWorkspaceSidebar } from '../../components/ResizableWorkspaceSidebar';
import { ConversationCollapsedRail } from './ConversationCollapsedRail';
import type { ConversationCollapsedRailSection } from './ConversationCollapsedRail';
import { CreateBotEntryButton, HeaderActionButton } from './ConversationSidebarQuickEntries';
import { ConversationSidebarSection as SidebarSection } from './ConversationSidebarSection';
import { FriendItem, ManagedItem } from './ConversationSidebarItems';

export interface ConversationSidebarProps {
  managedBots: ConversationBotView[];
  /** 协作组第三分组(「我协作的 Bot」,collaborations 目录,section=team;sprint spec 2026-10-10-conversation-team-bots)。 */
  teamBots: ConversationBotView[];
  friendBots: ConversationBotView[];
  store: ConversationState;
  directory: ConversationDirectoryModel;
  sessions: ConversationSessionsModel;
  others: ManagedBotOthersModel;
  onOpenSession(botId: string, section: ConversationBotSection, sessionId: string, friendUserId?: string): void;
  /** 好友 Bot 空态文案内「公开 Bot」链接:跳公开 Bot 发现页(/collaboration-square/bots)。 */
  onOpenPublicBots(): void;
  /** 管理组空态文案内「Bot 管理」链接:跳 Bot 管理页(/bot-workshop)创建 Bot。 */
  onOpenBotWorkshop(): void;
  /** 创建入口空间选择菜单数据(2026-11-05 验收微调):已加入空间列表(沿用空间上下文既有顺序,
   *  个人空间置顶);缺省空数组时菜单仅显示空态文案。 */
  createEntrySpaces?: Space[];
  /** 创建入口菜单默认勾选高亮的空间 id(取全局当前空间)。 */
  createEntryCurrentSpaceId?: number;
  /** 创建入口选择落地空间后回调:装配层带空间上下文跳 Bot 管理页。 */
  onCreateBotInSpace(spaceId: number): void;
  /** 「Bot 管理」菜单项回调(#2990 还原):带该 Bot 所属空间上下文跳转 Bot 管理页;
   *  好友 Bot 不提供(装配层只经 ManagedItem 传导,菜单项不渲染)。 */
  onOpenBotManagement?(bot: ConversationBotView['bot']): void;
  /** Bot 正在回复中的会话 id(#2990 还原):仅当前打开的普通 Runtime 会话;Coding Bot 暂为 null。 */
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
  const { createEntrySpaces, createEntryCurrentSpaceId, onCreateBotInSpace } = props;
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const isSearching = keyword.length > 0;
  const filteredManaged = filterBots(managedBots, keyword);
  const filteredTeam = filterBots(teamBots, keyword);
  const filteredFriend = filterBots(friendBots, keyword);
  // 组名与分组顺序按 v2 设计稿对齐(dmore index.html 侧栏实测):我管理的 Bot / 我协作的 Bot / 好友 Bot。
  // (2026-11-04 微调:团队组更名「团队空间 Bot」;2026-11-05 再更名「我协作的 Bot」——对齐接口语义
  //  (collaborations=我持有协作记录的 Bot,owner 非我),与「我管理的 Bot」形成 owner/collaborator 对仗。)
  // (2026-11-05 微调:管理组更名「我管理的 Bot」,三组补 infoHint(「我」视角与组名统一)。
  //  接口语义核对:管理组 GET /openapi/v1/bots owner=当前用户;协作组 collaborations 接口
  //  exclude_owner_id 排除本人 + 仅返回我持有 collaborator 记录(admin/member)的 Bot;
  //  好友组 Friend Connections(用户申请添加、对方同意),均为他人创建、我方主动建立好友关系。)
  const managedTitle = '我管理的 Bot';
  const teamTitle = '我协作的 Bot';
  const friendTitle = '好友 Bot';
  // 空态提示(文案即入口):管理组均为用户创建并拥有(owner=当前用户),引导去 Bot 管理创建
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
              infoHint="所有空间下我创建并拥有的 Bot"
              isSearching={isSearching}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.managed === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('managed', props.store.collapsedBotGroups.managed !== true)
              }
              headerAction={
                // 快捷入口(2026-11-05,同日验收微调):分组有 Bot 时创建动线不再断链;点击弹空间
                // 选择菜单(默认勾选当前空间),选择后带空间上下文直达 Bot 管理页;与空态文字引导
                // 互补。协作组不设入口(获取靠空间申请/owner 分享,非单方动作)。
                <CreateBotEntryButton
                  spaces={createEntrySpaces ?? []}
                  currentSpaceId={createEntryCurrentSpaceId}
                  onSelectSpace={onCreateBotInSpace}
                />
              }
            />
            {/* 团队目录为空时仍保留分类，与我的/好友 Bot 的空态、收起行为一致。 */}
            <SidebarSection
              title={teamTitle}
              loading={directory.teamLoading && teamBots.length === 0}
              error={directory.teamError}
              onRetry={directory.retryTeam}
              items={filteredTeam}
              emptyTitle="暂无协作的 Bot"
              isSearching={isSearching}
              renderItem={(view) => <ManagedItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              infoHint="团队空间下其他成员创建并邀请我协作的 Bot"
              collapsed={!isSearching && props.store.collapsedBotGroups.team === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('team', props.store.collapsedBotGroups.team !== true)
              }
            />
            <SidebarSection
              title={friendTitle}
              guideTarget="chat-friend-group"
              loading={directory.friendLoading && friendBots.length === 0}
              error={directory.friendError}
              onRetry={directory.retryFriend}
              items={filteredFriend}
              emptyTitle="暂无好友 Bot"
              emptyHint={friendEmptyHint}
              infoHint="从发现菜单中以用户身份申请添加的好友 Bot"
              isSearching={isSearching}
              renderItem={(view) => <FriendItem key={view.bot.botId} view={view} props={props} />}
              collapsible
              collapsed={!isSearching && props.store.collapsedBotGroups.friend === true}
              onToggleCollapsed={() =>
                props.store.setBotGroupCollapsed('friend', props.store.collapsedBotGroups.friend !== true)
              }
              headerAction={
                // 快捷入口(2026-11-05,同日验收微调):好友 Bot 获取动线直达公开 Bot 发现页;
                // 装配层回调负责跳转前把协作身份切为用户身份(好友仅承接用户身份申请)。
                // 入口语义按验收口径收敛为「添加 Bot 好友」(UserPlus),跳转落点不变。
                <HeaderActionButton
                  label="添加 Bot 好友"
                  icon={UserPlus}
                  onClick={() => {
                    Tracert.click(Points.workspaceChat.discoverBotEntry);
                    onOpenPublicBots();
                  }}
                />
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
    { id: 'managed', label: '我管理的 Bot', items: managedBots },
    { id: 'team', label: '我协作的 Bot', items: teamBots },
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
            // 与分组展开同契约:恢复分组可见(下次展开即就位) +
            // 未展开过的 Bot 补 toggleBot 懒加载;侧栏本身保持收起不打扰舞台。
            if (view.section !== 'team') store.setBotGroupCollapsed(view.section, false);
            if (!store.expandedBotIds[view.bot.botId]) {
              props.sessions.toggleBot(view.bot.botId, view.section);
            }
          }}
          onExpand={expandSidebar}
        />
      )}
    >
      <ConversationSidebarContent {...props} />
    </ResizableWorkspaceSidebar>
  );
}
