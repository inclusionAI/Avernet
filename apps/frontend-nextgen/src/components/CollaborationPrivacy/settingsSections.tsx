/**
 * 协作权限通用配置分区组件（collab-permission-entry-migration）：
 * 从 PermissionCard 提取，供协作权限页面卡片与 BotWorkshop「通用配置」弹窗共用。
 * 提取为 DOM 等价重构——页面侧渲染结果与提取前一致，既有测试断言不受影响。
 */
import { Badge } from '@/components/ui/Badge';
import { Switch } from '@/components/ui/Switch';
import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';
import { cn } from '@/utils/cn';
import { botFriendApprovalSection, botVisibilitySection } from './botVisibilityCopy';
import { ControlStateTooltip, LabelHelpTooltip } from './HelpTooltip';
import { RelationCard } from './RelationCard';
import { RequestList } from './RequestList';

export interface VisibilitySectionCallbacks {
  onEditPublication: (bot: CollaborationBot, audience: PublicAudience) => void;
  onViewScope: (bot: CollaborationBot, audience: PublicAudience) => void;
}

export interface FriendApprovalSectionCallbacks {
  onEditFriendApproval: (bot: CollaborationBot) => void;
  onViewFriendApprovalScope: (bot: CollaborationBot) => void;
}

interface SettingRowProps {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  busy?: boolean;
  status?: string;
  statusReason?: string;
  onChange: (checked: boolean) => void;
  /** 容器布局不同时的行内边距覆盖；缺省为 divide-y 列表语义（py-3 first:pt-0 last:pb-0）。 */
  paddingClassName?: string;
}

function SettingRow({
  label,
  description,
  checked,
  disabled,
  busy,
  status,
  statusReason,
  onChange,
  paddingClassName,
}: SettingRowProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4', paddingClassName ?? 'py-3 first:pt-0 last:pb-0')}>
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm font-medium text-foreground">{label}</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {status && <Badge tone="neutral">{status}</Badge>}
        {status && statusReason && <ControlStateTooltip label={label} status={status} content={statusReason} />}
        <Switch
          checked={checked}
          disabled={disabled || busy}
          aria-label={`${checked ? '关闭' : '开启'}${label}`}
          onCheckedChange={onChange}
        />
      </div>
    </div>
  );
}

interface BotAbilitySettingsProps {
  bot: CollaborationBot;
  busyAction: string | null | undefined;
  onToggleDirect: (bot: CollaborationBot, setting: DirectSetting, value: boolean | 'online' | 'hidden') => void;
  /** 通用配置弹窗 2×2 网格布局；缺省为页面列表布局（divide-y）。 */
  grid?: boolean;
}

/** 协作能力分区：四个开关行。 */
export function BotAbilitySettings({ bot, busyAction, onToggleDirect, grid = false }: BotAbilitySettingsProps) {
  const directBusy = (setting: DirectSetting) => busyAction === `${bot.id}:${setting}`;
  const rows = (
    <>
      <SettingRow
        label="参与协作群聊"
        description="控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。"
        checked={bot.collaborationStatus === 'online'}
        disabled={!bot.joinedBcn || bot.collaborationStatus === 'offline'}
        busy={directBusy('collaborationStatus')}
        onChange={(checked) => onToggleDirect(bot, 'collaborationStatus', checked ? 'online' : 'hidden')}
        paddingClassName={grid ? 'py-3' : undefined}
      />
      <SettingRow
        label="公开 Bot 画像"
        description="允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察。"
        checked={bot.profilePublic}
        disabled={!bot.joinedBcn || bot.profilePublicStatus === 'unavailable'}
        busy={directBusy('profilePublic')}
        status={bot.profilePublicStatus === 'unavailable' ? '暂不可用' : undefined}
        statusReason={
          bot.profilePublicStatus === 'unavailable' ? '该 Bot 尚未对其他 Bot 开放可见，请先调整 Bot 可见性' : undefined
        }
        onChange={(checked) => onToggleDirect(bot, 'profilePublic', checked)}
        paddingClassName={grid ? 'py-3' : undefined}
      />
      <SettingRow
        label="任务认领"
        description="开启后，Bot 将每天自动扫描任务广场并认领可执行的任务。"
        checked={bot.taskClaimingEnabled}
        disabled={!bot.joinedBcn}
        busy={directBusy('taskClaimingEnabled')}
        onChange={(checked) => onToggleDirect(bot, 'taskClaimingEnabled', checked)}
        paddingClassName={grid ? 'py-3' : undefined}
      />
      <SettingRow
        label="Dream Mode"
        description="开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送。"
        checked={bot.dreamModelEnabled}
        disabled={!bot.joinedBcn}
        busy={directBusy('dreamModelEnabled')}
        onChange={(checked) => onToggleDirect(bot, 'dreamModelEnabled', checked)}
        paddingClassName={grid ? 'py-3' : undefined}
      />
    </>
  );
  return (
    <>
      <div className="mb-3 flex items-center">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">协作能力</h4>
      </div>
      {grid ? (
        <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">{rows}</div>
      ) : (
        <div className="divide-y divide-border">{rows}</div>
      )}
    </>
  );
}

