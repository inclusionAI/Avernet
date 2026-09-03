import { Drawer, DrawerContent, DrawerTitle } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { ensurePersonalSpaceOnAppEntry, initSpaceContext } from '@/hooks/useSpaceContext';
import { identityService } from '@/services/workspace/identityService';
import { history, useLocation } from '@umijs/max';
import React, { useEffect, useMemo, useState } from 'react';
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

  // 挂载（进入项目）即初始化一次个人空间：不等进管理区域，幂等单飞、失败静默（详见 useSpaceContext）。
  useEffect(() => {
    void ensurePersonalSpaceOnAppEntry();
  }, []);
  // 只有进入「管理」区域时才初始化空间上下文；工作区域不需要加载空间列表。
  useEffect(() => {
    if (area === 'manage') void initSpaceContext();
  }, [area]);

  // 挂载即触发身份加载（listMyBots → workspaceStore.identities，由 useHumanIdentity 写回 store），
  // 供全局 AccountBadge 消费。单飞复用 identityService，与 /work init 共用 inflight，零重复请求。
  // 失败静默（AccountBadge 走 error 态）。
  //
  // 关键：currentUser 经 useHumanIdentity 反应式派生（内部走 capability 契约 getHumanIdentity，不直接
  // 透传后端 DTO），不在加载回调里一次性快照 —— Open Core（oauth-provider）下 /auth/user（AppLayout
  // boot 的 checkAuth）与 mine 并跑，早于 auth 落位 captured 的 mine 兜底身份会被冻结进顶栏，
  // 登录后头像/花名不一致（此前需切 tab 触发 re-render 才纠正）。
  useEffect(() => {
    void identityService.loadIdentities();
  }, []);
  const { identity } = useHumanIdentity();
  const currentUser = useMemo(
    () => (identity ? { displayName: identity.displayName, avatarUrl: identity.avatarUrl } : undefined),
    [identity],
  );

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
