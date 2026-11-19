import { profileStats } from '../profileStats';
import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { CommunityAuthor } from '@/domain/community/types';
import type { InteractionSummary } from '@/domain/community/types';
import { Bell, Bot, User } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { CommunityFollowIcon } from '../CommunityFollowIcon';

export interface CommunityAuthorAvatarProps {
  author: CommunityAuthor;
  size?: number;
  currentUserId?: string;
  summary?: InteractionSummary;
  summaryLoading?: boolean;
  onLoadSummary?: () => void;
  onFollow?: () => void;
  followLoading?: boolean;
  followed?: boolean;
}

function avatarContent(
  author: CommunityAuthor,
  size: number,
): JSX.Element {
  if (author.avatarUrl) return <Avatar name={author.displayName} src={author.avatarUrl} size={size} />;

  const Icon = author.type === 'bot' ? Bot : User;
  const iconSize = Math.round(size * 0.5);
  const style: CSSProperties = { width: size, height: size };
  return (
    <span
      role="img"
      aria-label={author.displayName}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      style={style}
    >
      <Icon aria-hidden width={iconSize} height={iconSize} />
    </span>
  );
}

/**
 * 社区作者头像：默认只渲染头像；详情页传入统计回调后启用 hover 卡片。
 * 统计数永远是服务端 summary，不在前端做点赞/关注后的本地持久状态。
 */
export function CommunityAuthorAvatar({
  author,
  size = 40,
  currentUserId,
  summary,
  summaryLoading,
  onLoadSummary,
  onFollow,
  followLoading,
  followed = false,
}: CommunityAuthorAvatarProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const openedByPointer = useRef(false);
  const pendingCloseRef = useRef<number | undefined>(undefined);
  const cancelPendingClose = useCallback(() => {
    if (pendingCloseRef.current === undefined) return;
    window.clearTimeout(pendingCloseRef.current);
    pendingCloseRef.current = undefined;
  }, []);
  const openCard = useCallback(() => {
    if (open) return;
    setOpen(true);
    if (!summaryLoading) onLoadSummary?.();
  }, [open, onLoadSummary, summaryLoading]);
  const closeCard = useCallback(() => {
    cancelPendingClose();
    setOpen(false);
  }, [cancelPendingClose]);
  const scheduleClose = useCallback(() => {
    cancelPendingClose();
    pendingCloseRef.current = window.setTimeout(() => setOpen(false), 150);
  }, [cancelPendingClose]);
  useEffect(() => cancelPendingClose, [cancelPendingClose]);
  const baseAvatar = avatarContent(author, size);
  if (!onLoadSummary || currentUserId === undefined) return baseAvatar;
  const isSelf = author.id === currentUserId && author.type === 'human';

  return (
    <Popover open={open} onOpenChange={(next) => (next ? openCard() : closeCard())}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-full p-0 focus-visible:ring-2 focus-visible:ring-primary"
          style={{ width: size, height: size }}
          aria-label={`查看${author.displayName}资料`}
          onMouseEnter={() => { openedByPointer.current = true; openCard(); }}
          onMouseLeave={scheduleClose}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            openedByPointer.current = event.detail > 0;
            openCard();
          }}
        >
          {baseAvatar}
        </Button>
      </PopoverTrigger>
      <PopoverContent onOpenAutoFocus={(event) => { if (openedByPointer.current) event.preventDefault(); }} sideOffset={16} className="w-[360px] max-w-[calc(100vw-24px)] rounded-xl border-0 px-3 pb-4 pt-0 shadow-lg" onMouseEnter={cancelPendingClose} onMouseLeave={closeCard}>
        <div className="relative flex h-[68px] items-center border-b border-divider pl-[84px]">
          <div className="absolute -top-3 left-0">{avatarContent(author, 68)}</div>
          <div className="min-w-0 flex-1">
            <p className="m-0 truncate text-sm font-semibold text-foreground">{author.displayName}</p>
            <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
              {author.type === 'bot' ? 'BOT' : 'HUMAN'} · {author.id}
            </p>
          </div>
        </div>
        {summaryLoading ? (
          <p className="m-0 mt-3 text-xs text-muted-foreground">统计加载中...</p>
        ) : (
          <dl className="m-0 mt-4 grid grid-cols-4 text-center text-xs text-muted-foreground">
            {profileStats(summary).map(({ label, count }) => <div key={label}><dt>{label}</dt><dd className="m-0 text-base leading-[21px] text-foreground">{count}</dd></div>)}
          </dl>
        )}
        {!isSelf && onFollow ? (
          <Button
            variant={followed ? "secondary" : "primary"}
            size="sm"
            className={`group mt-4 h-8 w-full justify-center border-0 px-3 text-sm focus-visible:ring-primary ${followed ? 'bg-muted' : ''}`}
            leftIcon={
              followed ? (
                <CommunityFollowIcon className="h-4 w-4" aria-hidden />
              ) : (
                <Bell className="h-4 w-4" aria-hidden />
              )
            }
            loading={followLoading}
            aria-label={followed ? "已关注" : "关注"}
            aria-pressed={followed}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onFollow();
            }}
          >
            {followed ? <><span className="group-hover:hidden group-focus-visible:hidden">已关注</span><span className="hidden group-hover:inline group-focus-visible:inline">取消关注</span></> : '关注'}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
