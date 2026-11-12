import React, { useEffect, useState } from 'react';
import { TaskPanelAssetBoundary } from './TaskPanelAssetBoundary';
import type { TaskPanelAsset } from '@/services/tasks/panel/taskPanelAssetLoader';
import { getTaskPanelController, nextgenTaskPanelHost } from '@/services/tasks/panel/taskPanelRuntime';
import type { TaskPanelController, TaskPanelProps } from '@avernet-assets/task-panel';
import '@/services/tasks/panel/taskPanelShell.css';

type HostProps = TaskPanelProps & { onSelectTask?: (taskId: string) => void };
type ControllerState =
  | { status: 'loading' }
  | { status: 'ready'; controller: TaskPanelController }
  | { status: 'error'; error: unknown };

function TaskLoopMount({ asset, props }: { asset: TaskPanelAsset; props: HostProps }) {
  const [state, setState] = useState<ControllerState>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    void Promise.resolve().then(() => getTaskPanelController()).then(
      controller => { if (live) setState({ status: 'ready', controller }); },
      error => { if (live) setState({ status: 'error', error }); },
    );
    return () => { live = false; };
  }, []);
  if (state.status === 'error') throw state.error;
  if (state.status === 'loading') return <div role="status">正在加载任务副屏…</div>;
  return <asset.TaskLoopView {...props} host={nextgenTaskPanelHost} controller={state.controller} />;
}

/** Thin adapter: runtime owns the controller; the boundary owns asset recovery. */
export function TaskPanelHost(props: HostProps): React.ReactElement {
  return (
    <TaskPanelAssetBoundary>
      {asset => <TaskLoopMount asset={asset} props={props} />}
    </TaskPanelAssetBoundary>
  );
}
