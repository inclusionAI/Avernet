// 空间卡片（对齐 PRD Teamclaw_PRD_new/src/pages/Admin/index.tsx 空间管理）：
// - 标题行：图标(team Users 18/team=primary/个人=muted) + 名称 semibold；右侧类型+状态 tag(当前空间时叠加显示)
// - 统计块：成员 / 更新时间 / 创建者 三列（直接展示创建者花名，无类型条件），muted 底，分隔线，label 上 / 值 下
//   （后端 space 列表不再返回 bot_count，故改展示更新时间；创建者为花名字符串，更新时间为相对时间字符串）
// - 操作：当前/已加入无按钮(整卡可点进详情)；未加入团队=申请加入(primary, block)；申请中=申请中(disabled)
// - 当前空间高亮：primary 边 + soft 外发光；hover 2px lift。
import { Button } from '@/components/ui';
import type { Space } from '@/domain/admin/models';
import { cn } from '@/utils/cn';
import { formatRelativeTime } from '@/utils/format';
import { User, Users } from 'lucide-react';
import { useState } from 'react';
import { SpaceJoinForm } from '../SpaceJoinForm';
import { Tag } from '../Tag';

export interface SpaceCardProps {
  space: Space;
  isCurrent?: boolean;
  onOpenDetail?: (space: Space) => void;
  onRequestJoin?: (space: Space, reason: string) => void;
}

// 右侧 tag 组对齐 PRD：当前空间叠加「当前空间」+ 用户态(个人/已加入/申请中/可申请)，gap-1.5
function Tags({ space, isCurrent }: { space: Space; isCurrent?: boolean }) {
  const isMember = space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER';
  const membership =
    space.spaceType === 'PERSONAL' ? (
      <Tag>个人</Tag>
    ) : isMember ? (
      <Tag tone="green">已加入</Tag>
    ) : space.joinStatus === 'APPLYING' ? (
      <Tag tone="orange">申请中</Tag>
    ) : (
      <Tag tone="orange">可申请</Tag>
    );
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      {isCurrent && <Tag tone="blue">当前空间</Tag>}
      {membership}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  const displayValue = value || '-';
  return (
    <div className="flex flex-1 flex-col justify-center gap-1 px-4">
      <span className="text-[rgb(113,113,122)]">{label}</span>
      <span className="truncate">{displayValue}</span>
    </div>
  );
}

export function SpaceCard({ space, isCurrent, onOpenDetail, onRequestJoin }: SpaceCardProps) {
  const isMember = space.currentUserRole === 'ADMIN' || space.currentUserRole === 'MEMBER';
  const joinable = !isMember && space.joinStatus !== 'APPLYING';
  const applying = !isMember && space.joinStatus === 'APPLYING';
  const [joinOpen, setJoinOpen] = useState(false);
  const cardClickable = isMember || space.spaceType === 'TEAM';
  const Icon = space.spaceType === 'PERSONAL' ? User : Users;
  const iconColor = space.spaceType === 'PERSONAL' ? 'text-muted-foreground' : 'text-primary';

  return (
    <>
      <section
        className={cn(
          'group flex h-full flex-col gap-4 rounded-lg border border-border bg-card p-4 transition-all duration-200 ease-out',
          cardClickable && 'cursor-pointer hover:-translate-y-0.5 hover:shadow-md',
          isCurrent && 'border-primary ring-1 ring-primary/20',
        )}
        onClick={cardClickable ? () => onOpenDetail?.(space) : undefined}
        role={cardClickable ? 'button' : undefined}
        tabIndex={cardClickable ? 0 : undefined}
        onKeyDown={(e) => {
          if (cardClickable && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            onOpenDetail?.(space);
          }
        }}
      >
        {/* 标题行：色调图标 + 名称；右侧类型/状态 tag（当前空间叠加显示） */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className={cn(
                'inline-flex size-7 shrink-0 items-center justify-center rounded-full border border-border transition-colors',
                space.spaceType === 'PERSONAL' ? 'bg-muted' : 'bg-primary/10 group-hover:bg-primary/15',
              )}
              aria-hidden
            >
              <Icon size={12} className={iconColor} />
            </span>
            <span className="truncate text-[14px] font-semibold leading-5 text-foreground">{space.spaceName}</span>
          </div>
          <Tags space={space} isCurrent={isCurrent} />
        </div>

        {/* 统计块：成员 / 更新时间 / 创建者（直接展示花名，无类型条件） */}
        <div className="flex items-center divide-x divide-border py-1">
          <Stat label="成员" value={space.memberCount} />
          <Stat label="更新时间" value={formatRelativeTime(space.gmtModified)} />
          <Stat label="创建者" value={space.creatorUserName ?? ''} />
        </div>

        {/* 操作：未加入团队=申请加入 primary(block)；申请中=申请中 disabled；当前/已加入无按钮 */}
        <div className="mt-auto flex items-center">
          {joinable && (
            <Button
              size="sm"
              variant="secondary"
              className="w-full text-xs font-semibold"
              onClick={(e) => {
                e.stopPropagation();
                setJoinOpen(true);
              }}
            >
              申请加入
            </Button>
          )}
          {applying && (
            <Button size="sm" variant="secondary" className="w-full text-xs font-semibold" disabled>
              申请中
            </Button>
          )}
        </div>
      </section>
      {/* 申请加入 Modal（移出 section，避免点击冒泡触发卡片 onOpenDetail） */}
      <SpaceJoinForm
        space={space}
        open={joinOpen}
        onOpenChange={setJoinOpen}
        onSubmit={async (reason) => {
          await onRequestJoin?.(space, reason);
          return true;
        }}
      />
    </>
  );
}

export default SpaceCard;
