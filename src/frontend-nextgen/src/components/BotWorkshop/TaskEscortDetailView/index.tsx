import type { TaskEscortFlowRun, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { STATUS_LABEL, STATUS_TONE, formatDuration, formatTime } from '@/components/BotWorkshop/TaskEscort/utils';
import WorkflowDagView from '@/components/BotWorkshop/TaskEscortFlowConfig/WorkflowDagView';
import { TaskEscortStatCard } from '@/components/BotWorkshop/TaskEscortStatCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { isLiveRunStatus } from '@/hooks/useTaskEscort';
import type { NodeExecution } from '@/services/taskEscort';
import { taskEscortService } from '@/services/taskEscort';
import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react';
import React, { useCallback, useMemo, useState } from 'react';

const TRIGGER_LABEL: Record<string, string> = {
  manual: '手动触发',
  schedule: '定时触发',
  event: '事件触发',
  api: 'API 触发',
};

type RunTab = 'nodes' | 'dag';

interface DetailViewProps {
  workflowId: string;
  workflowTitle: string;
  flowRuns: TaskEscortFlowRun[];
  isLoading: boolean;
  isRefreshing: boolean;
  onRefresh: () => void;
  onBack: () => void;
}

const TaskEscortDetailView: React.FC<DetailViewProps> = ({
  workflowId,
  workflowTitle,
  flowRuns,
  isLoading,
  isRefreshing,
  onRefresh,
  onBack,
}) => {
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [runNodes, setRunNodes] = useState<NodeExecution[]>([]);
  const [workflowSpec, setWorkflowSpec] = useState<TaskEscortWorkflowSpec | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<RunTab>('nodes');

  const stats = useMemo(() => {
    const total = flowRuns.length;
    const succeeded = flowRuns.filter((r) => r.status === 'succeeded').length;
    const failed = flowRuns.filter((r) => r.status === 'failed').length;
    const running = flowRuns.filter((r) => isLiveRunStatus(r.status)).length;
    const failedNodes = flowRuns.reduce((sum, r) => sum + (r.failed_nodes ?? 0), 0);
    const durations = flowRuns.map((r) => r.duration_ms).filter((d): d is number => d !== null && d !== undefined);
    const avgDuration = durations.length > 0 ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
    const totalTokens = flowRuns.reduce((sum, r) => sum + (r.total_tokens ?? 0), 0);
    return { total, succeeded, failed, running, failedNodes, avgDuration, totalTokens };
  }, [flowRuns]);

  const hasLive = useMemo(() => flowRuns.some((r) => isLiveRunStatus(r.status)), [flowRuns]);

  const handleRunClick = useCallback(
    async (flowId: string) => {
      if (expandedRunId === flowId) {
        // Collapse
        setExpandedRunId(null);
        setRunNodes([]);
        setWorkflowSpec(null);
        return;
      }
      setExpandedRunId(flowId);
      setRunNodes([]);
      setWorkflowSpec(null);
      setDetailError(null);
      setActiveTab('nodes');
      setIsLoadingDetail(true);
      try {
        const [detail, spec] = await Promise.all([
          taskEscortService.getFlowRun(flowId),
          taskEscortService.getWorkflow(workflowId).catch(() => null),
        ]);
        setRunNodes(detail.nodes);
        setWorkflowSpec(spec as TaskEscortWorkflowSpec | null);
      } catch (e: unknown) {
        const message = e instanceof Error ? e.message : '加载运行详情失败';
        setDetailError(message);
      } finally {
        setIsLoadingDetail(false);
      }
    },
    [expandedRunId, workflowId],
  );

  return (
    <div className="flex flex-col">
      {/* Back */}
      <div className="border-b border-[var(--color-border)] px-3 py-2">
        <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="h-3 w-3" />} onClick={onBack}>
          返回工作流列表
        </Button>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
        <div>
          <div className="text-sm font-semibold">{workflowTitle}</div>
          <div className="mt-0.5 font-mono text-[10px] text-[var(--color-muted)]">{workflowId}</div>
          <div className="mt-0.5 text-xs text-[var(--color-muted)]">
            {flowRuns.length} 次运行
            {hasLive && (
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

      {/* Stats */}
      {flowRuns.length > 0 && (
        <div className="grid grid-cols-3 gap-2 border-b border-[var(--color-border)] px-3 py-2">
          <TaskEscortStatCard
            label="成功率"
            value={stats.total > 0 ? `${Math.round((stats.succeeded / stats.total) * 100)}%` : '—'}
            color={stats.failed === 0 && stats.total > 0 ? 'green' : stats.failed > 0 ? 'yellow' : 'gray'}
          />
          <TaskEscortStatCard label="失败" value={String(stats.failed)} color={stats.failed > 0 ? 'red' : 'green'} />
          <TaskEscortStatCard
            label="运行中"
            value={String(stats.running)}
            color={stats.running > 0 ? 'blue' : 'gray'}
          />
          <TaskEscortStatCard
            label="失败节点"
            value={String(stats.failedNodes)}
            color={stats.failedNodes > 0 ? 'red' : 'green'}
          />
          <TaskEscortStatCard label="平均时长" value={formatDuration(stats.avgDuration)} color="gray" />
          <TaskEscortStatCard
            label="Token"
            value={stats.totalTokens > 0 ? stats.totalTokens.toLocaleString() : '—'}
            color="gray"
          />
        </div>
      )}

      {/* Runs List */}
      <div className="max-h-[500px] overflow-y-auto">
        {isLoading && flowRuns.length === 0 && !isRefreshing ? (
          <Spin tip="加载中…" />
        ) : flowRuns.length === 0 ? (
          <div className="py-12">
            <Empty compact title="暂无运行记录" description="该工作流尚未执行过。" />
          </div>
        ) : (
          <div className="divide-y divide-[var(--color-border)]">
            {flowRuns.map((run) => (
              <div key={run.flow_id}>
                {/* Run row - clickable */}
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start rounded-none border-0 px-3 py-2 text-left font-normal hover:bg-[var(--color-panel-muted)]"
                  onClick={() => handleRunClick(run.flow_id)}
                >
                  <div className="flex items-center gap-2">
                    {expandedRunId === run.flow_id ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-muted)]" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-mono text-xs font-medium">{run.flow_id}</span>
                        {run.trigger && (
                          <Badge tone="neutral" className="shrink-0 rounded px-1.5 py-0.5 text-[10px]">
                            {TRIGGER_LABEL[run.trigger] || run.trigger}
                          </Badge>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-[var(--color-muted)]">
                        {run.created_by && <span>{run.created_by}</span>}
                        <span>{formatTime(run.started_at)}</span>
                        <span>{formatDuration(run.duration_ms)}</span>
                        <span>
                          {run.succeeded_nodes}/{run.node_count} 节点
                        </span>
                      </div>
                    </div>
                    <Badge tone={STATUS_TONE[run.status] || 'neutral'}>{STATUS_LABEL[run.status] || run.status}</Badge>
                  </div>
                  {run.error_message && (
                    <div className="mt-1 rounded bg-[var(--color-error-soft)] px-2 py-1 font-mono text-[10px] text-[var(--color-error)]">
                      {run.error_message}
                    </div>
                  )}
                </Button>

                {/* Expanded detail */}
                {expandedRunId === run.flow_id && (
                  <div className="border-t border-[var(--color-border)] bg-[var(--color-panel-muted)] px-3 py-2">
                    {/* Tab toggle */}
                    <div className="mb-2 flex gap-1">
                      <Button
                        variant={activeTab === 'nodes' ? 'secondary' : 'ghost'}
                        size="sm"
                        onClick={() => setActiveTab('nodes')}
                      >
                        节点
                      </Button>
                      <Button
                        variant={activeTab === 'dag' ? 'secondary' : 'ghost'}
                        size="sm"
                        onClick={() => setActiveTab('dag')}
                      >
                        DAG
                      </Button>
                    </div>

                    {/* Loading */}
                    {isLoadingDetail && <Spin tip="加载运行详情…" />}

                    {/* Error */}
                    {detailError && !isLoadingDetail && (
                      <div className="rounded bg-[var(--color-error-soft)] px-2 py-1 text-xs text-[var(--color-error)]">
                        {detailError}
                      </div>
                    )}

                    {/* Node list */}
                    {activeTab === 'nodes' && !isLoadingDetail && !detailError && runNodes.length > 0 && (
                      <div className="space-y-1">
                        {runNodes.map((node) => (
                          <Card
                            key={node.node_id}
                            className="flex items-center gap-2 rounded-md bg-[var(--color-panel)] px-2 py-1.5 shadow-none"
                          >
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-xs font-medium">{node.node_title || node.node_id}</div>
                              <div className="truncate text-[10px] text-[var(--color-muted)]">
                                {node.node_id} · {node.executor_type}
                                {node.phase && ` · ${node.phase}`}
                                {node.duration_ms !== null && ` · ${formatDuration(node.duration_ms)}`}
                              </div>
                            </div>
                            <Badge tone={STATUS_TONE[node.status] || 'neutral'}>
                              {STATUS_LABEL[node.status] || node.status}
                            </Badge>
                          </Card>
                        ))}
                      </div>
                    )}

                    {/* DAG view */}
                    {activeTab === 'dag' && !isLoadingDetail && !detailError && workflowSpec && (
                      <WorkflowDagView spec={workflowSpec} />
                    )}
                    {activeTab === 'dag' && !isLoadingDetail && !detailError && !workflowSpec && (
                      <div className="flex h-48 items-center justify-center text-xs text-[var(--color-muted)]">
                        无法加载工作流定义
                      </div>
                    )}

                    {/* Empty nodes */}
                    {!isLoadingDetail && !detailError && runNodes.length === 0 && (
                      <div className="py-4 text-center text-xs text-[var(--color-muted)]">暂无节点执行数据</div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default TaskEscortDetailView;
