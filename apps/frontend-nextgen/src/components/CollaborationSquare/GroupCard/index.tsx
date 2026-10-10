import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { MessageSquarePlus, Share2, Users } from 'lucide-react';

export interface GroupCardProps {
  group: PublicGroup;
  busy: boolean;
  onOpenMembers: (group: PublicGroup) => void;
  onShare: (group: PublicGroup) => void;
  onCreateSession: (group: PublicGroup) => void;
}

export default function GroupCard({ group, busy, onOpenMembers, onShare, onCreateSession }: GroupCardProps) {
  const ownerBotLabel =
    group.driverBotUuid && group.ownerBotName !== group.driverBotUuid
      ? `${group.ownerBotName} (${group.driverBotUuid})`
      : group.ownerBotName;
  const createSessionButton = (
    <Button
      loading={busy}
      disabled={!group.canCreateSession}
      onClick={() => onCreateSession(group)}
      leftIcon={<MessageSquarePlus aria-hidden className="h-4 w-4" />}
    >
      创建新会话
    </Button>
  );
  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="items-center">
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate" title={group.name}>
            {group.name}
          </CardTitle>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-auto rounded-full border-0 p-0 leading-normal hover:opacity-80"
            onClick={() => onOpenMembers(group)}
          >
            <Badge className="gap-1 whitespace-nowrap">
              <Users aria-hidden className="size-3" />
              {group.memberCount} 位成员
            </Badge>
          </Button>
          <Badge>{group.typeLabel}</Badge>
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-4">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 rounded-lg bg-muted p-3 text-xs">
          <dt className="font-medium text-muted-foreground">群主 Bot</dt>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <dd className="m-0 min-w-0 truncate text-foreground" tabIndex={0}>
                  {ownerBotLabel}
                </dd>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm break-all">{ownerBotLabel}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
          <dt className="font-medium text-muted-foreground">协作目标</dt>
          <dd className="m-0 min-w-0 line-clamp-2 text-foreground" title={group.goal}>
            {group.goal || '—'}
          </dd>
        </dl>
      </CardContent>
      <CardFooter className="flex-wrap justify-between">
        <Button
          variant="ghost"
          size="sm"
          aria-label={`分享 ${group.name}`}
          onClick={() => onShare(group)}
          leftIcon={<Share2 aria-hidden className="h-4 w-4" />}
        >
          分享
        </Button>
        {group.canCreateSession ? (
          createSessionButton
        ) : (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">{createSessionButton}</span>
              </TooltipTrigger>
              <TooltipContent>当前群暂不允许创建新会话</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </CardFooter>
    </Card>
  );
}
