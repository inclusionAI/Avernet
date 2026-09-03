import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import { Info } from 'lucide-react';

const modeLabels = { none: '全部申请无需审批', all: '全部申请需审批', partial_exempt: '部分组织免审批' } as const;

interface RequestListProps {
  config: FriendApprovalConfig;
  disabled: boolean;
  disabledReason?: string;
  onEdit: () => void;
  onViewScope: () => void;
}

export function RequestList({ config, disabled, disabledReason, onEdit, onViewScope }: RequestListProps) {
  const exemptScopeCount = config.exemptOrganizationPaths.length || config.exemptDepartmentNos?.length || 0;
  return (
    <section className="flex flex-wrap items-start justify-between gap-3 border-t border-border pt-5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <div className="flex items-center gap-1.5">
            <p className="m-0 text-sm font-medium text-foreground">好友审批策略</p>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-5 w-5 text-muted-foreground"
                    aria-label="好友审批策略说明"
                  >
                    <Info className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>控制好友申请是否进入管理后台工单中心，并由用户确认后通过。</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          {disabledReason && <span className="text-xs leading-5 text-warning">{disabledReason}</span>}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          当前策略：{modeLabels[config.mode]}
          {config.mode === 'partial_exempt' ? ` · ${exemptScopeCount} 个免审批范围` : ''}
          {config.mode === 'partial_exempt' && exemptScopeCount > 0 && (
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
      <Button variant="secondary" size="sm" disabled={disabled} onClick={onEdit}>
        编辑策略
      </Button>
    </section>
  );
}
