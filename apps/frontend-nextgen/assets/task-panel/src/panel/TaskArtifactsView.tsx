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
        {({ task, loading }) => {
          if (loading && !task) return <div role="status"><Empty description="加载任务产物中…" /></div>;
          if (!task) return null;
          return <TaskArtifactsList task={task} apiBaseUrl={apiBaseUrl} title="产物列表" previewPortalContainer={previewPortalContainer} />;
        }}
      </TaskPanelFetcher>
    </div>
  );
}
