import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { Bot, MessageCircleMore, Share2, UserRound, Users } from 'lucide-react';
import type { ReactElement } from 'react';

export interface GroupCardProps {
  group: PublicGroup;
  busy: boolean;
  onOpenMembers: (group: PublicGroup) => void;
  onShare: (group: PublicGroup) => void;
  onCreateSession: (group: PublicGroup) => void;
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

export default function GroupCard({ group, busy, onOpenMembers, onShare, onCreateSession }: GroupCardProps) {
  const ownerBotLabel =
    group.driverBotUuid && group.ownerBotName !== group.driverBotUuid
      ? `${group.ownerBotName} (${group.driverBotUuid})`
      : group.ownerBotName;
  const typeTone = group.typeLabel === '任务协作' ? 'primary'
    : group.typeLabel === '自定义协作' ? 'purple'
    : group.typeLabel === '自由聊天' ? 'success' : 'neutral';
  return (
    <TooltipProvider delayDuration={200}>
      <Card className="flex h-full min-h-[210px] min-w-0 flex-col rounded-[22px] border-0 bg-muted/70 shadow-none transition-shadow hover:shadow-sm">
        <CardHeader className="items-start gap-3 p-5 pb-0">
          <CardTitle className="min-w-0 flex-1 text-base">
            <GroupTextTooltip text={group.name}>
              <span tabIndex={0} className="block truncate">{group.name}</span>
            </GroupTextTooltip>
          </CardTitle>
          <Badge tone={typeTone} className="shrink-0 whitespace-nowrap">{group.typeLabel}</Badge>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-4 px-5 pb-4 pt-2">
          <div className="flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
            <GroupTextTooltip text={group.ownerUserName || '未公开'}>
              <span tabIndex={0} className="flex min-w-0 max-w-[40%] items-center gap-1">
                <UserRound aria-hidden className="h-3.5 w-3.5 shrink-0" />
                <span className="sr-only">Owner 用户：</span>
                <span className="truncate">{group.ownerUserName || '未公开'}</span>
              </span>
            </GroupTextTooltip>
            <GroupTextTooltip text={ownerBotLabel}>
              <span tabIndex={0} className="flex min-w-0 flex-1 items-center gap-1">
                <Bot aria-hidden className="h-3.5 w-3.5 shrink-0" />
                <span className="sr-only">群主 Bot：</span>
                <span className="truncate">{ownerBotLabel}</span>
              </span>
            </GroupTextTooltip>
          </div>
          <GroupTextTooltip text={group.goal || '暂无描述'}>
            <p tabIndex={0} className="m-0 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">
              {group.goal || '暂无描述'}
            </p>
          </GroupTextTooltip>
        </CardContent>
        <div className="mx-5 mt-auto flex items-center justify-between gap-3 border-t border-divider/40 py-4">
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${group.memberCount} 位成员`}
            className="h-10 min-w-0 gap-1.5 px-0 text-sm font-normal hover:bg-transparent hover:text-primary"
            onClick={() => onOpenMembers(group)}
            leftIcon={<Users aria-hidden className="h-4 w-4 shrink-0" />}
          >
            成员 <span className="text-muted-foreground">·</span> {group.memberCount}
          </Button>
          <div className="flex shrink-0 items-center gap-2">
            <IconButton
              label={`分享 ${group.name}`}
              icon={<Share2 aria-hidden className="h-4 w-4" />}
              onClick={() => onShare(group)}
              className="h-10 w-10 rounded-xl bg-background text-foreground"
            />
            {group.canCreateSession ? (
              <IconButton
                label="创建新会话"
                loading={busy}
                icon={<MessageCircleMore aria-hidden className="h-5 w-5" />}
                onClick={() => onCreateSession(group)}
                className="h-10 w-10 rounded-xl bg-background text-foreground"
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
                    className="h-10 w-10 rounded-xl bg-background text-foreground"
                    leftIcon={<MessageCircleMore aria-hidden className="h-5 w-5" />}
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
