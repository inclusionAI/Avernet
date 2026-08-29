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
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="truncate" title={group.name}>
            {group.name}
          </CardTitle>
          <p className="mt-1 text-xs text-[var(--color-muted)]">群主 Bot · {group.ownerBotName}</p>
        </div>
        <Badge>{group.typeLabel}</Badge>
      </CardHeader>
      <CardContent className="flex-1 space-y-3 text-sm">
        <p className="m-0 text-[var(--color-muted)]">Owner用户 · {group.ownerUserName}</p>
        <p className="m-0 leading-6 text-[var(--color-muted)]">协作目标 · {group.goal}</p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onOpenMembers(group)}
          leftIcon={<Users aria-hidden className="h-4 w-4" />}
        >
          {group.memberCount} 位成员
        </Button>
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
