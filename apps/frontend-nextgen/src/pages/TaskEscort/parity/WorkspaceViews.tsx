import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { PageId } from '@/domain/taskEscortWorkspace/types';
import { WorkflowEditorPage } from './editor/WorkflowEditorPage';
import { ExperiencePage } from './evolution/ExperiencePage';
import { IssuePage } from './evolution/IssuePage';
import { ManagementPage } from './management/ManagementPage';
import { OverviewPage } from './overview/OverviewPage';

function WorkspaceLoading() {
  return (
    <div role="status">
      <Spin tip="正在读取工作流" />
    </div>
  );
}

export function WorkspaceViews({ page, context }: { page: PageId; context: TaskEscortParityContext }) {
  if (context.loading) return <WorkspaceLoading />;
  if (context.error)
    return (
      <Empty
        title="工作流读取失败"
        description={context.error}
        action={
          <Button variant="outline" onClick={context.refreshWorkflows}>
            重试读取
          </Button>
        }
      />
    );
  if (!context.ready || !context.workflow)
    return (
      <Empty
        title={context.workflow ? '工作区尚未就绪' : '暂无可访问的工作流'}
        description="当前身份没有可用的工作流数据，请检查访问权限或刷新重试。"
        action={
          <Button variant="outline" onClick={context.refreshWorkflows}>
            刷新工作流
          </Button>
        }
      />
    );
  const View = {
    runs: OverviewPage,
    issues: IssuePage,
    workbench: IssuePage,
    experiences: ExperiencePage,
    editor: WorkflowEditorPage,
    settings: ManagementPage,
    dashboard: null,
  }[page];
  return View ? <View context={context} /> : null;
}
