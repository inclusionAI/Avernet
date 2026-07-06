/**
 * SessionListPanel - 会话列表面板
 *
 * 显示群组内的会话列表，支持新建、搜索、分页、标题编辑
 */

import Button from '@/components/Button';
import Empty from '@/components/Empty';
import { Skeleton } from '@/components/Skeleton';
import { Drawer, DrawerContent } from '@/components/ui/drawer';
import { Sidebar } from '@/components/ui/sidebar';
import { cn } from '@/utils/utils';
import {
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Search,
} from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { GroupSession } from '../types';

interface SessionListPanelProps {
  /** 会话列表 */
  sessions: GroupSession[];
  /** 当前活跃的会话 ID */
  activeSessionId: string;
  /** 是否正在加载 */
  isLoading: boolean;
  /** 是否有更多 */
  hasMore: boolean;
  /** 是否正在创建 */
  isCreating: boolean;
  /** 选中会话回调 */
  onSelectSession: (sessionId: string) => void;
  /** 新建会话回调 */
  onCreateSession: () => void;
  /** 加载更多回调 */
  onLoadMore: () => void;
  /** 搜索回调 */
  onSearch: (query: string) => void;
  /** 清除选中会话回调 */
  onClearSession?: () => void;
  /** 是否为移动端 */
  isMobile?: boolean;
  /** 当前视角类型 */
  actorKind?: 'bot' | 'human';
  /** 打开会话管理回调 */
  onOpenSessionSettings?: (sessionId: string) => void;
  /** 是否收起 */
  collapsed?: boolean;
  /** 收起状态变更回调 */
  onCollapsed?: (collapsed: boolean) => void;
  /** 移动端 Drawer 打开状态 */
  drawerOpen?: boolean;
  /** 移动端 Drawer 状态变更回调 */
  onDrawerOpenChange?: (open: boolean) => void;
}

