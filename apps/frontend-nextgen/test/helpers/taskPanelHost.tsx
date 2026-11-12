import React from 'react';
import { TaskPanelHostContext } from '../../assets/task-panel/src/panel/hostContext';
import { nextgenTaskPanelHost } from '@/services/tasks/panel/taskPanelRuntime';
export { nextgenTaskPanelHost as taskPanelTestHost };
export function TaskPanelTestHost({ children }: { children: React.ReactNode }): React.ReactElement {
  return <TaskPanelHostContext.Provider value={nextgenTaskPanelHost}>{children}</TaskPanelHostContext.Provider>;
}

import * as taskPanelAsset from '../../assets/task-panel/src';
export function TaskPanelTestAssetBoundary({ children }: { children: (asset: typeof taskPanelAsset) => React.ReactNode }) {
  return <TaskPanelTestHost>{children(taskPanelAsset)}</TaskPanelTestHost>;
}
