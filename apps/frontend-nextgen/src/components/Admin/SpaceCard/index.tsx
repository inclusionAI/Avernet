// 空间卡片：保留 Figma 布局，统计显示成员、更新时间和创建者。
import { Button } from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { cn } from '@/utils/cn';
import { formatRelativeTime } from '@/utils/format';
import { useState } from 'react';
import { SpaceJoinForm } from '../SpaceJoinForm';

export interface SpaceCardProps {
  space: Space;
  isCurrent?: boolean;
  onOpenDetail?: (space: Space) => void;
  onRequestJoin?: (space: Space, reason: string) => void | boolean | Promise<void | boolean>;
}

function Stat({ label, value }: { label: string; value?: number | string }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center gap-0.5">
      <span className="text-xs leading-[18px] text-muted-foreground">{label}</span>
      <span className="w-full truncate px-2 text-center text-[13px] leading-5 text-foreground">
        {value === '' || value === undefined ? '—' : value}
      </span>
    </div>
  );
}

export function SpaceCard({ space, isCurrent, onOpenDetail, onRequestJoin }: SpaceCardProps) {
  const personal = space.spaceType === 'PERSONAL';
  const isMember =
    space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER' || space.joinStatus === 'JOINED';
  const applying = !isMember && space.joinStatus === 'APPLYING';
  const joinable = space.spaceType === 'TEAM' && !isMember && !applying;
  const viewable = isMember || (personal && space.isCreator === true);
  const [joinOpen, setJoinOpen] = useState(false);

  return (
    <>
      <section className="relative flex h-[188px] min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card transition-shadow hover:shadow-sm">
        {viewable && (
          <Button
            variant="ghost"
            aria-label={`查看空间 ${space.spaceName}`}
            className="absolute inset-0 z-[1] h-full w-full rounded-xl hover:bg-transparent outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            onClick={() => onOpenDetail?.(space)}
          />
        )}
        <div className="flex flex-1 flex-col gap-4 px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span
              aria-hidden
              className={cn(
                'flex size-10 shrink-0 items-center justify-center rounded-xl font-medium',
                personal ? 'bg-primary/10 text-[17px] text-primary' : 'bg-emerald-500/10 text-[17px] text-emerald-600',
              )}
            >
              {personal ? 'P' : 'G'}
            </span>
            <h2 className="min-w-0 truncate text-sm font-medium leading-[22px]" title={space.spaceName}>
              {space.spaceName}
            </h2>
          </div>
          <div className="flex items-center divide-x divide-border">
            <Stat label="成员" value={space.memberCount} />
            <Stat label="更新时间" value={space.gmtModified ? formatRelativeTime(space.gmtModified) : undefined} />
            <Stat label="创建者" value={space.creatorUserName} />
          </div>
        </div>
        <div className="flex h-14 shrink-0 items-center justify-end gap-2 border-t border-border/50 bg-primary/[0.035] px-5">
          {!personal && isMember && (
            <span className="mr-auto rounded bg-emerald-500/10 px-2 text-xs leading-5 text-emerald-700">已加入</span>
          )}
          {isCurrent && <span className="rounded bg-primary/10 px-2 text-xs leading-5 text-primary">当前空间</span>}
          {joinable && (
            <Button
              size="sm"
              variant="secondary"
              className="relative z-10 h-7 px-3 text-[13px]"
              onClick={() => setJoinOpen(true)}
            >
              申请加入
            </Button>
          )}
          {applying && (
            <Button size="sm" variant="secondary" className="h-7 px-3 text-[13px]" disabled>
              申请中
            </Button>
          )}
        </div>
      </section>
      <SpaceJoinForm
        space={space}
        open={joinOpen}
        onOpenChange={setJoinOpen}
        onSubmit={async (reason) => {
          if (!onRequestJoin) return false;
          return (await onRequestJoin(space, reason)) !== false;
        }}
      />
    </>
  );
}

export default SpaceCard;
