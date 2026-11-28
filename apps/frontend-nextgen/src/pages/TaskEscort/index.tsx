import { Button, Empty } from '@/components/ui';
import { Segmented } from '@/components/ui/Segmented';
import { useTaskEscortWorkspaceContext } from '@/hooks/taskEscortParity/useTaskEscortWorkspaceContext';
import { useTaskEscortDashboardAccess } from '@/hooks/taskEscortWorkspace/useTaskEscortDashboardAccess';
import { useTaskEscortWorkspaceScope } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceScope';
import { useTaskEscortWorkspace } from '@/hooks/useTaskEscortWorkspace';
import { clearWorkspaceDeepLinks } from '@/services/taskEscortParity/workspaceNavigation';
import { history } from '@umijs/max';
import { useEffect, useRef } from 'react';
import { Dashboard } from './Dashboard';
import { GuardSidebar } from './Navigation';
import { WorkspaceHeader } from './WorkspaceHeader';
import { WorkspaceIntroduction } from './WorkspaceIntroduction';
import { WorkspaceViews } from './parity/WorkspaceViews';
import './styles.css';

export default function TaskEscortPage() {
  const scope = useTaskEscortWorkspaceScope();
  const legacyWorkspace = useTaskEscortWorkspace(scope.key, { ...scope, loadWorkspace: false });
  const { context, page, workflows, selectWorkflow, showIntroduction, setProductNavigation } =
    useTaskEscortWorkspaceContext(scope);
  const dashboardAccess = useTaskEscortDashboardAccess(scope);
  const showDashboard = dashboardAccess !== 'denied';
  const editorWorkspace = page === 'editor' && !showIntroduction;
  // 换空间后旧空间的 workflowId / runId 等深链必须失效，否则会串用上一个空间的数据或报“无权访问”。
  const previousScopeKey = useRef(scope.key);
  useEffect(() => {
    if (previousScopeKey.current === scope.key) return;
    previousScopeKey.current = scope.key;
    const current = history.location;
    const cleaned = clearWorkspaceDeepLinks(current.search);
    if (cleaned !== current.search) history.replace(`${current.pathname}${cleaned}${current.hash}`);
  }, [scope.key]);
  // 普通身份即使显式深链到数据大盘，也直接回到运行概览，不展示无权死胡同。
  useEffect(() => {
    if (page === 'dashboard' && dashboardAccess === 'denied' && !showIntroduction) context.navigate('runs');
  }, [page, dashboardAccess, showIntroduction, context.navigate]);
  // Keep the real legacy controller intact for the independently owned dashboard.
  const dashboardController = {
    ...legacyWorkspace,
    navigate: context.navigate,
    setWorkflowId: selectWorkflow,
  };
  return (
    <div
      className={`task-escort-workspace task-escort-workspace-root${editorWorkspace ? ' task-escort-editor' : ''}`}
    >
      <div className="guard-layout">
        <GuardSidebar
          page={page}
          navigate={context.navigate}
          showIntroduction={showIntroduction}
          showDashboard={showDashboard}
        />
        <section
          aria-label="任务护航内容"
          tabIndex={-1}
          data-detail-focus-fallback
          className={`page-scroll${showIntroduction ? ' task-escort-introduction-page' : ''}`}
          key={`${scope.key}:${page}`}
        >
          <div className="flex justify-end border-b border-border bg-background px-4 py-3 sm:px-6">
            <Segmented
              aria-label="任务护航视图"
              value={showIntroduction ? 'productNavigation' : 'workspace'}
              options={[
                { value: 'productNavigation', label: '产品导航' },
                {
                  value: 'workspace',
                  label: '工作区',
                  disabledReason: showIntroduction && !context.workflow ? '创建工作流后即可进入工作区' : undefined,
                },
              ]}
              onChange={(value) => setProductNavigation(value === 'productNavigation')}
            />
          </div>
          <div className="content-area">
            {showIntroduction ? (
              <WorkspaceIntroduction context={context} />
            ) : page === 'dashboard' && context.ready ? (
              <Dashboard demo={dashboardController} scope={scope} />
            ) : (
              <>
                <WorkspaceHeader page={page} context={context} workflows={workflows} selectWorkflow={selectWorkflow} />
                {!scope.ready && !context.loading && !context.error ? (
                  <Empty
                    title="工作区尚未就绪"
                    description="请先加载当前用户身份，再读取可访问的工作流。"
                    action={
                      <Button variant="outline" onClick={context.refreshWorkflows}>
                        刷新工作流
                      </Button>
                    }
                  />
                ) : (
                  <WorkspaceViews page={page} context={context} />
                )}
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
