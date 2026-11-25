import React, { useEffect, useState, useSyncExternalStore } from 'react';
import { loadTaskPanelAsset, type TaskPanelAsset } from '@/services/tasks/panel/taskPanelAssetLoader';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { nextgenTaskPanelHost, getTaskPanelRuntimeGeneration, subscribeTaskPanelRuntime } from '@/services/tasks/panel/taskPanelRuntime';

type AssetBoundaryProps = { children: (asset: TaskPanelAsset) => React.ReactNode };
type AssetLoadState =
  | { status: 'loading' }
  | { status: 'ready'; asset: TaskPanelAsset }
  | { status: 'error'; error: unknown };

/** Keep the render-prop invocation below the React error boundary as well. */
function TaskAssetMount({ children }: AssetBoundaryProps) {
  const [state, setState] = useState<AssetLoadState>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    // The loader owns shared in-flight/success caching and evicts failed loads.
    void Promise.resolve().then(() => loadTaskPanelAsset()).then(
      asset => { if (live) setState({ status: 'ready', asset }); },
      error => { if (live) setState({ status: 'error', error }); },
    );
    return () => { live = false; };
  }, []);
  if (state.status === 'error') throw state.error;
  if (state.status === 'loading') return <div role="status">正在加载任务组件…</div>;
  const { asset } = state;
  return (
    <asset.TaskPanelHostContext.Provider value={nextgenTaskPanelHost}>
      {children(asset)}
    </asset.TaskPanelHostContext.Provider>
  );
}

/** Isolate asset failures without introducing an error UI or resetting the shared runtime. */
class TaskAssetRenderBoundary extends React.Component<React.PropsWithChildren, { error: string | null }> {
  state = { error: null as string | null };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : String(error) };
  }

  render() {
    if (this.state.error !== null) {
      return null;
    }
    return this.props.children;
  }
}

/** Auxiliary task views and the chat panel share loading and render recovery. */
export function TaskPanelAssetBoundary({ children }: AssetBoundaryProps) {
  const viewerId = useSyncExternalStore(useWorkspaceStore.subscribe, () => nextgenTaskPanelHost.getViewer()?.userId ?? null, () => null);
  const generation = useSyncExternalStore(subscribeTaskPanelRuntime, getTaskPanelRuntimeGeneration, getTaskPanelRuntimeGeneration);
  return (
    <TaskAssetRenderBoundary key={JSON.stringify([viewerId, generation])}>
      <TaskAssetMount>{children}</TaskAssetMount>
    </TaskAssetRenderBoundary>
  );
}
