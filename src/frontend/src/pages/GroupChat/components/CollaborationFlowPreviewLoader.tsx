import { Empty, Spin } from '@/components';
import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import React from 'react';
import type { CollaborationBindingView } from '../utils/collaborationGraphLayout';

const LazyCollaborationFlowPreview = React.lazy(
  () => import('./CollaborationFlowPreview'),
);

interface CollaborationFlowPreviewLoaderProps {
  graph: CollaborationDefinitionGraphPreview;
  initialNodes: string[];
  bindingViews: Record<string, CollaborationBindingView>;
  selectedNodeId?: string;
  highlightedBinding?: string;
  onNodeSelect?: (nodeId: string) => void;
}

interface CollaborationFlowPreviewBoundaryState {
  failed: boolean;
}

class CollaborationFlowPreviewBoundary extends React.Component<
  React.PropsWithChildren,
  CollaborationFlowPreviewBoundaryState
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    console.error('[CollaborationFlowPreview] Failed to load preview:', error);
  }

  render() {
    if (this.state.failed) {
      return (
        <Empty
          size="sm"
          className="flex-1"
          title="协作流程加载失败"
          description="角色绑定仍可继续，或重新编辑 YAML 后重试。"
        />
      );
    }
    return this.props.children;
  }
}

const CollaborationFlowPreviewLoader: React.FC<
  CollaborationFlowPreviewLoaderProps
> = (props) => (
  <CollaborationFlowPreviewBoundary>
    <React.Suspense
      fallback={
        <Spin label="加载协作流程..." className="min-h-[300px] flex-1" />
      }
    >
      <LazyCollaborationFlowPreview {...props} />
    </React.Suspense>
  </CollaborationFlowPreviewBoundary>
);

export default CollaborationFlowPreviewLoader;
