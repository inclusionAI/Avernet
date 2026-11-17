// 协作页(/workspace/collaboration,Task 8):承接旧混合 Workspace 页的协作群分支。
// useGroupWorkspace / GroupWorkspaceArea(会话/群管理面板、成员面板、邀请、
// membership 深链)原样保留;跨模块切换收口到 App Shell 一级导航,
// 本页不再含二级视图切换。页内数据源不依赖展开的 chat 分支。
import { Button, Empty, Spin } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { GroupWorkspaceArea } from '@/pages/Workspace/GroupWorkspaceArea';
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { reloadCurrentTab } from '@/utils/redirectCurrentTab';
import { history } from '@umijs/max';
import { useEffect, useState } from 'react';

export default function CollaborationPage(): JSX.Element {
  const { identity } = useHumanIdentity();
  // URL 回填、规范化投影与一次性定位意图共用同一编排，避免将自身投影误判为新深链。
  const { urlHydrated, sessionLocationRequest, hydrationError } = useWorkspacePage();

  // <lg 二级协作群列表抽屉开关(桌面收起,对齐旧页行为)。
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const isDesktop = useMinWidth(1024);
  useEffect(() => {
    if (isDesktop) setMobileListOpen(false);
  }, [isDesktop]);

  // 指定 Human 尚未出现在身份列表时，不挂载另一身份的群/会话请求。
  if (!urlHydrated && hydrationError) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty title="无法打开指定协作区" description={hydrationError} action={
          <div className="flex gap-2">
            <Button size="sm" onClick={reloadCurrentTab}>刷新页面</Button>
            <Button size="sm" variant="outline" onClick={() => history.replace('/workspace/collaboration')}>返回协作区</Button>
          </div>
        } />
      </div>
    );
  }
  if (!urlHydrated) {
    return <div className="flex h-full items-center justify-center"><Spin tip="正在打开协作区…" /></div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
        <GroupWorkspaceArea
          sessionLocationRequest={sessionLocationRequest}
          userAvatarUrl={identity?.avatarUrl}
          userIdentityId={identity?.userId}
          userIdentityName={identity?.displayName}
          mobileListOpen={mobileListOpen}
          onCloseMobileList={() => setMobileListOpen(false)}
          onOpenMobileList={() => setMobileListOpen(true)}
        />
      </div>
    </div>
  );
}