/** 友好的相对时间格式 */
function formatRelativeTime(timestamp: number): string {
  const now = new Date();
  const date = new Date(timestamp);
  const pad = (n: number) => n.toString().padStart(2, '0');

  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday =
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate();

  const timeStr = `${pad(date.getHours())}:${pad(date.getMinutes())}`;

  if (isToday) return `今天 ${timeStr}`;
  if (isYesterday) return `昨天 ${timeStr}`;

  // 同年只显示月/日，跨年显示年/月/日
  if (date.getFullYear() === now.getFullYear()) {
    return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${timeStr}`;
  }
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(
    date.getDate(),
  )}`;
}

const SessionListPanel: React.FC<SessionListPanelProps> = ({
  sessions,
  activeSessionId,
  isLoading,
  hasMore,
  isCreating,
  onSelectSession,
  onCreateSession,
  onLoadMore,
  onSearch,
  onClearSession,
  isMobile,
  actorKind = 'bot',
  onOpenSessionSettings,
  collapsed,
  onCollapsed,
  drawerOpen,
  onDrawerOpenChange,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // 搜索防抖
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchQuery(value);
      if (searchTimerRef.current) {
        clearTimeout(searchTimerRef.current);
      }
      searchTimerRef.current = setTimeout(() => {
        onSearch(value);
      }, 300);
    },
    [onSearch],
  );

  // 搜索结果不包含当前选中会话时，清除选中状态
  useEffect(() => {
    if (!activeSessionId || !searchQuery?.trim() || isLoading) return;
    const isActiveInList = sessions?.some(
      (s) => s.sessionId === activeSessionId,
    );
    if (!isActiveInList) {
      onClearSession?.();
    }
  }, [sessions, activeSessionId, searchQuery, isLoading, onClearSession]);

  // 滚动加载更多
  const handleScroll = useCallback(() => {
    const el = listRef.current;
    if (!el || !hasMore || isLoading) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      onLoadMore();
    }
  }, [hasMore, isLoading, onLoadMore]);

  // 移动端 Drawer 模式
  if (isMobile && drawerOpen !== undefined) {
    return (
      <Drawer open={drawerOpen} onOpenChange={onDrawerOpenChange}>
        <DrawerContent
          position="bottom"
          height="auto"
          title="会话列表"
          className="rounded-t-2xl"
        >
          <div className="flex flex-col bg-white max-h-[60vh]">
            {/* 头部：标题 + 新建按钮 */}
            <div className="px-3 pt-3 pb-2 flex-shrink-0">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-800">
                  会话
                </span>
                <Button
                  variant="secondary"
                  soft
                  size="sm"
                  onClick={onCreateSession}
                  disabled={isCreating}
                  className="h-7 gap-1 text-xs"
                >
                  {isCreating ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Plus className="w-3.5 h-3.5" />
                  )}
                  新建会话
                </Button>
              </div>
            </div>

            {/* 搜索框 */}
            <div className="px-3 pb-2 flex-shrink-0">
              <div className="relative group">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 group-focus-within:text-lavender-500 transition-colors" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  placeholder="搜索会话名称"
                  className="w-full pl-8 pr-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
                />
              </div>
            </div>

            {/* 会话列表 */}
            <div
              ref={listRef}
              onScroll={handleScroll}
              className="overflow-y-auto"
            >
              {isLoading && sessions.length === 0 ? (
                <div className="px-1.5 pt-2">
                  <Skeleton.ListItem count={5} showMeta />
                </div>
              ) : sessions.length === 0 ? (
                <Empty
                  size="sm"
                  icon={<MessageSquare />}
                  title="暂无会话"
                  description="点击上方「新建会话」创建"
                />
              ) : (
                <div className="space-y-0.5 px-1.5 pb-2">
                  {sessions.map((session, index) => {
                    const isActive = activeSessionId === session.sessionId;
                    const seq = index + 1;
                    return (
                      <button
                        key={session.sessionId}
                        type="button"
                        onClick={() => {
                          onSelectSession(session.sessionId);
                          onDrawerOpenChange?.(false);
                        }}
                        className={cn(
                          'w-full px-3 py-2.5 text-left rounded-lg transition-all duration-150',
                          'border-l-2',
                          isActive
                            ? 'bg-lavender-50 border-l-lavender-500'
                            : 'border-l-transparent hover:bg-slate-50',
                        )}
                      >
                        <div className="flex items-center gap-1.5">
                          <span
                            className={cn(
                              'inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-semibold flex-shrink-0',
                              isActive
                                ? 'bg-lavender-500 text-white'
                                : 'bg-slate-200/70 text-slate-500',
                            )}
                          >
                            {seq}
                          </span>
                          <span
                            className={cn(
                              'text-[13px] truncate flex-1 leading-snug',
                              isActive
                                ? 'font-medium text-slate-800'
                                : 'text-slate-700',
                            )}
                          >
                            {session.sessionTitle || '新会话'}
                          </span>
                        </div>
                        <div className="mt-1 text-[10px] text-slate-400 truncate leading-tight pl-5">
                          {formatRelativeTime(session.createdAt)}
                        </div>
                      </button>
                    );
                  })}

                  {isLoading && sessions.length > 0 && (
                    <div className="flex items-center justify-center py-3 text-slate-400 gap-2">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-lavender-500" />
                      <span className="text-xs">加载更多...</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </DrawerContent>
      </Drawer>
    );
  }

  return (
    <Sidebar
      width={{ default: 260, collapsed: 44, min: 200, max: 360 }}
      collapsed={collapsed}
      onCollapsed={onCollapsed}
      collapsedContent={
        <div className="flex flex-col items-center gap-2 pt-2">
          <MessageSquare className="w-4 h-4 text-lavender-500" />
          <span className="text-[9px] text-slate-400 writing-vertical-rl leading-tight">
            会话
          </span>
        </div>
      }
      className={cn(isMobile ? 'w-full' : '', 'bg-slate-50/40')}
    >
      {/* 头部：标题 + 新建按钮 */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-800">会话</span>
          </div>
          <Button
            variant="secondary"
            soft
            size="sm"
            onClick={onCreateSession}
            disabled={isCreating}
            className="h-7 gap-1 text-xs"
          >
            {isCreating ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Plus className="w-3.5 h-3.5" />
            )}
            新建会话
          </Button>
        </div>
        <p className="mt-1.5 text-[10px] text-slate-400 leading-relaxed">
          {actorKind === 'human'
            ? '仅展示当前用户已加入的会话，不同会话之间上下文独立'
            : '仅展示当前 Bot 视角下已加入的会话，不同会话之间上下文独立'}
        </p>
      </div>

      {/* 搜索框 */}
      <div className="px-3 pb-2 flex-shrink-0">
        <div className="relative group">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 group-focus-within:text-lavender-500 transition-colors" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder="搜索会话名称"
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
          />
        </div>
      </div>

      {/* 会话列表 */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto custom-scrollbar"
      >
        {isLoading && sessions.length === 0 ? (
          <div className="px-1.5 pt-2">
            <Skeleton.ListItem count={5} showMeta />
          </div>
        ) : sessions.length === 0 ? (
          <Empty
            size="sm"
            icon={<MessageSquare />}
            title="暂无会话"
            description="点击上方「新建会话」创建"
          />
        ) : (
          <div className="space-y-0.5 px-1.5 pb-2">
            {sessions.map((session, index) => {
              const isActive = activeSessionId === session.sessionId;
              const seq = index + 1;
              return (
                <div key={session.sessionId} className="relative group/row">
                  <button
                    type="button"
                    onClick={() => onSelectSession(session.sessionId)}
                    className={cn(
                      'w-full px-3 py-2.5 text-left rounded-lg transition-all duration-150',
                      'border-l-2',
                      isActive
                        ? 'bg-white border-l-lavender-500 shadow-sm'
                        : 'border-l-transparent hover:bg-white/60',
                    )}
                  >
                    {/* 标题行 */}
                    <div className="flex items-center gap-1.5 pr-5 group-hover/row:pr-6">
                      <span
                        className={cn(
                          'inline-flex items-center justify-center w-4 h-4 rounded text-[9px] font-semibold flex-shrink-0',
                          isActive
                            ? 'bg-lavender-500 text-white'
                            : 'bg-slate-200/70 text-slate-500',
                        )}
                      >
                        {seq}
                      </span>
                      <span
                        className={cn(
                          'text-[13px] truncate flex-1 leading-snug',
                          isActive
                            ? 'font-medium text-slate-800'
                            : 'text-slate-700',
                        )}
                      >
                        {session.sessionTitle || '新会话'}
                      </span>
                    </div>

                    {/* 时间行 */}
                    <div className="mt-1 text-[10px] text-slate-400 truncate leading-tight pl-5 group-hover/row:opacity-0 transition-opacity">
                      {formatRelativeTime(session.createdAt)}
                    </div>
                  </button>

                  {/* "..." 操作按钮（hover 时显示） */}
                  {onOpenSessionSettings && (
                    <div className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover/row:opacity-100 transition-opacity">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenSessionSettings(session.sessionId);
                        }}
                        className="p-1 rounded-md hover:bg-slate-200/70 text-slate-400 hover:text-slate-600 transition-colors"
                        title="会话管理"
                      >
                        <MoreHorizontal className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              );
            })}

            {/* 加载更多 */}
            {isLoading && sessions.length > 0 && (
              <div className="flex items-center justify-center py-3 text-slate-400 gap-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-lavender-500" />
                <span className="text-xs">加载更多...</span>
              </div>
            )}

            {/* 没有更多 */}
            {!isLoading && !hasMore && sessions.length > 0 && (
              <div className="text-center py-3 text-[10px] text-slate-400">
                没有更多了
              </div>
            )}
          </div>
        )}
      </div>
    </Sidebar>
  );
};

export default SessionListPanel;
