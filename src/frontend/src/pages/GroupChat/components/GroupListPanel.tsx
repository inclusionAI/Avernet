/**
 * GroupListPanel - 群组列表面板
 *
 * 支持两种形态：
 * - 桌面端：Sidebar 形式，嵌入左侧
 * - 移动端：Drawer 抽屉形式，从底部弹出
 *
 * 包含：搜索框 + 操作按钮 + 群组列表
 */

import Empty from '@/components/Empty';
import Skeleton from '@/components/Skeleton';
import { ChatListItem, type ActionItem } from '@/components/ui/chat-list-item';
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { SearchInput } from '@/components/ui/search-input';
import { Sidebar } from '@/components/ui/sidebar';
import type { Bot } from '@/services/backend-api/BotController';
import { cn } from '@/utils/utils';
import { Loader2, Settings, UserPlus, Users } from 'lucide-react';
import React, { useCallback, useEffect, useRef } from 'react';
import type { GroupInfo } from '../types';
import BotInfoCard from './BotInfoCard';
import GroupAvatarGrid from './GroupAvatarGrid';

interface GroupListPanelProps {
  /** 是否为移动端 */
  isMobile?: boolean;
  /** 群组列表 */
  groups: GroupInfo[];
  /** 当前选中的群组 ID */
  selectedGroupId: string | null;
  /** 是否正在加载 */
  isLoading: boolean;
  /** 是否有更多数据 */
  hasMore?: boolean;
  /** 是否正在加载更多 */
  isLoadingMore?: boolean;
  /** 加载更多回调 */
  onLoadMore?: () => void;
  /** 搜索关键词 */
  searchQuery: string;
  /** 搜索关键词变化回调 */
  onSearchChange: (query: string) => void;
  /** 点击群组回调 */
  onGroupClick: (groupId: string) => void;
  /** 移动端抽屉是否打开 */
  drawerOpen?: boolean;
  /** 移动端抽屉状态变化回调 */
  onDrawerOpenChange?: (open: boolean) => void;
  /** 获取 Bot 名称 */
  getBotName: (botUuid: string) => string | null;
  /** 好友请求未读数 */
  unreadRequestCount?: number;
  /** 点击添加好友按钮 */
  onAddFriend?: () => void;
  /** 点击创建群组按钮 */
  onCreateGroup?: () => void;
  /** 打开群管理回调 */
  onOpenGroupSettings?: (groupId: string) => void;
  /** 当前选中的 Bot/用户 信息 */
  currentBot?: {
    bot_uuid?: string;
    bot_name: string;
    avatar_url?: string;
    visibility?: 'public' | 'protected' | 'private' | 'offline';
    is_online: boolean;
    /** Actor 在线状态（online/hidden） */
    status?: 'online' | 'hidden';
  } | null;
  /** Bot 完整信息（用于 LicenceInfo） */
  bot?: Bot | null;
  /** 卡片类型：bot | human（新增） */
  cardType?: 'bot' | 'human';
  /** 加载群组回调 */
  onLoadGroups?: () => void;
  /** 状态切换回调（新增） */
  onBotStatusChange?: (status: 'online' | 'hidden') => void;
  /** 允许被添加为好友开关变化 */
  onAllowAddFriendChange?: (value: boolean) => void;
  /** 好友确认选项变化 */
  onRequireConfirmationChange?: (value: boolean) => void;
  /** 是否收起 */
  collapsed?: boolean;
  /** 收起状态变更回调 */
  onCollapsed?: (collapsed: boolean) => void;
}

// 格式化时间 - 列表用
const formatListTime = (timestamp: number) => {
  const date = new Date(timestamp);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();
  const isYesterday =
    new Date(now.getTime() - 86400000).toDateString() === date.toDateString();

  if (isToday) {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }
  if (isYesterday) {
    return '昨天';
  }
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
};

