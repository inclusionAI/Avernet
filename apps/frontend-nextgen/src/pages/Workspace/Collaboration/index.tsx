// 协作页(/workspace/collaboration,Task 8):承接旧混合 Workspace 页的协作群分支。
// useGroupWorkspace / GroupWorkspaceArea(会话/群管理面板、成员面板、邀请、
// membership 深链)原样保留;跨模块切换收口到 App Shell 一级导航,
// 本页不再含二级视图切换。页内数据源不依赖展开的 chat 分支。
import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { GroupWorkspaceArea } from '@/pages/Workspace/GroupWorkspaceArea';
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useEffect, useState } from 'react';

export default function CollaborationPage(): JSX.Element {
  const { identity } = useHumanIdentity();
  // group 深链(current/group/session/membership)hydration 沿用 useWorkspacePage;
  // 出向投影在本页落定:旧投影 gate 在 store.view==='group',本页是独立路由、
  // 不再依赖该 store 视图位(避免 bot 身份 availableViews 钳制干扰投影)。
  const { urlHydrated, activeIdentityId } = useWorkspacePage();
  const selectedGroupId = useWorkspaceStore((s) => s.selectedGroupId);
  const selectedSessionId = useWorkspaceStore((s) => s.selectedSessionId);
  const membership = useWorkspaceStore((s) => s.membership);

  // <lg 二级协作群列表抽屉开关(桌面收起,对齐旧页行为)。
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const isDesktop = useMinWidth(1024);
  useEffect(() => {
    if (isDesktop) setMobileListOpen(false);
  }, [isDesktop]);

  // 群聊 Store → URL 投影(语义与旧 useWorkspacePage 的 group 投影一致,视图恒 group)。
  useEffect(() => {
    if (!urlHydrated) return;
    const next = serializeWorkspaceRoute({
      currentIdentityId: activeIdentityId,
      groupId: selectedGroupId,
      sessionId: selectedSessionId,
      membership,
    });
    const current = window.location.search.replace(/^\?/, '');
    if (next === current) return;
    history.replace(`${window.location.pathname}?${next}${window.location.hash}`);
  }, [activeIdentityId, membership, selectedGroupId, selectedSessionId, urlHydrated]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
        <GroupWorkspaceArea
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
