import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { useTaskEscort } from '@/hooks/useTaskEscort';
import type { BotDomain } from '@/services/botWorkshop';
import { Activity, BookOpen, Settings2, XCircle } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import TaskEscortDashboardView from '../TaskEscortDashboardView';
import TaskEscortDetailView from '../TaskEscortDetailView';
import TaskEscortFlowConfig from '../TaskEscortFlowConfig';
import TaskEscortGuidePanel from '../TaskEscortGuidePanel';

export interface TaskEscortProps {
  bot?: BotDomain;
}

type EscortTab = 'config' | 'logs' | 'guide';

const TAB_OPTIONS: Array<{ value: EscortTab; label: string; icon: React.ReactNode }> = [
  { value: 'config', label: '流程配置', icon: <Settings2 className="h-3.5 w-3.5" /> },
  { value: 'logs', label: '日志分析', icon: <Activity className="h-3.5 w-3.5" /> },
  { value: 'guide', label: '操作指南', icon: <BookOpen className="h-3.5 w-3.5" /> },
];

const TaskEscort: React.FC<TaskEscortProps> = ({ bot }) => {
  const [tab, setTab] = useState<EscortTab>('config');

  const botOwnerId = bot?.spaceId;
  const botId = bot?.id;

  const {
    workflowTypes,
    flowRuns,
    isLoadingTypes,
    isLoadingRuns,
    isRefreshingDashboard,
    isRefreshingDetail,
    error,
    selectedWorkflowId,
    refreshDashboard,
    refreshDetail,
    navigateToDetail,
    backToDashboard,
    loadTypes,
  } = useTaskEscort({
    botOwnerId,
    botId,
    enabled: tab === 'logs' && !!bot,
  });

  const flowConfigEnabled = tab === 'config' && !!bot;

  const selectedWorkflow = useMemo(
    () => workflowTypes.find((w) => w.workflow_id === selectedWorkflowId),
    [workflowTypes, selectedWorkflowId],
  );

  const workflowTitle = selectedWorkflow?.workflow_title || selectedWorkflowId || '';

  if (!bot) {
    return (
      <div className="flex min-h-full flex-col bg-card">
        <div className="border-b border-border px-5 py-4">
          <h2 className="m-0 text-sm font-semibold">任务护航</h2>
        </div>
        <div className="px-5 py-4">
          <Empty compact title="请选择一个 Bot" description="任务护航配置需要绑定具体 Bot。" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col bg-card">
      <div className="border-b border-border px-5 py-4">
        <h2 className="m-0 text-sm font-semibold">任务护航</h2>
      </div>
      <div className="space-y-4 px-5 py-4">
        <div className="flex flex-wrap items-center gap-1 rounded-lg bg-[var(--color-panel-muted)] p-1">
          {TAB_OPTIONS.map((option) => (
            <Button
              key={option.value}
              variant={tab === option.value ? 'secondary' : 'ghost'}
              size="sm"
              leftIcon={option.icon}
              onClick={() => setTab(option.value)}
            >
              {option.label}
            </Button>
          ))}
        </div>

        {tab === 'config' && <TaskEscortFlowConfig botOwnerId={botOwnerId} botId={botId} enabled={flowConfigEnabled} />}

        {tab === 'logs' && (
          <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
            {error ? (
              <div className="flex flex-col items-center gap-3 py-12">
                <XCircle className="h-8 w-8 text-[var(--color-error)]" />
                <p className="text-sm text-[var(--color-error)]">{error}</p>
                <Button variant="secondary" size="sm" onClick={loadTypes}>
                  重试
                </Button>
              </div>
            ) : selectedWorkflowId ? (
              <TaskEscortDetailView
                workflowId={selectedWorkflowId}
                workflowTitle={workflowTitle}
                flowRuns={flowRuns}
                isLoading={isLoadingRuns}
                isRefreshing={isRefreshingDetail}
                onRefresh={refreshDetail}
                onBack={backToDashboard}
              />
            ) : (
              <TaskEscortDashboardView
                workflowTypes={workflowTypes}
                isLoading={isLoadingTypes}
                isRefreshing={isRefreshingDashboard}
                onRefresh={refreshDashboard}
                onNavigate={navigateToDetail}
              />
            )}
          </div>
        )}

        {tab === 'guide' && <TaskEscortGuidePanel />}
      </div>
    </div>
  );
};

export default TaskEscort;