// 获取群组预览信息
const getGroupPreview = (
  group: GroupInfo,
  getBotName: (botUuid: string) => string | null,
) => {
  const memberAvatars = group.participants.slice(0, 4).map((p) => {
    const displayName = p.botUuid ? getBotName(p.botUuid) || p.name : p.name;
    return {
      name: (displayName || p?.botUuid || 'U').charAt(0),
      type: p.type,
      botUuid: p?.botUuid,
      avatar: p?.avatar,
    };
  });

  return {
    lastMessage: '',
    lastTime: group.updatedAt || Date.now(),
    memberCount: group.participants.length,
    memberAvatars,
    unreadCount: 0,
  };
};

const GroupListPanel: React.FC<GroupListPanelProps> = ({
  isMobile = false,
  groups,
  selectedGroupId,
  isLoading,
  hasMore = false,
  isLoadingMore = false,
  onLoadMore,
  searchQuery,
  onSearchChange,
  onGroupClick,
  drawerOpen,
  onDrawerOpenChange,
  getBotName,
  unreadRequestCount = 0,
  onAddFriend,
  onCreateGroup,
  onOpenGroupSettings,
  currentBot,
  bot,
  onBotStatusChange,
  onAllowAddFriendChange,
  onRequireConfirmationChange,
  cardType = 'bot',
  collapsed,
  onCollapsed,
}) => {
  // 从 visibility 计算好友设置
  // private: 不允许被添加好友
  // protected: 允许被添加 + 需要确认
  // public: 允许被添加 + 无需确认
  // user 类型：默认允许添加，需要确认
  const allowAddFriend =
    cardType === 'human' ? true : currentBot?.visibility !== 'private';
  const requireConfirmation =
    cardType === 'human' ? true : currentBot?.visibility === 'protected';

  // 滚动容器 ref
  const listRef = useRef<HTMLDivElement>(null);

  // 滚动加载更多（带节流）
  const lastTriggerTimeRef = useRef(0);
  const THROTTLE_INTERVAL = 200;

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      // 节流控制
      const now = Date.now();
      if (now - lastTriggerTimeRef.current < THROTTLE_INTERVAL) return;

      const target = e.currentTarget;
      const { scrollTop, scrollHeight, clientHeight } = target;
      // 距离底部 100px 时触发加载更多
      if (
        scrollHeight - scrollTop - clientHeight < 100 &&
        hasMore &&
        !isLoadingMore &&
        onLoadMore
      ) {
        lastTriggerTimeRef.current = now;
        onLoadMore();
      }
    },
    [hasMore, isLoadingMore, onLoadMore],
  );

  // 自动加载更多：当首屏数据未填满容器或接近底部时，自动继续加载
  useEffect(() => {
    const checkAndLoadMore = () => {
      const container = listRef.current;
      if (!container || !onLoadMore) return;

      // 距离底部阈值（像素）
      const threshold = 100;
      const { scrollHeight, clientHeight, scrollTop } = container;
      const distanceToBottom = scrollHeight - scrollTop - clientHeight;

      // 两种情况触发加载：
      // 1. 无滚动条（内容未填满容器）
      // 2. 有滚动条但距离底部小于阈值（接近底部）
      const shouldLoadMore =
        scrollHeight <= clientHeight || distanceToBottom < threshold;

      if (
        shouldLoadMore &&
        hasMore &&
        !isLoadingMore &&
        !isLoading &&
        groups.length > 0
      ) {
        onLoadMore();
      }
    };

    // 延迟执行，确保 DOM 已更新
    const timer = setTimeout(checkAndLoadMore, 100);
    return () => clearTimeout(timer);
  }, [groups, hasMore, isLoadingMore, isLoading, onLoadMore]);

  // 注意：群组列表现在由后端搜索，不再需要前端过滤
  // groups 已经是后端搜索后的结果

  // 渲染群组列表项
  const renderGroupItem = (group: GroupInfo) => {
    const preview = getGroupPreview(group, getBotName);
    const isSelected = group.id === selectedGroupId;
    const strategyBadge =
      group.groupStrategy === 'manager_worker'
        ? {
            className: 'bg-amber-50 text-amber-600',
            label: '任务协作',
          }
        : group.groupStrategy === 'state_machine'
        ? {
            className: 'bg-emerald-50 text-emerald-600',
            label: '自定义协作',
          }
        : {
            className: 'bg-lavender-50 text-lavender-600',
            label: '自由聊天',
          };

    const actions: ActionItem[] | undefined = onOpenGroupSettings
      ? [
          {
            icon: <Settings className="w-3.5 h-3.5" />,
            label: '群管理',
            onClick: () => onOpenGroupSettings(group.id),
          },
        ]
      : undefined;

    return (
      <ChatListItem
        key={group.id}
        avatar={<GroupAvatarGrid avatars={preview.memberAvatars} />}
        title={group.topic}
        subtitle={
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                'inline-flex items-center px-1 py-0 text-[10px] font-medium rounded',
                strategyBadge.className,
              )}
            >
              {strategyBadge.label}
            </span>
            {group.visibility === 'public' && (
              <span className="inline-flex items-center px-1 py-0 text-[10px] font-medium rounded bg-blue-50 text-blue-600">
                公开群
              </span>
            )}
            <span>{preview.memberCount}个成员</span>
          </span>
        }
        time={formatListTime(preview.lastTime)}
        preview={preview.lastMessage || undefined}
        badge={preview.unreadCount || null}
        selected={isSelected}
        onClick={() => {
          if (window.aixBridge?.closePanelForce) {
            window.aixBridge.closePanelForce();
          }
          onGroupClick(group.id);
          if (isMobile && onDrawerOpenChange) {
            onDrawerOpenChange(false);
          }
        }}
        actions={actions}
        singleAction
        className={
          isMobile ? 'border-b border-slate-100' : 'border-b border-slate-50'
        }
        data-aspm-click="ca114903.da194198"
        data-aspm-desc="GroupChat-点击群组列表项"
        data-aspm-param={``}
        data-aspm-expo
      />
    );
  };

  // 渲染列表内容
  const renderListContent = () => {
    if (isLoading) {
      return isMobile ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 size={24} className="animate-spin text-lavender-500" />
        </div>
      ) : (
        <Skeleton.ListItem count={4} />
      );
    }

    if (groups.length === 0) {
      return (
        <Empty
          size="sm"
          icon={<Users />}
          title={searchQuery ? '未找到匹配的协作群' : '暂无协作群'}
          description={searchQuery ? '' : '点击右上角「拉起协作」创建新协作群'}
          className={isMobile ? 'py-12' : 'h-40'}
        />
      );
    }

    return (
      <div>
        {groups.map(renderGroupItem)}
        {/* 加载更多指示器 */}
        {isLoadingMore && (
          <div className="flex items-center justify-center py-4">
            <Loader2 size={20} className="animate-spin text-lavender-500" />
          </div>
        )}
        {/* 没有更多数据提示 */}
        {!hasMore && groups.length > 0 && !searchQuery && (
          <div className="text-center py-4 text-xs text-slate-400">
            没有更多了
          </div>
        )}
      </div>
    );
  };

  // 渲染操作按钮（搜索框下方的文字按钮）
  const renderActionButtons = () => {
    // human 类型仅展示拉起协作按钮，不展示添加好友
    if (cardType === 'human') {
      return (
        <div className="flex items-center gap-2 px-3 pb-2.5 w-full">
          {onCreateGroup && (
            <button
              type="button"
              onClick={onCreateGroup}
              className={cn(
                'flex flex-1 items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors',
                'bg-lavender-50 text-lavender-600 hover:bg-lavender-100',
              )}
              data-aspm-click="ca114903.da194200"
              data-aspm-desc="GroupChat-打开创建协作"
              data-aspm-param={``}
              data-aspm-expo
            >
              <Users className="w-4 h-4" />
              拉起协作
            </button>
          )}
        </div>
      );
    }

    return (
      <div className="flex items-center gap-2 px-3 pb-2.5 w-full">
        {/* 添加好友按钮 */}
        {onAddFriend && (
          <button
            type="button"
            onClick={onAddFriend}
            className={cn(
              'relative flex flex-1 items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors',
              'bg-lavender-50 text-lavender-600 hover:bg-lavender-100',
            )}
            data-aspm-click="ca114903.da194199"
            data-aspm-desc="GroupChat-打开添加好友"
            data-aspm-param={``}
            data-aspm-expo
          >
            <UserPlus className="w-4 h-4" />
            添加好友
            {/* 未读好友请求红点 */}
            {unreadRequestCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-red-500 rounded-full" />
            )}
          </button>
        )}

        {/* 创建群组按钮 */}
        {onCreateGroup && (
          <button
            type="button"
            onClick={onCreateGroup}
            className={cn(
              'flex flex-1 items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors',
              'bg-lavender-50 text-lavender-600 hover:bg-lavender-100',
            )}
            data-aspm-click="ca114903.da194200"
            data-aspm-desc="GroupChat-打开创建协作"
            data-aspm-param={``}
            data-aspm-expo
          >
            <Users className="w-4 h-4" />
            拉起协作
          </button>
        )}
      </div>
    );
  };

  // 移动端：抽屉形式
  if (isMobile) {
    return (
      <>
        <Drawer open={drawerOpen} onOpenChange={onDrawerOpenChange}>
          <DrawerContent
            position="bottom"
            height="auto"
            title="选择协作群"
            className="rounded-t-2xl"
          >
            <div className="flex flex-col bg-white max-h-[60vh]">
              {/* Bot/用户 信息卡片 */}
              {currentBot && (
                <BotInfoCard
                  bot={cardType === 'bot' ? bot : undefined}
                  botUuid={currentBot.bot_uuid}
                  botName={currentBot.bot_name}
                  avatarUrl={currentBot.avatar_url}
                  isOnline={currentBot.is_online}
                  visibility={currentBot.visibility}
                  actorStatus={currentBot.status}
                  allowAddFriend={allowAddFriend}
                  requireConfirmation={requireConfirmation}
                  onStatusChange={onBotStatusChange}
                  onAllowAddFriendChange={onAllowAddFriendChange}
                  onRequireConfirmationChange={onRequireConfirmationChange}
                  type={cardType}
                />
              )}
              {/* 搜索栏 */}
              <div className="px-3 pt-2.5 pb-2">
                <SearchInput
                  value={searchQuery}
                  onChange={onSearchChange}
                  placeholder="搜索协作群..."
                />
              </div>

              {/* 操作按钮 */}
              {renderActionButtons()}

              <div
                className="overflow-y-auto"
                onScroll={handleScroll}
                ref={listRef}
              >
                {renderListContent()}
              </div>
            </div>
          </DrawerContent>
        </Drawer>
      </>
    );
  }

  // 桌面端：Sidebar 形式
  return (
    <>
      <Sidebar
        width={{ default: 260, collapsed: 44, min: 200, max: 360 }}
        collapsed={collapsed}
        onCollapsed={onCollapsed}
        collapsedContent={
          <div className="flex flex-col items-center gap-2 pt-2">
            <Users className="w-4 h-4 text-lavender-500" />
            <span className="text-[9px] text-slate-400 writing-vertical-rl leading-tight">
              协作群
            </span>
          </div>
        }
      >
        {/* Bot/用户 信息卡片 */}
        {currentBot && (
          <BotInfoCard
            bot={cardType === 'bot' ? bot : undefined}
            botUuid={currentBot.bot_uuid}
            botName={currentBot.bot_name}
            avatarUrl={currentBot.avatar_url}
            isOnline={currentBot.is_online}
            visibility={currentBot.visibility}
            actorStatus={currentBot.status}
            allowAddFriend={allowAddFriend}
            requireConfirmation={requireConfirmation}
            onStatusChange={onBotStatusChange}
            onAllowAddFriendChange={onAllowAddFriendChange}
            onRequireConfirmationChange={onRequireConfirmationChange}
            type={cardType}
          />
        )}
        {/* 搜索栏 */}
        <div className="px-3 pt-2.5 pb-2">
          <SearchInput
            value={searchQuery}
            onChange={onSearchChange}
            placeholder="搜索协作群..."
          />
        </div>

        {/* 操作按钮 */}
        {renderActionButtons()}

        {/* 群组列表 */}
        <div
          className="flex-1 overflow-auto"
          onScroll={handleScroll}
          ref={listRef}
        >
          {renderListContent()}
        </div>
      </Sidebar>
    </>
  );
};

export default GroupListPanel;
