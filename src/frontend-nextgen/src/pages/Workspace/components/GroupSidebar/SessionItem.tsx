import { IconButton } from '@/components/ui';
import type { SessionView } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Star } from 'lucide-react';
import React from 'react';
import { formatMonthDayTime, SessionCard } from '../SessionCard';

interface SessionItemProps {
  session: SessionView;
  selected: boolean;
  favorite: boolean;
  onSelectSession: (sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onManageSession?: (sessionId: string) => void;
}

/**
 * 协作群会话条目:卡片式(角标 + 标题 + 副行 + 日期 + 收藏),选中态蓝色高亮。
 * 收藏星:已收藏常显,未收藏悬浮显现;点击不冒泡,避免误收起所属群卡片。
 */
export const SessionItem = React.memo(function SessionItem({
  session,
  selected,
  favorite,
  onSelectSession,
  onToggleFavorite,
  onManageSession,
}: SessionItemProps) {
  const handleFavorite = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleFavorite(session.sessionId);
  };
  const memberCount = session.participantCount ?? session.participants.length;
  const createdTime = formatMonthDayTime(session.lastMessageAt ?? session.createdAt);
  const subtitle = memberCount > 0 ? `${memberCount} 个成员` : undefined;

  return (
    <SessionCard
      title={session.title}
      subtitle={subtitle}
      dateText={createdTime}
      selected={selected}
      onSelect={() => onSelectSession(session.sessionId)}
      trailing={
        <div className="flex items-center gap-0.5 self-start" onClick={(e) => e.stopPropagation()}>
          <IconButton
            label={favorite ? '取消收藏' : '收藏会话'}
            size="sm"
            icon={
              <Star className={cn('h-3.5 w-3.5', favorite ? 'fill-warning text-warning' : 'text-muted-foreground')} />
            }
            onClick={handleFavorite}
            className={cn(!favorite && 'opacity-0 transition-opacity group-hover:opacity-100')}
          />
          {onManageSession && (
            <IconButton
              label="管理会话"
              size="sm"
              icon={<MoreHorizontal className="h-4 w-4" />}
              onClick={(e) => {
                e.stopPropagation();
                onManageSession(session.sessionId);
              }}
            />
          )}
        </div>
      }
    />
  );
});
