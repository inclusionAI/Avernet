import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Bot, MessageCircleMore, Share, Users } from 'lucide-react';
import type { ReactElement } from 'react';

export interface GroupCardProps {
  group: PublicGroup;
  busy: boolean;
  onOpenMembers: (group: PublicGroup) => void;
  onShare: (group: PublicGroup) => void;
  onCreateSession: (group: PublicGroup) => void;
}

/** 群类型 → 徽章色调（dmore「任务协作」蓝底胶囊，其余类型沿用既有软色映射）。 */
function getGroupTypeTone(typeLabel: string): BadgeTone {
  if (typeLabel === '任务协作') return 'primary';
  if (typeLabel === '自定义协作') return 'purple';
  if (typeLabel === '自由聊天') return 'success';
  return 'neutral';
}

/** 群类型胶囊（dmore 标签 65×20、29px 全圆角、11px 文本）；卡头右上与分享弹窗预览条共用。 */
export function GroupTypeBadge({ typeLabel }: { typeLabel: string }) {
  return <Badge tone={getGroupTypeTone(typeLabel)}>{typeLabel}</Badge>;
}

function GroupTextTooltip({ text, children }: { text: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className="max-w-[min(24rem,calc(100vw-2rem))] whitespace-normal break-words">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * 公开协作群卡片（dmore 协作群卡片 268×148）：白底、16px 圆角、1px #EEE 描边、无底阴影；
 * 头部为「群名称 + 右上类型胶囊」，其下为 12px 群主 Bot 灰字行与描述，底部为 0.5px 分割线
 * 上方「成员」文字 chip 与右侧 32px 方角图标按钮（默认无底色，hover 才出灰底）。
 */
export default function GroupCard({ group, busy, onOpenMembers, onShare, onCreateSession }: GroupCardProps) {
  const ownerBotLabel =
    group.driverBotUuid && group.ownerBotName !== group.driverBotUuid
      ? `${group.ownerBotName} (${group.driverBotUuid})`
      : group.ownerBotName;
  return (
    <TooltipProvider delayDuration={200}>
      <Card
        className={cn(
          // dmore：卡片 148px 定高节奏，白底描边取代阴影；描边走 --divider（#EEE）。
          'flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4 pb-3',
          'transition-shadow hover:shadow-sm',
        )}
      >
        {/* 头部行：群名称（14px/600）+ 右上类型胶囊（dmore Container@17,17）。 */}
        <div className="flex min-w-0 items-start justify-between gap-2">
          <GroupTextTooltip text={group.name}>
            <span tabIndex={0} className="min-w-0 flex-1 truncate text-sm font-semibold leading-[18px] text-foreground">
              {group.name}
            </span>
          </GroupTextTooltip>
          <span className="shrink-0">
            <GroupTypeBadge typeLabel={group.typeLabel} />
          </span>
        </div>
        {/* 群主 Bot 行（公开目录不提供 Owner 用户）：12px 灰字 + 12px 小图标，与 BotCard 元信息行同规格。 */}
        <div className="mt-1.5 flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
          <GroupTextTooltip text={ownerBotLabel}>
            <span tabIndex={0} className="flex min-w-0 flex-1 items-center gap-1">
              <Bot aria-hidden className="h-3 w-3 shrink-0" />
              <span className="sr-only">群主 Bot：</span>
              <span className="truncate">{ownerBotLabel}</span>
            </span>
          </GroupTextTooltip>
        </div>
        {/* 群描述（dmore 灰字 12px/18）。 */}
        <GroupTextTooltip text={group.goal || '暂无描述'}>
          <p tabIndex={0} className="m-0 mt-3 line-clamp-2 break-words text-xs leading-[18px] text-muted-foreground">
            {group.goal || '暂无描述'}
          </p>
        </GroupTextTooltip>
        {/* 底部行（dmore 0.5px 分割线上方）：左「成员 N」文字 chip + 右 hover-only 图标按钮组。 */}
        <div className="mt-auto flex items-center justify-between gap-3 border-t border-divider pt-2.5">
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${group.memberCount} 位成员`}
            onClick={() => onOpenMembers(group)}
            leftIcon={<Users aria-hidden className="h-3 w-3 shrink-0" />}
            className="h-6 min-w-0 gap-1 rounded-md px-1 text-xs font-normal text-muted-foreground"
          >
            成员 {group.memberCount}
          </Button>
          <div className="flex shrink-0 items-center gap-1">
            <IconButton
              label={`分享 ${group.name}`}
              icon={<Share aria-hidden className="h-[18px] w-[18px]" />}
              onClick={() => onShare(group)}
              className="h-8 w-8 rounded-lg text-foreground"
            />
            {group.canCreateSession ? (
              <IconButton
                label="创建新会话"
                loading={busy}
                icon={<MessageCircleMore aria-hidden className="h-[18px] w-[18px]" />}
                onClick={() => onCreateSession(group)}
                className="h-8 w-8 rounded-lg text-foreground"
              />
            ) : (
              <GroupTextTooltip text="当前群暂不允许创建新会话">
                <span tabIndex={0} className="inline-flex">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="创建新会话"
                    disabled
                    onClick={() => onCreateSession(group)}
                    className="h-8 w-8 rounded-lg text-foreground"
                    leftIcon={<MessageCircleMore aria-hidden className="h-[18px] w-[18px]" />}
                  />
                </span>
              </GroupTextTooltip>
            )}
          </div>
        </div>
      </Card>
    </TooltipProvider>
  );
}

/**
 * 群卡加载骨架：逐区镜像真实群卡（dmore artboard-004）——头部名称行 + 右上 65×20 类型胶囊位、
 * 群主 Bot 灰字行、两行描述、分割线底部行（左侧「成员」chip 位 + 右侧 32px 图标钮位），
 * loading→loaded 切换时零跳版。占位块统一走 ui/Skeleton（脉冲叠色），容器与真卡同描边规格。
 */
export function GroupCardSkeleton() {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4 pb-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <Skeleton.Line className="h-[18px] min-w-0 flex-1" />
        <Skeleton.Block className="h-5 w-[65px] shrink-0 rounded-full" />
      </div>
      <div className="mt-1.5 flex items-center gap-3">
        <Skeleton.Line className="w-1/3" />
      </div>
      <div className="mt-3 space-y-1.5">
        <Skeleton.Line className="h-[18px]" />
        <Skeleton.Line className="h-[18px] w-3/4" />
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-divider pt-2.5">
        <Skeleton.Block className="h-6 w-14 rounded-md" />
        <div className="flex shrink-0 items-center gap-1">
          <Skeleton.Block className="h-8 w-8 rounded-lg" />
          <Skeleton.Block className="h-8 w-8 rounded-lg" />
        </div>
      </div>
    </div>
  );
}
