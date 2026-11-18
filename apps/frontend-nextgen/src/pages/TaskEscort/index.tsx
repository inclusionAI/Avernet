import { useTaskEscortWorkspaceContext } from '@/hooks/taskEscortParity/useTaskEscortWorkspaceContext';
import { useTaskEscortWorkspaceScope } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceScope';
import { useTaskEscortWorkspace } from '@/hooks/useTaskEscortWorkspace';
import { Dashboard } from './Dashboard';
import { GuardSidebar } from './Navigation';
import { WorkspaceHeader } from './WorkspaceHeader';
import { WorkspaceViews } from './parity/WorkspaceViews';
import './styles.css';

export default function TaskEscortPage() {
  const scope = useTaskEscortWorkspaceScope();
  const legacyWorkspace = useTaskEscortWorkspace(scope.key, { ...scope, loadWorkspace: false });
  const { context, page, workflows, selectWorkflow } = useTaskEscortWorkspaceContext(scope);
  // Keep the real legacy controller intact for the independently owned dashboard.
  const dashboardController = {
    ...legacyWorkspace,
    navigate: context.navigate,
    setWorkflowId: selectWorkflow,
  };
  return (
    <div
      className={`task-escort-workspace task-escort-workspace-root${page === 'editor' ? ' task-escort-editor' : ''}`}
    >
      <div className="guard-layout">
        <GuardSidebar page={page} navigate={context.navigate} />
        <section
          aria-label="任务护航内容"
          tabIndex={-1}
          data-detail-focus-fallback
          className="page-scroll"
          key={`${scope.key}:${page}`}
        >
          <div className="content-area">
            {page === 'dashboard' ? (
              <Dashboard demo={dashboardController} scope={scope} />
            ) : (
              <>
                <WorkspaceHeader page={page} context={context} workflows={workflows} selectWorkflow={selectWorkflow} />
                <WorkspaceViews page={page} context={context} />
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
