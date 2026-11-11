/**
 * 协作权限通用配置分区组件（collab-permission-entry-migration）：
 * 从 PermissionCard 提取，供协作权限页面卡片与 BotWorkshop「通用配置」弹窗共用。
 * 提取为 DOM 等价重构——页面侧渲染结果与提取前一致，既有测试断言不受影响。
 */
import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import { botFriendApprovalSection, botVisibilitySection, visibilityAudience } from './botVisibilityCopy';
import { LabelHelpTooltip } from './HelpTooltip';
import { RelationCard, type RelationCardProps } from './RelationCard';
import { RequestList } from './RequestList';
import { SettingRow } from './SettingRow';

export { BotAbilitySettings } from './BotAbilitySettings';

export interface VisibilitySectionCallbacks {
  onEditPublication: (bot: CollaborationBot, audience: PublicAudience) => void;
  onViewScope: (bot: CollaborationBot, audience: PublicAudience) => void;
}

export interface FriendApprovalSectionCallbacks {
  onEditFriendApproval: (bot: CollaborationBot) => void;
  onViewFriendApprovalScope: (bot: CollaborationBot) => void;
}

interface BotVisibilitySettingsProps extends VisibilitySectionCallbacks {
  bot: CollaborationBot;
  showUserVisibility?: boolean;
  /** 外部 Bot 无审批链路：不渲染待审批/审批链接，也不因审批态禁用编辑（数据面已由外部合同归一）。 */
  isExternalBot?: boolean;
  /** 公开风险前置检测在途态（仅影响「对用户」行，bot-public-risk-check）。 */
  userRiskStatus?: RelationCardProps['riskStatus'];
}

/** Bot 可见性分区：标题 + 双 audience 行（对用户 / 对 Bot 独立配置）。 */
export function BotVisibilitySettings({
  bot,
  showUserVisibility = true,
  isExternalBot = false,
  userRiskStatus,
  onEditPublication,
  onViewScope,
}: BotVisibilitySettingsProps) {
  return (
    <>
      <div className="mb-3 flex items-center gap-1.5">
        <h4 className="m-0 text-xs font-semibold tracking-wide text-muted-foreground">{botVisibilitySection.title}</h4>
        <LabelHelpTooltip
          label={botVisibilitySection.title}
          content={showUserVisibility ? botVisibilitySection.description : visibilityAudience.bot.description}
        />
      </div>
      <div className="divide-y divide-divider">
        {showUserVisibility && (
          <RelationCard
            audience="user"
            config={bot.publication.user}
            pending={bot.pendingPublications.user}
            disabled={!bot.joinedBcn}
            riskStatus={userRiskStatus}
            onEdit={() => onEditPublication(bot, 'user')}
            onViewScope={() => onViewScope(bot, 'user')}
          />
        )}
        <RelationCard
          audience="bot"
          config={bot.publication.bot}
          pending={isExternalBot ? undefined : bot.pendingPublications.bot}
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
      <div className="divide-y divide-divider">
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

/** Bot 好友审批分区：标题（含审批处理入口链接说明——全栈为通知中心，BCS-only 为发现页入口）+ 策略行。 */
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
