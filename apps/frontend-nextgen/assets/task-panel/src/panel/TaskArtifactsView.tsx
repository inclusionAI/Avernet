import React from 'react';
import { TaskPanelFetcher } from './TaskPanelFetcher';
import { Empty } from './theme';
import { C } from './tokens';
import { TaskArtifactsList } from './v2/TaskArtifactsList';

export interface TaskArtifactsViewProps {
  taskId: string;
  /** Owner used for task-list metadata; never inferred from the authenticated viewer. */
  ownerUserId: string;
  apiBaseUrl: string;
  taskApiBase?: string;
  previewPortalContainer?: HTMLElement | null;
}

/** Complete artifact view. HostContext supplies transport/access, not host UI components. */
export function TaskArtifactsView({
  taskId, ownerUserId, apiBaseUrl, taskApiBase, previewPortalContainer,
}: TaskArtifactsViewProps): React.ReactElement {
  return (
    <div data-task-panel-root="true" style={{ minHeight: 0, color: C.textPrimary, background: C.surface }}>
      <TaskPanelFetcher
        key={`${taskId}:${ownerUserId}`}
        taskId={taskId}
        userId={ownerUserId}
        apiBaseUrl={apiBaseUrl}
        taskApiBase={taskApiBase}
        includeActionLog={false}
      >
        {({ task, loading, error, retry }) => {
          if (loading && !task) return <div role="status"><Empty description="加载任务产物中…" /></div>;
          if (error && !task) {
            return (
              <div role="alert" style={{ padding: 24, textAlign: 'center' }}>
                <div style={{ color: C.danger, fontSize: 13, marginBottom: 12 }}>任务产物加载失败</div>
                <div style={{ color: C.textSecondary, fontSize: 13, marginBottom: 12 }}>{error}</div>
                <button type="button" onClick={retry} style={{
                  padding: '6px 16px', border: `1px solid ${C.primary}`, borderRadius: 4,
                  background: C.surface, color: C.primary, cursor: 'pointer',
                }}>重试</button>
              </div>
            );
          }
          if (!task) return null;
          return <TaskArtifactsList task={task} apiBaseUrl={apiBaseUrl} title="产物列表" previewPortalContainer={previewPortalContainer} />;
        }}
      </TaskPanelFetcher>
    </div>
  );
}
