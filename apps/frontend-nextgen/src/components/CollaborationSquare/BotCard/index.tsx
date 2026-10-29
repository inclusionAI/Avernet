import { Badge } from '@/components/ui/Badge';
import { IconButton } from '@/components/ui/IconButton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  getPublicBotTargetId,
  resolvePublicBotPrimaryAction,
  type FriendRequestActor,
  type PublicBot,
} from '@/domain/collaborationSquare/types';
import { Bot, MessageCircleMore, Plus, Share2, UserRound } from 'lucide-react';
import type { ReactElement } from 'react';

/** 不改变卡片尺寸，悬浮或聚焦时展示完整文本。 */
function FullTextTooltip({ text, children }: { text: string; children: ReactElement }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-[min(24rem,calc(100vw-2rem))] whitespace-normal break-words text-left leading-5">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export interface SquareBotCardProps {
  bot: PublicBot;
  activeActor: FriendRequestActor;
  busy: boolean;
  /** 深链定位高亮：卡片被标记为分享深链的定位目标时短暂高亮。 */
  highlighted?: boolean;
  onShare: (bot: PublicBot) => void;
  onPrimaryAction: (bot: PublicBot) => void;
}

export default function SquareBotCard({ bot, activeActor, busy, highlighted, onShare, onPrimaryAction }: SquareBotCardProps) {
  const applying = bot.relationshipStatus === 'applying';
  const owned = bot.isOwnedByLoggedInUser === true;
  const friend = bot.relationshipStatus === 'friend';
  const primaryAction = resolvePublicBotPrimaryAction({
    activeActor,
    targetActorId: getPublicBotTargetId(bot),
    relationshipStatus: bot.relationshipStatus,
    isOwnedByLoggedInUser: bot.isOwnedByLoggedInUser,
  });
  const canStartConversation = primaryAction === 'open_human_bot_conversation';
  const primaryDisabled =
    primaryAction === 'applying' || primaryAction === 'friendship_established' || primaryAction === 'self_target';
  const primaryLabel =
    primaryAction === 'open_human_bot_conversation'
      ? '立即开始对话'
      : primaryAction === 'applying'
      ? '申请中'
      : primaryAction === 'friendship_established'
      ? '已是好友'
      : primaryAction === 'self_target'
      ? '当前 Bot'
      : '申请好友权限';
  const description = bot.description.trim() || '暂无描述';
  const descriptionText = (
    <p tabIndex={0} className="m-0 min-h-10 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">{description}</p>
  );
  return (
    <Card
      data-bot-id={bot.id}
      aria-current={highlighted ? 'true' : undefined}
      className={`flex h-full min-h-[156px] min-w-0 flex-col rounded-[22px] border-0 shadow-none transition-[box-shadow,background-color] duration-500 ${
        highlighted ? 'bg-primary/5 ring-2 ring-primary' : 'bg-muted/70'
      }`}
    >
      <CardHeader className="items-start gap-3 p-5 pb-0">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-background text-primary">
            <Bot aria-hidden className="h-8 w-8" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <CardTitle className="min-w-0 text-base">
                <FullTextTooltip text={bot.name}>
                  <span tabIndex={0} className="block truncate">{bot.name}</span>
                </FullTextTooltip>
              </CardTitle>
            </div>
            <div className="mt-1 flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
              <FullTextTooltip text={bot.ownerName}>
                <span tabIndex={0} className="flex min-w-0 max-w-[40%] items-center gap-1">
                  <UserRound aria-hidden className="h-3.5 w-3.5 shrink-0" />
                  <span className="sr-only">Owner 用户：</span>
                  <span className="truncate">{bot.ownerName}</span>
                </span>
              </FullTextTooltip>
              <FullTextTooltip text={bot.id}>
                <span className="flex min-w-0 flex-1 items-center gap-1" tabIndex={0}>
                  <Bot aria-hidden className="h-3.5 w-3.5 shrink-0" />
                  <span className="sr-only">Bot UUID：</span>
                  <span className="truncate">{bot.id}</span>
                </span>
              </FullTextTooltip>
            </div>
          </div>
        </div>
        <div className="ml-auto flex shrink-0 flex-wrap justify-end gap-2">
          {owned && <Badge tone="success">我的 Bot</Badge>}
          {primaryAction === 'self_target' ? (
            <Badge tone="neutral">当前 Bot</Badge>
          ) : applying ? (
            <Badge tone="warning">申请中</Badge>
          ) : friend ? (
            <Badge tone="primary">好友Bot</Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-2 px-5 pb-0 pt-3">
        {bot.description.trim() ? (
          <FullTextTooltip text={description}>{descriptionText}</FullTextTooltip>
        ) : (
          descriptionText
        )}
        {bot.shortProfile && (
          <FullTextTooltip text={`Profile：${bot.shortProfile}`}>
            <p tabIndex={0} className="m-0 line-clamp-2 break-words text-xs text-muted-foreground">
              <span className="font-medium">Profile：</span>
              {bot.shortProfile}
            </p>
          </FullTextTooltip>
        )}
      </CardContent>
        <div className="mt-auto flex shrink-0 items-center justify-end gap-2 px-5 pb-5 pt-3">
          <IconButton
            label={`分享 ${bot.name}`}
            icon={<Share2 aria-hidden className="h-4 w-4" />}
            onClick={() => onShare(bot)}
            className="h-10 w-10 rounded-xl bg-background text-foreground"
          />
          {!primaryDisabled && (
            <IconButton
              label={primaryLabel}
              loading={busy}
              icon={
                canStartConversation ? (
                  <MessageCircleMore aria-hidden className="h-5 w-5" />
                ) : (
                  <Plus aria-hidden className="h-6 w-6" />
                )
              }
              onClick={() => onPrimaryAction(bot)}
              className="h-10 w-10 rounded-xl bg-background text-foreground"
            />
          )}
        </div>
    </Card>
  );
}
