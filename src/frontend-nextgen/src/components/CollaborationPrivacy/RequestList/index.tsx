import type { FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import { Button } from '@/components/ui/Button';

const modeLabels = { none: '全部申请无需审批', all: '全部申请需审批', partial_exempt: '部分组织免审批' } as const;

interface RequestListProps {
  config: FriendApprovalConfig;
  disabled: boolean;
  disabledReason?: string;
  onEdit: () => void;
}

export function RequestList({ config, disabled, disabledReason, onEdit }: RequestListProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] p-4">
      <div>
        <p className="m-0 text-sm font-medium text-[var(--color-fg)]">好友申请审批</p>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          当前策略：{modeLabels[config.mode]}
          {config.mode === 'partial_exempt' ? ` · ${config.exemptOrganizationPaths.length} 个免审批范围` : ''}
        </p>
        {disabledReason && <p className="mt-1 text-xs text-[var(--color-warning)]">{disabledReason}</p>}
      </div>
      <Button variant="secondary" size="sm" disabled={disabled} onClick={onEdit}>编辑策略</Button>
    </div>
  );
}
