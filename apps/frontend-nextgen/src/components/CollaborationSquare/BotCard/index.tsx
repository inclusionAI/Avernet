import { getCapabilities } from '@/capabilities';
import { Badge } from '@/components/ui/Badge';
import { Avatar } from '@/components/ui/Avatar';
import { Card } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import {
  getPublicBotTargetId,
  resolvePublicBotPrimaryAction,
  type FriendRequestActor,
  type PublicBot,
} from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Hash, MessageCircleMore, Plus, Share2, UserRound } from 'lucide-react';
import type { ReactElement } from 'react';

// 公开Bot 卡片：与公开协作群卡/专家市场卡同一套广场卡语言——白底、16px 圆角、
// 1px #EEE 描边（border-divider）、无静态阴影，hover 才出 shadow-sm
// （dmore 原稿的双层常驻软阴影已随描边方案废弃，三个 Tab 卡面统一）。

// 右上角状态 chip（dmore「申请中」55×32）：6px 圆角、警示橙软底。
const STATUS_CHIP = 'flex h-8 items-center rounded-md px-2 text-xs';

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
    // BCS-only（Backend 关闭）时 Human 主操作转为「已是好友」展示，不再提供 Backend 私聊入口。
    backendChatEnabled: getCapabilities().getPublicBotBackendEnabled(),
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
    <p
      tabIndex={0}
      className="m-0 line-clamp-2 min-h-9 break-words text-xs leading-[18px] text-muted-foreground"
    >
      {description}
    </p>
  );
  return (
    <Card
      data-bot-id={bot.id}
      aria-current={highlighted ? 'true' : undefined}
      className={cn(
        'flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4 transition-shadow hover:shadow-sm',
        // 深链定位高亮：蓝环 + 常驻 shadow-sm，目标卡无需 hover 也立得住。
        highlighted ? 'ring-2 ring-primary shadow-sm' : '',
      )}
    >
      {/* 头部行：40px 圆形头像 + 名称/身份元信息 + 右侧操作（dmore Container@(17,17)）。 */}
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Avatar name={bot.name} src={bot.avatarUrl} size={40} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <FullTextTooltip text={bot.name}>
              <span tabIndex={0} className="min-w-0 truncate text-sm font-semibold text-foreground">
                {bot.name}
              </span>
            </FullTextTooltip>
            {/* 关系 pill 紧跟名称（dmore「好友Bot」54×18 蓝色胶囊）。 */}
            {owned && <Badge tone="success" className="shrink-0">我的 Bot</Badge>}
            {primaryAction === 'self_target' && <Badge tone="neutral" className="shrink-0">当前 Bot</Badge>}
            {friend && <Badge tone="primary" className="shrink-0">好友Bot</Badge>}
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
            <FullTextTooltip text={bot.ownerName}>
              <span tabIndex={0} className="flex min-w-0 max-w-[40%] items-center gap-1">
                <UserRound aria-hidden className="h-3 w-3 shrink-0" />
                <span className="sr-only">Owner 用户：</span>
                <span className="truncate">{bot.ownerName}</span>
              </span>
            </FullTextTooltip>
            <FullTextTooltip text={bot.id}>
              <span className="flex min-w-0 flex-1 items-center gap-1" tabIndex={0}>
                <Hash aria-hidden className="h-3 w-3 shrink-0" />
                <span className="sr-only">Bot UUID：</span>
                <span className="truncate">{bot.id}</span>
              </span>
            </FullTextTooltip>
          </div>
        </div>
        {/* 右上角操作区（dmore Frame 9）：可操作时为图标按钮组，等待/命中自身时为状态 chip。 */}
        <div className="ml-auto flex shrink-0 items-center justify-end gap-2">
          {applying ? (
            <span className={cn(STATUS_CHIP, 'bg-warning/10 font-normal text-warning')}>申请中</span>
          ) : primaryAction === 'friendship_established' ? (
            <span className={cn(STATUS_CHIP, 'bg-foreground/[0.06] font-normal text-muted-foreground')}>
              {primaryLabel}
            </span>
          ) : primaryDisabled ? null : (
            <>
              <IconButton
                label={`分享 ${bot.name}`}
                icon={<Share2 aria-hidden className="h-4.5 w-4.5" />}
                onClick={() => onShare(bot)}
                className="h-8 w-8 rounded-lg text-foreground"
              />
              {/* 主操作 32px 方钮：默认无背景（透明），仅 hover 出灰底（ghost 原生 hover:bg-accent）。 */}
              <IconButton
                label={primaryLabel}
                loading={busy}
                data-guide-target="discover-add-friend-button"
                icon={
                  canStartConversation ? (
                    <MessageCircleMore aria-hidden className="h-4.5 w-4.5" />
                  ) : (
                    <Plus aria-hidden className="h-5 w-5" />
                  )
                }
                onClick={() => onPrimaryAction(bot)}
                className="h-8 w-8 rounded-lg text-foreground"
              />
            </>
          )}
        </div>
      </div>
      <div className="mt-3 flex min-w-0 flex-1 flex-col gap-2">
        {bot.description.trim() ? (
          <FullTextTooltip text={description}>{descriptionText}</FullTextTooltip>
        ) : (
          descriptionText
        )}
        {bot.shortProfile && (
          <FullTextTooltip text={`Profile：${bot.shortProfile}`}>
            <p tabIndex={0} className="m-0 line-clamp-2 break-words text-[11px] text-muted-foreground">
              <span className="font-medium">Profile：</span>
              {bot.shortProfile}
            </p>
          </FullTextTooltip>
        )}
      </div>
    </Card>
  );
}

/**
 * 公开 Bot 卡加载骨架：逐区镜像真实 Bot 卡——40px 圆形头像位 +
 * 名称行/元信息灰字行、右上 32px 图标钮位、两行描述与 Profile 行；卡面描边静止态与
 * 真实卡一致（无静态阴影），loading→loaded 切换时零跳版。占位块统一走 ui/Skeleton（脉冲叠色）。
 */
export function SquareBotCardSkeleton() {
  return (
    <div className="flex h-full min-w-0 flex-col rounded-2xl border border-divider bg-card p-4">
      <div className="flex items-start gap-3">
        <Skeleton.Block className="h-10 w-10 shrink-0 rounded-full" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <Skeleton.Line className="h-[18px] w-2/5" />
          <Skeleton.Line className="w-3/5" />
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <Skeleton.Block className="h-8 w-8 rounded-lg" />
          <Skeleton.Block className="h-8 w-8 rounded-lg" />
        </div>
      </div>
      <div className="mt-3 space-y-2">
        <Skeleton.Line className="h-[18px]" />
        <Skeleton.Line className="h-[18px] w-5/6" />
        <Skeleton.Line className="h-3 w-2/3" />
      </div>
    </div>
  );
}
