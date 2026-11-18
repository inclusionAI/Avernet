import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import type { TaskEscortParityContext, TaskEscortWorkflowSummary } from '@/domain/taskEscortParity/types';
import type { PageId } from '@/domain/taskEscortWorkspace/types';
import { Plus, RefreshCw } from 'lucide-react';
import { pages } from './Navigation';
import { WorkspaceWorkflowPicker } from './parity/WorkspaceWorkflowPicker';

interface WorkspaceHeaderProps {
  page: PageId;
  context: TaskEscortParityContext;
  workflows: TaskEscortWorkflowSummary[];
  selectWorkflow: (id: string) => void;
}

export function WorkspaceHeader(props: WorkspaceHeaderProps) {
  const { page, context, workflows, selectWorkflow } = props;
  const info = pages.find((item) => item.id === (page === 'workbench' ? 'issues' : page))!;
  const workflow = context.workflow;
  return (
    <div className="workspace-context-header">
      <PageHeader
        title={info.title}
        description={
          <>
            <span>{info.description}</span>
            {workflow && (
              <span className="workspace-metadata">
                <span className="font-mono">{workflow.id}</span>
                <span>{workflow.runCount === null ? '运行次数未知' : `${workflow.runCount} 次运行`}</span>
                <span>{workflow.lastStatus ?? '最近状态未知'}</span>
                {workflow.ownerId && (
                  <span>
                    负责人 <span className="font-mono">{workflow.ownerId}</span>
                  </span>
                )}
              </span>
            )}
          </>
        }
        actions={
          <div className="workspace-context-actions">
            <WorkspaceWorkflowPicker
              workflows={workflows}
              selected={workflow}
              onSelect={selectWorkflow}
              loading={context.loading}
            />
            <Button size="sm" disabled aria-describedby="workspace-create-unavailable">
              <Plus size={14} aria-hidden />
              新建工作流
            </Button>
            <Button variant="outline" size="sm" disabled={context.loading} onClick={context.refreshWorkflows}>
              <RefreshCw size={14} aria-hidden />
              刷新数据
            </Button>
          </div>
        }
      />
      <p id="workspace-create-unavailable" className="mt-2 text-xs text-muted-foreground">
        新建工作流尚未接入已确认的安全创建契约。
      </p>
    </div>
  );
}
