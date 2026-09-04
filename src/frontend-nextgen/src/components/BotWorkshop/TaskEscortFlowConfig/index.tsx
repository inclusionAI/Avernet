import type { TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import { useTaskEscortFlowConfig } from '@/hooks/useTaskEscortFlowConfig';
import { dump as yamlDump } from 'js-yaml';
import { Plus, RefreshCw } from 'lucide-react';
import React, { useMemo, useState } from 'react';

import CreateWorkflowFromYamlModal from './CreateWorkflowFromYamlModal';
import WorkflowDagView from './WorkflowDagView';

interface FlowConfigProps {
  botOwnerId?: string;
  botId?: string;
  enabled: boolean;
}

type ViewMode = 'dag' | 'yaml';

function specToYaml(spec: TaskEscortWorkflowSpec): string {
  try {
    return yamlDump(spec, { indent: 2, lineWidth: 120 });
  } catch {
    return '# 无法序列化，可能包含循环引用';
  }
}

/** 安全提取字符串值，防止对象/数组被直接渲染导致 React error #31 */
function toText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

const TaskEscortFlowConfig: React.FC<FlowConfigProps> = ({ botOwnerId, botId, enabled }) => {
  const {
    workflows,
    selectedWorkflowId,
    spec,
    isLoadingList,
    isLoadingSpec,
    isCreatingWorkflow,
    error,
    selectWorkflow,
    refreshList,
    createWorkflowFromYaml,
  } = useTaskEscortFlowConfig({ botOwnerId, botId, enabled });

  const [viewMode, setViewMode] = useState<ViewMode>('dag');
  const [createOpen, setCreateOpen] = useState(false);

  const yamlText = useMemo(() => {
    if (!spec) return '';
    return specToYaml(spec);
  }, [spec]);

  const selectedWorkflow = useMemo(
    () => workflows.find((w) => w.workflowId === selectedWorkflowId),
    [workflows, selectedWorkflowId],
  );

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <Select value={selectedWorkflowId || undefined} onValueChange={(v) => selectWorkflow(v)}>
            <SelectTrigger className="h-8 text-xs">
              {selectedWorkflowId ? (
                <span className="truncate">{selectedWorkflow?.title || selectedWorkflowId}</span>
              ) : (
                <span className="text-[var(--color-muted)]">选择工作流…</span>
              )}
            </SelectTrigger>
            <SelectContent>
              {workflows.length === 0 ? (
                <div className="py-2 text-center text-xs text-[var(--color-muted)]">暂无工作流</div>
              ) : (
                workflows.map((wf) => (
                  <SelectItem key={wf.workflowId} value={wf.workflowId}>
                    <span className="truncate">{wf.title || wf.workflowId}</span>
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
        <Button variant="secondary" size="icon" aria-label="从 YAML 创建工作流" onClick={() => setCreateOpen(true)}>
          <Plus aria-hidden className="h-4 w-4" />
        </Button>
        <Button
          variant="secondary"
          size="sm"
          loading={isLoadingList}
          onClick={refreshList}
          leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
        >
          刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <Card className="border-[var(--color-error-soft)] bg-[var(--color-error-soft)] px-3 py-2 text-xs text-[var(--color-error)] shadow-none">
          {error}
        </Card>
      )}

      {/* Loading */}
      {isLoadingList && workflows.length === 0 && <Spin tip="加载工作流列表…" />}

      {/* Empty */}
      {!isLoadingList && workflows.length === 0 && !error && (
        <Empty compact title="暂无工作流" description="创建工作流后将在此显示。" />
      )}

      {/* Spec content */}
      {selectedWorkflowId && spec && (
        <div className="space-y-3">
          {/* View mode toggle */}
          <Card className="flex gap-1 rounded-lg bg-[var(--color-panel-muted)] p-1 shadow-none">
            <Button variant={viewMode === 'dag' ? 'secondary' : 'ghost'} size="sm" onClick={() => setViewMode('dag')}>
              DAG
            </Button>
            <Button variant={viewMode === 'yaml' ? 'secondary' : 'ghost'} size="sm" onClick={() => setViewMode('yaml')}>
              YAML
            </Button>
          </Card>

          {/* Spec header */}
          <Card>
            <CardContent className="space-y-1 py-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold">{toText(spec.title)}</span>
                <Badge tone="neutral">v{toText(spec.version, '?')}</Badge>
              </div>
              <div className="text-[10px] text-[var(--color-muted)]">{toText(spec.id)}</div>
            </CardContent>
          </Card>

          {/* DAG view */}
          {viewMode === 'dag' && <WorkflowDagView spec={spec} />}

          {/* YAML view */}
          {viewMode === 'yaml' && (
            <Card className="overflow-hidden shadow-none">
              <div className="border-b border-[var(--color-border)] bg-[var(--color-panel-muted)] px-3 py-1.5 text-xs font-medium">
                WorkflowSpec YAML
              </div>
              <pre className="max-h-[400px] overflow-auto bg-[var(--color-panel-muted)] p-3 text-[11px] leading-relaxed">
                <code className="font-mono">{yamlText || '# 无内容'}</code>
              </pre>
            </Card>
          )}
        </div>
      )}

      {/* Spec loading */}
      {selectedWorkflowId && isLoadingSpec && !spec && <Spin tip="加载工作流定义…" />}

      <CreateWorkflowFromYamlModal
        open={createOpen}
        loading={isCreatingWorkflow}
        onOpenChange={setCreateOpen}
        onCreate={createWorkflowFromYaml}
      />
    </div>
  );
};

export default TaskEscortFlowConfig;
