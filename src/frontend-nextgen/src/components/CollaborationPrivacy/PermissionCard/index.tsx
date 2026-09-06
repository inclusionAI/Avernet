import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import { Switch } from '@/components/ui/Switch';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';
import { Copy, Info, RefreshCw } from 'lucide-react';
import { RelationCard } from '../RelationCard';
import { RequestList } from '../RequestList';

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

interface SettingRowProps {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  busy: boolean;
  status?: string;
  statusReason?: string;
  onChange: (checked: boolean) => void;
}

function SettingRow({ label, description, checked, disabled, busy, status, statusReason, onChange }: SettingRowProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="m-0 text-sm font-medium text-foreground">{label}</p>
          {status && (
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 gap-1 px-1 text-muted-foreground"
                    aria-label={`${label}${status}说明`}
                  >
                    <Badge tone="neutral">{status}</Badge>
                    <Info className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </TooltipTrigger>
                {statusReason && <TooltipContent>{statusReason}</TooltipContent>}
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <Switch
        checked={checked}
        disabled={disabled || busy}
        aria-label={`${checked ? '关闭' : '开启'}${label}`}
        onCheckedChange={onChange}
      />
    </div>
  );
}

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
  const directBusy = (setting: DirectSetting) => busyAction === `${bot.id}:${setting}`;
  const friendDisabledByScope = bot.publication.user.scope === 'none' && bot.publication.bot.scope === 'none';
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
            <div className="mb-3 flex items-center">
              <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">协作能力</h4>
            </div>
            <div className="divide-y divide-border">
              <SettingRow
                label="参与协作群聊"
                description="控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。"
                checked={bot.collaborationStatus === 'online'}
                disabled={!bot.joinedBcn || bot.collaborationStatus === 'offline'}
                busy={directBusy('collaborationStatus')}
                onChange={(checked) => onToggleDirect(bot, 'collaborationStatus', checked ? 'online' : 'hidden')}
              />
              <SettingRow
                label="Bot 画像公开"
                description="允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察。"
                checked={bot.profilePublic}
                disabled={!bot.joinedBcn || bot.profilePublicStatus === 'unavailable'}
                busy={directBusy('profilePublic')}
                status={bot.profilePublicStatus === 'unavailable' ? '暂不可用' : undefined}
                statusReason={
                  bot.profilePublicStatus === 'unavailable'
                    ? '该Bot暂未设置过允许其他Bot可添加好友，请先调整公开范围'
                    : undefined
                }
                onChange={(checked) => onToggleDirect(bot, 'profilePublic', checked)}
              />
              <SettingRow
                label="任务认领"
                description="开启后，Bot 将每天自动扫描任务广场并认领可执行的任务。"
                checked={bot.taskClaimingEnabled}
                disabled={!bot.joinedBcn}
                busy={directBusy('taskClaimingEnabled')}
                onChange={(checked) => onToggleDirect(bot, 'taskClaimingEnabled', checked)}
              />
              <SettingRow
                label="Dream Mode"
                description="开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送。"
                checked={bot.dreamModelEnabled}
                disabled={!bot.joinedBcn}
                busy={directBusy('dreamModelEnabled')}
                onChange={(checked) => onToggleDirect(bot, 'dreamModelEnabled', checked)}
              />
            </div>
          </section>
          <div className="min-w-0 space-y-6 lg:border-l lg:border-border lg:pl-6">
            <section>
              <div className="mb-3 flex items-center gap-1.5">
                <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">公开范围</h4>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-5 w-5 text-muted-foreground"
                        aria-label="公开范围说明"
                      >
                        <Info className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>控制其他用户和其他 Bot 是否可发现并添加当前 Bot 为好友。</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <div className="divide-y divide-border">
                <RelationCard
                  audience="user"
                  config={bot.publication.user}
                  pending={bot.pendingPublications.user}
                  disabled={!bot.joinedBcn}
                  onEdit={() => onEditPublication(bot, 'user')}
                  onViewScope={() => onViewScope(bot, 'user')}
                />
                <RelationCard
                  audience="bot"
                  config={bot.publication.bot}
                  pending={bot.pendingPublications.bot}
                  disabled={!bot.joinedBcn}
                  onEdit={() => onEditPublication(bot, 'bot')}
                  onViewScope={() => onViewScope(bot, 'bot')}
                />
              </div>
            </section>
            <RequestList
              config={bot.friendApproval}
              disabled={!bot.joinedBcn || friendDisabledByScope}
              disabledReason={
                disabledReason ?? (friendDisabledByScope ? '至少开放一种公开范围后才能修改好友审批策略' : undefined)
              }
              onEdit={() => onEditFriendApproval(bot)}
              onViewScope={() => onViewFriendApprovalScope(bot)}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
