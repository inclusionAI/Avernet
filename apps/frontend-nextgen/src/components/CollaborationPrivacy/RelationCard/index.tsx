import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { PendingPublication, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { RefreshCw } from 'lucide-react';
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
  /** 风险记录主导对用户公开的进度，审批投影提供同一笔申请的外链。 */
  riskStatus?:
    | {
        kind: 'checking' | 'advancing' | 'approving';
        approvalPuid?: string | null;
        detailUrl?: string | null;
        onViewApprovalProgress: () => void;
        onRefreshStatus?: () => void;
        refreshingStatus?: boolean;
      }
    | { kind: 'failed' | 'attention'; label: string }
    | null;
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
  const failure = riskStatus && 'label' in riskStatus ? riskStatus : null;
  const inFlight = riskStatus && 'onViewApprovalProgress' in riskStatus ? riskStatus : null;
  const approving = inFlight?.kind === 'approving';
  const pendingApproval =
    !riskStatus || (approving && (!inFlight.approvalPuid || pending?.id === inFlight.approvalPuid))
      ? pending
      : undefined;
  const riskBadge = failure ? (
    <Badge tone="error">{failure.label}</Badge>
  ) : !pendingApproval && inFlight ? (
    <Badge tone={approving ? 'warning' : 'primary'}>{approving ? '审批中' : '检测中'}</Badge>
  ) : null;
  // 链接与按钮共用右侧动作区，审批投影仅在对应当前审批时提供外链。
  const stateLink = pendingApproval?.approvalUrl ? (
    <Button asChild variant="ghost" size="sm" className="px-2 text-primary">
      <a href={pendingApproval.approvalUrl} target="_blank" rel="noopener noreferrer">
        查看审批进度
      </a>
    </Button>
  ) : !pendingApproval && approving ? (
    <Button variant="ghost" size="sm" className="px-2 text-primary" onClick={inFlight.onViewApprovalProgress}>
      查看审批进度
    </Button>
  ) : inFlight?.detailUrl ? (
    <Button asChild variant="ghost" size="sm" className="px-2 text-primary">
      <a href={inFlight.detailUrl} target="_blank" rel="noopener noreferrer">
        查看检测工单
      </a>
    </Button>
  ) : null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="m-0 text-sm font-medium text-foreground">{audienceLabels[audience]}</p>
          {pendingApproval && <Badge tone="warning">待审批</Badge>}
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
        {inFlight?.onRefreshStatus ? (
          <Button
            variant="ghost"
            size="sm"
            className="px-2 text-primary"
            loading={inFlight.refreshingStatus ?? false}
            leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
            onClick={inFlight.onRefreshStatus}
          >
            刷新状态
          </Button>
        ) : null}
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled || Boolean(pendingApproval || inFlight)}
          onClick={onEdit}
        >
          {pendingApproval || approving ? '审批中' : inFlight ? '检测中' : failure ? '去处理' : '修改可见性'}
        </Button>
      </div>
    </div>
  );
}
