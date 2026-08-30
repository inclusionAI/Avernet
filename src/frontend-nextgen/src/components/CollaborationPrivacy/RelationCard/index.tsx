import type { PendingPublication, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';

const scopeLabels = { none: '不公开', all: '全部公开', restricted: '限制公开范围' } as const;
const audienceLabels: Record<PublicAudience, string> = { user: '其他用户', bot: '其他 Bot' };

interface RelationCardProps {
  audience: PublicAudience;
  config: PublicConfig;
  pending?: PendingPublication;
  disabled?: boolean;
  onEdit: () => void;
  onViewScope: () => void;
}

export function RelationCard({ audience, config, pending, disabled, onEdit, onViewScope }: RelationCardProps) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-muted)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="m-0 text-sm font-medium text-[var(--color-fg)]">对{audienceLabels[audience]}公开</p>
            {pending && <Badge tone="warning">待审批</Badge>}
            {pending && (
              <Button asChild variant="ghost" size="sm" className="h-auto p-0 text-[var(--color-primary)]">
                <a href="/admin/work-orders" target="_blank" rel="noopener noreferrer">查看审批进度</a>
              </Button>
            )}
          </div>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            当前生效：{scopeLabels[config.scope]}
            {config.scope === 'restricted' ? ` · ${config.organizationPaths.length} 个组织范围` : ''}
            {config.scope === 'restricted' && <Button variant="ghost" size="sm" className="ml-1 h-auto p-0 align-baseline text-[var(--color-primary)]" onClick={onViewScope}>查看</Button>}
          </p>
        </div>
        <Button variant="secondary" size="sm" disabled={disabled || Boolean(pending)} onClick={onEdit}>
          {pending ? '审批中' : '编辑范围'}
        </Button>
      </div>
    </div>
  );
}
