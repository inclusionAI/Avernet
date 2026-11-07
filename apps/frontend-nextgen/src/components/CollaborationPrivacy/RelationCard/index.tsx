import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { PendingPublication, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { visibilityAudience, visibilityScopeLabels } from '../botVisibilityCopy';

const audienceLabels: Record<PublicAudience, string> = {
  user: visibilityAudience.user.label,
  bot: visibilityAudience.bot.label,
};
export interface RelationCardProps {
  audience: PublicAudience;
  config: PublicConfig;
  pending?: PendingPublication;
  disabled?: boolean;
  onEdit: () => void;
  onViewScope: () => void;
  /**
   * 公开风险前置检测在途态（bot-public-risk-check，仅「对用户」行使用）：
   * checking/advancing/approving 锁定编辑并展示徽标；approving 附「查看审批进度」，
   * checking/advancing 附「查看检测工单」（detail_url 缺失时不渲染链接）。
   * 既有 pending（审批中）优先渲染，避免双徽标。
   */
  riskStatus?: {
    kind: 'checking' | 'advancing' | 'approving';
    detailUrl?: string | null;
    onViewApprovalProgress: () => void;
  } | null;
}

export function RelationCard({
  audience,
  config,
  pending,
  disabled,
  onEdit,
  onViewScope,
  riskStatus,
}: RelationCardProps) {
  const riskBadge =
    !pending && riskStatus ? (
      <Badge tone={riskStatus.kind === 'approving' ? 'warning' : 'primary'}>
        {riskStatus.kind === 'approving' ? '审批中' : '检测中'}
      </Badge>
    ) : null;
  // 状态外链与操作按钮同入右侧动作区：链接用原生 sm 高度（h-8）保证与按钮同一文本盒水平对齐。
  // 审批态看审批进度（对 Bot 走既有 approvalUrl，对用户风险动线走站内跳转），检测态看检测工单。
  const stateLink = pending?.approvalUrl ? (
    <Button asChild variant="ghost" size="sm" className="px-2 text-primary">
      <a href={pending.approvalUrl} target="_blank" rel="noopener noreferrer">
        查看审批进度
      </a>
    </Button>
  ) : riskStatus && !pending ? (
    riskStatus.kind === 'approving' ? (
      <Button variant="ghost" size="sm" className="px-2 text-primary" onClick={riskStatus.onViewApprovalProgress}>
        查看审批进度
      </Button>
    ) : riskStatus.detailUrl ? (
      <Button asChild variant="ghost" size="sm" className="px-2 text-primary">
        <a href={riskStatus.detailUrl} target="_blank" rel="noopener noreferrer">
          查看检测工单
        </a>
      </Button>
    ) : null
  ) : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="m-0 text-sm font-medium text-foreground">{audienceLabels[audience]}</p>
          {pending && <Badge tone="warning">待审批</Badge>}
          {riskBadge}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          当前可见性：{visibilityScopeLabels[config.scope]}
          {config.scope === 'restricted' ? ` · ${config.organizationPaths.length} 个组织范围` : ''}
          {config.scope === 'restricted' && (
            <Button
              variant="ghost"
              size="sm"
              className="ml-1 h-auto p-0 align-baseline text-primary"
              onClick={onViewScope}
            >
              查看
            </Button>
          )}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {stateLink}
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled || Boolean(pending) || Boolean(riskStatus)}
          onClick={onEdit}
        >
          {pending || riskStatus?.kind === 'approving' ? '审批中' : riskStatus ? '检测中' : '修改可见性'}
        </Button>
      </div>
    </div>
  );
}
