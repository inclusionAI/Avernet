import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { canStartPublicBotConversation, type PublicBot } from '@/domain/collaborationSquare/types';
import { Bot, MessageCircle, Share2, UserPlus } from 'lucide-react';

export interface SquareBotCardProps {
  bot: PublicBot;
  busy: boolean;
  onShare: (bot: PublicBot) => void;
  onPrimaryAction: (bot: PublicBot) => void;
}

export default function SquareBotCard({ bot, busy, onShare, onPrimaryAction }: SquareBotCardProps) {
  const applying = bot.relationshipStatus === 'applying';
  const owned = bot.isOwnedByViewer === true;
  const friend = bot.relationshipStatus === 'friend';
  const canStartConversation = canStartPublicBotConversation(bot);
  const description = bot.description.trim() || '暂无描述';
  const descriptionText = (
    <p className="m-0 min-h-10 line-clamp-2 break-words text-xs leading-5 text-muted-foreground">{description}</p>
  );
  return (
    <Card className="flex h-full min-w-0 flex-col overflow-hidden">
      <CardHeader className="p-4 pb-0">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Bot aria-hidden className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <CardTitle className="truncate" title={bot.name}>
              {bot.name}
            </CardTitle>
          </div>
        </div>
        {owned ? <Badge tone="success">我的 Bot</Badge> : applying ? <Badge tone="warning">申请中</Badge> : null}
        {!owned && friend && <Badge tone="success">已经是好友</Badge>}
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-4 p-4 pt-3">
        {bot.description.trim() ? (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>{descriptionText}</TooltipTrigger>
              <TooltipContent side="top" className="max-w-sm whitespace-normal break-words text-left leading-5">
                {description}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          descriptionText
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 rounded-lg bg-muted/60 p-3 text-xs">
          <dt className="font-medium text-muted-foreground">Owner 用户</dt>
          <dd className="m-0 truncate text-foreground" title={bot.ownerName}>
            {bot.ownerName}
          </dd>
          <dt className="font-medium text-muted-foreground">Bot UUID</dt>
          <dd className="m-0 truncate text-foreground" tabIndex={0} title={bot.id}>
            {bot.id}
          </dd>
          {bot.shortProfile && (
            <>
              <dt className="font-medium text-muted-foreground">Profile</dt>
              <dd className="m-0 line-clamp-2 text-foreground" title={bot.shortProfile}>
                {bot.shortProfile}
              </dd>
            </>
          )}
        </dl>
      </CardContent>
      <CardFooter className="flex-wrap justify-between gap-2 p-3">
        <Button
          variant="ghost"
          size="sm"
          aria-label={`分享 ${bot.name}`}
          onClick={() => onShare(bot)}
          leftIcon={<Share2 aria-hidden className="h-4 w-4" />}
        >
          分享
        </Button>
        <Button
          size="sm"
          loading={busy}
          disabled={applying && !owned}
          onClick={() => onPrimaryAction(bot)}
          leftIcon={
            canStartConversation ? (
              <MessageCircle aria-hidden className="h-4 w-4" />
            ) : (
              <UserPlus aria-hidden className="h-4 w-4" />
            )
          }
        >
          {canStartConversation ? '立即开始对话' : applying ? '申请中' : '申请好友权限'}
        </Button>
      </CardFooter>
    </Card>
  );
}
