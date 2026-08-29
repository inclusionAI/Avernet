import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import { Switch } from '@/components/ui/Switch';
import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';
import { Copy } from 'lucide-react';
import { RelationCard } from '../RelationCard';
import { RequestList } from '../RequestList';

interface PermissionCardProps {
  bot: CollaborationBot;
  busyAction: string | null;
  onCopyId: (botId: string) => void;
  onToggleDirect: (bot: CollaborationBot, setting: DirectSetting, value: boolean | 'online' | 'hidden') => void;
  onEditPublication: (bot: CollaborationBot, audience: PublicAudience) => void;
  onEditFriendApproval: (bot: CollaborationBot) => void;
  onViewScope: (bot: CollaborationBot, audience: PublicAudience) => void;
}

interface SettingRowProps {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  busy: boolean;
  onChange: (checked: boolean) => void;
}

function SettingRow({ label, description, checked, disabled, busy, onChange }: SettingRowProps) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div>
        <p className="m-0 text-sm font-medium text-[var(--color-fg)]">{label}</p>
        <p className="mt-1 text-xs leading-5 text-[var(--color-muted)]">{description}</p>
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
  onToggleDirect,
  onEditPublication,
  onEditFriendApproval,
  onViewScope,
}: PermissionCardProps) {
  const disabledReason = bot.joinedBcn ? undefined : '加入 BCN 后才能修改协作权限';
  const directBusy = (setting: DirectSetting) => busyAction === `${bot.id}:${setting}`;
  const friendDisabledByScope = bot.publication.user.scope === 'none' && bot.publication.bot.scope === 'none';
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-[var(--color-border)] pb-5">
        <div>
          <CardTitle>{bot.name}</CardTitle>
          <p className="mt-2 text-xs text-[var(--color-muted)]">{bot.engine}</p>
          <div className="mt-2 flex items-center gap-1 text-xs text-[var(--color-muted)]">
            <span>Bot ID：{bot.id}</span>
            <IconButton
              label={`复制 ${bot.name} 的 Bot ID`}
              icon={<Copy className="h-3.5 w-3.5" aria-hidden />}
              size="sm"
              onClick={() => onCopyId(bot.id)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {disabledReason && (
          <div className="rounded-lg bg-[var(--color-warning-soft)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {disabledReason}
          </div>
        )}
        <section>
          <h4 className="m-0 text-sm font-semibold text-[var(--color-fg)]">协作能力</h4>
          <div className="mt-2 divide-y divide-[var(--color-border)]">
            <SettingRow
              label="参与协作群聊"
              description="控制 Bot 是否可参与群聊会话。关闭后无法加入新协作群，并停止在已加入的协作群会话中回复消息"
              checked={bot.collaborationStatus === 'online'}
              disabled={!bot.joinedBcn || bot.collaborationStatus === 'offline'}
              busy={directBusy('collaborationStatus')}
              onChange={(checked) => onToggleDirect(bot, 'collaborationStatus', checked ? 'online' : 'hidden')}
            />
            <SettingRow
              label="Bot 画像公开"
              description="允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察"
              checked={bot.profilePublic}
              disabled={!bot.joinedBcn}
              busy={directBusy('profilePublic')}
              onChange={(checked) => onToggleDirect(bot, 'profilePublic', checked)}
            />
            <SettingRow
              label="任务认领"
              description="开启后，Bot 将每天自动扫描任务广场并认领可执行的任务"
              checked={bot.taskClaimingEnabled}
              disabled={!bot.joinedBcn}
              busy={directBusy('taskClaimingEnabled')}
              onChange={(checked) => onToggleDirect(bot, 'taskClaimingEnabled', checked)}
            />
            <SettingRow
              label="Dream Model"
              description="开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送"
              checked={bot.dreamModelEnabled}
              disabled={!bot.joinedBcn}
              busy={directBusy('dreamModelEnabled')}
              onChange={(checked) => onToggleDirect(bot, 'dreamModelEnabled', checked)}
            />
          </div>
        </section>
        <section>
          <h4 className="m-0 text-sm font-semibold text-[var(--color-fg)]">公开范围</h4>
          <div className="mt-4 space-y-3">
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
        />
      </CardContent>
    </Card>
  );
}
