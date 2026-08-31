import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { Bot, MessageCircle, Share2, UserPlus } from 'lucide-react';

export interface SquareBotCardProps {
  bot: PublicBot;
  busy: boolean;
  onShare: (bot: PublicBot) => void;
  onPrimaryAction: (bot: PublicBot) => void;
}

export default function SquareBotCard({ bot, busy, onShare, onPrimaryAction }: SquareBotCardProps) {
  const applying = bot.relationshipStatus === 'applying';
  const friend = bot.relationshipStatus === 'friend';
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary-soft)] text-[var(--color-primary)]">
            <Bot aria-hidden className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <CardTitle className="truncate" title={bot.name}>
              {bot.name}
            </CardTitle>
          </div>
        </div>
        {applying && <Badge tone="warning">申请中</Badge>}
        {friend && <Badge tone="success">好友 Bot</Badge>}
      </CardHeader>
      <CardContent className="flex-1 space-y-4">
        <p className="m-0 text-sm leading-6 text-[var(--color-muted)]">{bot.description}</p>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 rounded-lg bg-[var(--color-panel-muted)] p-3 text-xs">
          <dt className="font-medium text-[var(--color-muted)]">Owner 用户</dt>
          <dd className="m-0 truncate text-[var(--color-fg)]" title={bot.ownerName}>
            {bot.ownerName}
          </dd>
          <dt className="font-medium text-[var(--color-muted)]">Bot ID</dt>
          <dd className="m-0 truncate text-[var(--color-fg)]" tabIndex={0} title={bot.id}>
            {bot.id}
          </dd>
        </dl>
      </CardContent>
      <CardFooter className="flex-wrap justify-between">
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
          loading={busy}
          disabled={applying}
          onClick={() => onPrimaryAction(bot)}
          leftIcon={
            friend ? <MessageCircle aria-hidden className="h-4 w-4" /> : <UserPlus aria-hidden className="h-4 w-4" />
          }
        >
          {friend ? '立即开始对话' : applying ? '申请中' : '申请好友权限'}
        </Button>
      </CardFooter>
    </Card>
  );
}