interface BotVisibilitySettingsProps extends VisibilitySectionCallbacks {
  bot: CollaborationBot;
}

/** Bot 可见性分区：标题 + 双 audience 行（对用户 / 对 Bot 独立配置）。 */
export function BotVisibilitySettings({ bot, onEditPublication, onViewScope }: BotVisibilitySettingsProps) {
  return (
    <>
      <div className="mb-3 flex items-center gap-1.5">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">{botVisibilitySection.title}</h4>
        <LabelHelpTooltip label={botVisibilitySection.title} content={botVisibilitySection.description} />
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
    </>
  );
}

interface FriendApprovalSettingsProps extends FriendApprovalSectionCallbacks {
  bot: CollaborationBot;
}

/**
 * 发布审批分区（collab-permission-entry-migration AC-06~08）：
 * 仅 internal × 团队空间 Bot 渲染（形态经 capability getGeneralConfigPublishApprovalEnabled 承载，
 * 空间经 spaceKind==='team' 判定，均由弹窗容器组合后决定是否渲染本组件）。
 * 开关为受控本地态（配置展示级：不持久化、不走工单，弹窗关闭即恢复默认关）；
 * 不跟随 joinedBcn 只读（平台治理配置，不依赖 BCN 网络）。
 */
export function PublishApprovalSettings({
  checked,
  loading,
  error,
  onChange,
}: {
  checked: boolean;
  loading?: boolean;
  error?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <>
      <div className="mb-3 flex items-center gap-1.5">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">发布审批</h4>
        <LabelHelpTooltip
          label="发布审批"
          content="控制共同编辑者发布该 Bot 前是否需要 Owner 审批；仅团队空间 Bot 展示本组。"
        />
      </div>
      <div className="divide-y divide-border">
        <SettingRow
          label="共同编辑者发布需 Owner 审批"
          description="开启后，共同编辑者发布该 Bot 前需经 Owner 审批；关闭后可直接发布。"
          checked={checked}
          disabled={Boolean(error)}
          busy={loading}
          onChange={onChange}
        />
        {error ? <p className="m-0 py-2 text-xs text-destructive">{error}</p> : null}
      </div>
    </>
  );
}

/** Bot 好友审批分区：标题（含通知中心入口链接说明）+ 策略行。 */
export function FriendApprovalSettings({
  bot,
  onEditFriendApproval,
  onViewFriendApprovalScope,
}: FriendApprovalSettingsProps) {
  const joinedBcnDisabledReason = bot.joinedBcn ? undefined : '加入 BCN 后才能修改协作权限';
  const friendDisabledByScope = bot.publication.user.scope === 'none' && bot.publication.bot.scope === 'none';
  return (
    <>
      <div className="mb-3 flex items-center gap-1.5">
        <h4 className="text-xs font-semibold tracking-wide text-muted-foreground">{botFriendApprovalSection.title}</h4>
        <LabelHelpTooltip
          label={botFriendApprovalSection.title}
          content={
            <>
              {botFriendApprovalSection.descriptionLeading}
              <a
                href={botFriendApprovalSection.approvalEntryPath}
                className="font-medium text-primary hover:opacity-80"
              >
                {botFriendApprovalSection.approvalEntryLabel}
              </a>
              {botFriendApprovalSection.descriptionTrailing}
            </>
          }
        />
      </div>
      <RequestList
        config={bot.friendApproval}
        disabled={!bot.joinedBcn || friendDisabledByScope}
        disabledReason={
          joinedBcnDisabledReason ??
          (friendDisabledByScope ? botVisibilitySection.disabledFriendApprovalReason : undefined)
        }
        onEdit={() => onEditFriendApproval(bot)}
        onViewScope={() => onViewFriendApprovalScope(bot)}
      />
    </>
  );
}
