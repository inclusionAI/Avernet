import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { SessionView } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Settings2, Star } from 'lucide-react';
import React, { useState } from 'react';
import { formatSessionTime, formatSessionTimeTooltip, SessionCard } from '../SessionCard';

interface SessionItemProps {
  session: SessionView;
  selected: boolean;
  favorite: boolean;
  onSelectSession: (sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onManageSession?: (sessionId: string) => void;
}

/**
 * 协作群会话条目：标题 + 副行 + 日期，收藏与会话管理统一收进右侧更多操作菜单。
 */
export const SessionItem = React.memo(function SessionItem({
  session,
  selected,
  favorite,
  onSelectSession,
  onToggleFavorite,
  onManageSession,
}: SessionItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleFavorite = (e: React.MouseEvent) => {
    e.stopPropagation();
    setMenuOpen(false);
    onToggleFavorite(session.sessionId);
  };
  const sessionTime = session.lastMessageAt ?? session.createdAt;
  const createdTime = formatSessionTime(sessionTime);

  return (
    <SessionCard
      title={session.title}
      subtitle=""
      compact
      dateText={createdTime}
      dateTooltip={formatSessionTimeTooltip(sessionTime)}
      selected={selected}
      indicator="message"
      onSelect={() => onSelectSession(session.sessionId)}
      trailing={
        <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
          <Popover open={menuOpen} onOpenChange={setMenuOpen}>
            <PopoverTrigger asChild>
              <IconButton
                label="会话更多操作"
                size="sm"
                icon={<MoreHorizontal className="h-4 w-4" />}
                onClick={(e) => e.stopPropagation()}
              />
            </PopoverTrigger>
            <PopoverContent align="end" className="w-44 p-1">
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={handleFavorite}
              >
                <Star className={cn('h-3.5 w-3.5', favorite ? 'fill-warning text-warning' : 'text-muted-foreground')} />
                {favorite ? '取消收藏' : '收藏会话'}
              </Button>
              {onManageSession && (
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                  onClick={() => {
                    setMenuOpen(false);
                    onManageSession(session.sessionId);
                  }}
                >
                  <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                  管理会话
                </Button>
              )}
            </PopoverContent>
          </Popover>
        </div>
      }
    />
  );
});
