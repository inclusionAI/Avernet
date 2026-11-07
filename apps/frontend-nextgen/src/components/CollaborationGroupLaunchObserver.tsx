import { useCollaborationGroupLaunchStore } from '@/stores/collaborationGroupLaunchStore';
import { lazy, Suspense } from 'react';

const LaunchDialog = lazy(() => import('@/pages/Workspace/components/Modals/CollaborationGroupLaunchDialog'));

export function CollaborationGroupLaunchObserver() {
  const request = useCollaborationGroupLaunchStore((state) => state.request);
  if (!request) return null;
  return (
    <Suspense fallback={null}>
      <LaunchDialog key={request.id} request={request} />
    </Suspense>
  );
}
