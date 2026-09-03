import { getCapabilities } from '@/capabilities';
import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { initSpaceContext } from '@/hooks/useSpaceContext';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useLocation } from '@umijs/max';
import React, { useEffect, useMemo, useState } from 'react';
import type { AccountUser } from './AccountBadge';
import { AppHeader } from './AppHeader';
import { AppSidebar } from './AppSidebar';
import { SidebarNavList } from './SidebarNavList';
import { getMergedNavigationItems, getNavigationItem, type NavigationArea } from './navigation';

export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const activeItem = useMemo(() => getNavigationItem(location.pathname), [location.pathname]);
  // 合并 Open Core 基线与 internal overlay 注入的额外导航项（capability 同步返回，无请求）。
  const mergedItems = useMemo(() => getMergedNavigationItems(), []);
  const [area, setArea] = useState<NavigationArea>(activeItem?.area ?? 'work');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [currentUser, setCurrentUser] = useState<AccountUser | undefined>();
  const isDesktop = useMinWidth(1024);

  useEffect(() => {
    if (activeItem) setArea(activeItem.area);
  }, [activeItem]);
  // 视口回到桌面（≥lg）时收起一级导航抽屉，避免抽屉压住重新出现的内流侧栏。
  useEffect(() => {
    if (isDesktop) setMobileNavOpen(false);
  }, [isDesktop]);
  const changeArea = (nextArea: NavigationArea) => {
    setArea(nextArea);
    history.push(nextArea === 'work' ? '/work' : '/manage');
  };
  // <lg 抽屉内点导航：跳转并收起抽屉。
  const handleMobileNavigate = (path: string) => {
    history.push(path);
    setMobileNavOpen(false);
  };

  // 只有进入「管理」区域时才初始化空间上下文；工作区域不需要加载空间列表。
  useEffect(() => {
    if (area === 'manage') void initSpaceContext();
  }, [area]);

  // 挂载即解析当前登录身份（listMyBots human[0] → workspaceStore.identities），
  // 供全局 AccountBadge 消费。单飞复用 identityService，与 /work init 共用 inflight，零重复请求。
  // 幂等：单飞进行中/已成功时跳过；失败静默（AccountBadge 走 error 态）。
  //
  // 关键：currentUser 必须经 capability 契约（getHumanIdentity）解析，而非直接透传后端 DTO。
  // 内部 overlay 可提供独立头像兜底；直接透传后端 DTO 会绕过 capability，因此统一从契约读取。
  useEffect(() => {
    void identityService.loadIdentities().then((res) => {
      if (res.ok) {
        useWorkspaceStore.getState().setIdentities(res.data.identities, res.data.defaultActiveId);
        const identity = getCapabilities().getHumanIdentity().value;
        setCurrentUser(identity ? { displayName: identity.displayName, avatarUrl: identity.avatarUrl } : undefined);
      }
    });
  }, []);

  return (
    <div className="flex h-full flex-col bg-[var(--color-bg)]">
      <AppHeader
        area={area}
        sidebarCollapsed={sidebarCollapsed}
        onAreaChange={changeArea}
        onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
        onOpenMobileNav={() => setMobileNavOpen(true)}
        currentUser={currentUser}
      />
      <div className="flex min-h-0 flex-1">
        <AppSidebar
          area={area}
          activePath={location.pathname}
          collapsed={sidebarCollapsed}
          items={mergedItems}
          onNavigate={(path) => history.push(path)}
        />
        <main className="relative min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
      {/* <lg 一级导航抽屉：≥lg 内流侧栏可见；<lg 由 AppHeader 汉堡触发本抽屉。 */}
      <Drawer open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">{area === 'work' ? '工作导航' : '管理导航'}</DrawerTitle>
          <SidebarNavList
            area={area}
            activePath={location.pathname}
            items={mergedItems}
            onNavigate={handleMobileNavigate}
          />
        </DrawerContent>
      </Drawer>
    </div>
  );
}
