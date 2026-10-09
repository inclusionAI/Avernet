import { Badge } from '@/components/ui/Badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';
import { Copy, RefreshCw } from 'lucide-react';
import { BotAbilitySettings, BotVisibilitySettings, FriendApprovalSettings } from '../settingsSections';

interface PermissionCardProps {
  bot: CollaborationBot;
  busyAction: string | null;
  onCopyId: (botId: string) => void;
  onRefresh: (bot: CollaborationBot) => void;
  onToggleDirect: (bot: CollaborationBot, setting: DirectSetting, value: boolean | 'online' | 'hidden') => void;
  onEditPublication: (bot: CollaborationBot, audience: PublicAudience) => void;
  onEditFriendApproval: (bot: CollaborationBot) => void;
  onViewScope: (bot: CollaborationBot, audience: PublicAudience) => void;
  onViewFriendApprovalScope: (bot: CollaborationBot) => void;
}

/**
 * 协作权限页面的 Bot 权限卡。
 * 三组配置分区（协作能力 / Bot 可见性 / Bot 好友审批）已提取为 settingsSections 共享组件，
 * 供 BotWorkshop「通用配置」弹窗复用（collab-permission-entry-migration）。
 */
export function PermissionCard({
  bot,
  busyAction,
  onCopyId,
  onRefresh,
  onToggleDirect,
  onEditPublication,
  onEditFriendApproval,
  onViewScope,
  onViewFriendApprovalScope,
}: PermissionCardProps) {
  const disabledReason = bot.joinedBcn ? undefined : '加入 BCN 后才能修改协作权限';
  const refreshBusy = busyAction === `${bot.id}:refresh`;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-border pb-5">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <CardTitle className="min-w-0 truncate text-lg" title={bot.name}>
              {bot.name}
            </CardTitle>
            {bot.engine !== 'unknown' && <Badge tone="neutral">{bot.engine}</Badge>}
            <IconButton
              className="shrink-0"
              label={`刷新 ${bot.name} 的权限状态`}
              icon={<RefreshCw className={`h-3.5 w-3.5${refreshBusy ? ' animate-spin' : ''}`} aria-hidden />}
              size="sm"
              disabled={Boolean(busyAction)}
              onClick={() => onRefresh(bot)}
            />
          </div>
          <div className="mt-3 flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-xs font-medium text-muted-foreground">Bot UUID</span>
            <code
              className="min-w-0 max-w-[48rem] truncate rounded-md bg-muted/30 px-2 py-1 text-xs text-foreground"
              title={bot.id}
            >
              {bot.id}
            </code>
            <IconButton
              className="shrink-0"
              label={`复制 ${bot.name} 的 Bot UUID`}
              icon={<Copy className="h-3.5 w-3.5" aria-hidden />}
              size="sm"
              onClick={() => onCopyId(bot.id)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {disabledReason && (
          <div className="border-y border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
            {disabledReason}
          </div>
        )}
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <section className="min-w-0">
            <BotAbilitySettings bot={bot} busyAction={busyAction} onToggleDirect={onToggleDirect} />
          </section>
          <div className="min-w-0 space-y-6 lg:border-l lg:border-border lg:pl-6">
            <section>
              <BotVisibilitySettings bot={bot} onEditPublication={onEditPublication} onViewScope={onViewScope} />
            </section>
            <section className="border-t border-border pt-5">
              <FriendApprovalSettings
                bot={bot}
                onEditFriendApproval={onEditFriendApproval}
                onViewFriendApprovalScope={onViewFriendApprovalScope}
              />
            </section>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
