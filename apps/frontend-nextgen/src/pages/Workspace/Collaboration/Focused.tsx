import { Button, Empty, Skeleton } from '@/components/ui';
import { getCollaborationPresentation } from '@/domain/collaborationPresentation';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { GroupWorkspaceArea } from '@/pages/Workspace/GroupWorkspaceArea';
import { useCollaborationScope } from '@/pages/Workspace/hooks/useCollaborationScope';
import { useFocusedCollaborationNavigation } from '@/pages/Workspace/hooks/useFocusedCollaborationNavigation';
import { useLocation } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import CollaborationPage from './index';

function FocusedFrame({ onBack, children }: { onBack: () => void; children: ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex h-10 shrink-0 items-center border-b border-border px-3">
        <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="h-4 w-4" aria-hidden />} onClick={onBack}>
          返回完整协作区
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

function CollaborationOnlyPage() {
  const navigation = useFocusedCollaborationNavigation('only', null);
  return (
    <FocusedFrame onBack={navigation.goBack}>
      <CollaborationPage />
    </FocusedFrame>
  );
}

function ScopedCollaborationPage({ mode, query }: { mode: 'group' | 'session'; query: string }) {
  const projectedQuery = useRef<string | null>(null);
  const model = useCollaborationScope(mode, query, projectedQuery);
  const navigation = useFocusedCollaborationNavigation(mode, model.scope, projectedQuery);
  const { identity } = useHumanIdentity();
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const isDesktop = useMinWidth(1024);
  useEffect(() => {
    if (isDesktop) setMobileListOpen(false);
  }, [isDesktop]);
  return (
    <FocusedFrame onBack={navigation.goBack}>
      {model.error ? (
        <Empty
          title="无法打开指定协作区"
          description={model.error}
          action={
            <Button variant="outline" size="sm" onClick={model.retry}>
              重试
            </Button>
          }
        />
      ) : model.scope ? (
        <div className="flex min-h-0 flex-1">
          <GroupWorkspaceArea
            key={`${model.scope.identityId}:${model.scope.group.groupId}:${mode}`}
            scope={model.scope}
            sessionOnly={mode === 'session'}
            userAvatarUrl={identity?.avatarUrl}
            userIdentityId={identity?.userId}
            userIdentityName={identity?.displayName}
            mobileListOpen={mode !== 'session' && mobileListOpen}
            onCloseMobileList={() => setMobileListOpen(false)}
            onOpenMobileList={mode === 'session' ? undefined : () => setMobileListOpen(true)}
          />
        </div>
      ) : (
        <div role="status" aria-label="加载指定协作区" className="space-y-4 p-6">
          <Skeleton.Block className="h-10 w-1/3" />
          <Skeleton.Block className="h-24 w-full" />
        </div>
      )}
    </FocusedFrame>
  );
}

export default function FocusedCollaborationPage() {
  const location = useLocation();
  const mode = getCollaborationPresentation(location.pathname);
  return mode === 'group' || mode === 'session' ? (
    <ScopedCollaborationPage mode={mode} query={location.search} />
  ) : (
    <CollaborationOnlyPage />
  );
}
