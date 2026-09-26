import type { TaskEscortWorkflowType } from '@/components/BotWorkshop/TaskEscort/types';
import { STATUS_LABEL, STATUS_TONE, formatTimeShort } from '@/components/BotWorkshop/TaskEscort/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { isLiveRunStatus } from '@/hooks/useTaskEscort';
import React, { useMemo } from 'react';

interface DashboardViewProps {
  workflowTypes: TaskEscortWorkflowType[];
  isLoading: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
  onNavigate: (workflowId: string) => void;
}

const TaskEscortDashboardView: React.FC<DashboardViewProps> = ({
  workflowTypes,
  isLoading,
  isRefreshing,
  onRefresh,
  onNavigate,
}) => {
  const hasRunning = useMemo(() => workflowTypes.some((w) => isLiveRunStatus(w.last_status)), [workflowTypes]);

  return (
    <div className="flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
        <div>
          <div className="text-sm font-semibold">工作流</div>
          <div className="mt-0.5 text-xs text-[var(--color-muted)]">
            {workflowTypes.length} workflows
            {hasRunning && (
              <span className="ml-1.5 inline-flex items-center text-[var(--color-primary)]">
                <span className="mr-1 h-1.5 w-1.5 rounded-full bg-[var(--color-primary)]" />
                Live
              </span>
            )}
          </div>
        </div>
        <Button variant="secondary" size="sm" loading={isRefreshing} onClick={onRefresh}>
          {isRefreshing ? '刷新中…' : '刷新'}
        </Button>
      </div>

      {/* Content */}
      <div className="max-h-[400px] overflow-y-auto">
        {isLoading && workflowTypes.length === 0 && !isRefreshing ? (
          <Spin tip="加载中…" />
        ) : workflowTypes.length === 0 ? (
          <div className="py-12">
            <Empty compact title="暂无工作流" description="运行工作流后将在此显示。" />
          </div>
        ) : (
          <div className="divide-y divide-[var(--color-border)]">
            {workflowTypes.map((wf) => (
              <Button
                key={wf.workflow_id}
                variant="ghost"
                className="h-auto w-full justify-start rounded-none px-3 py-2.5 text-left hover:bg-[var(--color-panel-muted)]"
                onClick={() => onNavigate(wf.workflow_id)}
              >
                <div className="flex w-full items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">{wf.workflow_title || wf.workflow_id}</div>
                    <div className="truncate text-[10px] text-[var(--color-muted)]">{wf.workflow_id}</div>
                  </div>
                  {wf.last_status && (
                    <Badge tone={STATUS_TONE[wf.last_status] || 'neutral'}>
                      {STATUS_LABEL[wf.last_status] || wf.last_status}
                    </Badge>
                  )}
                </div>
                <div className="ml-0 mt-1 flex w-full items-center gap-2 text-[10px]">
                  <span className="text-[var(--color-muted)]">{wf.run_count} 总计</span>
                  {isLiveRunStatus(wf.last_status) && (
                    <span className="inline-flex items-center text-[var(--color-primary)]">
                      <span className="mr-0.5 h-1 w-1 rounded-full bg-[var(--color-primary)]" />
                      进行中
                    </span>
                  )}
                  <span className="ml-auto text-[var(--color-muted)]">
                    {formatTimeShort(wf.last_run_at ?? wf.updated_at)}
                  </span>
                </div>
              </Button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default TaskEscortDashboardView;
