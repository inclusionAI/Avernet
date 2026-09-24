import { Drawer, DrawerContent, DrawerTitle, IconButton } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { ensurePersonalSpaceOnAppEntry, initSpaceContext } from '@/hooks/useSpaceContext';
import { workspaceService } from '@/services/workspace/workspaceService';
import { history, useLocation } from '@umijs/max';
import { Menu } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { AppSidebar } from './AppSidebar';
import { OpenSourceExperienceNotice } from './OpenSourceExperienceNotice';
import { SidebarNavList } from './SidebarNavList';
import { getMergedNavigationItems, getNavigationItem } from './navigation';

/**
 * 全局壳（refactor-global-nav-shell）：单侧边栏 + 内容区两栏布局，顶栏（AppHeader）已退役，
 * 原「工作/管理」区域 Tab 随之移除——协作/Bot 双分组常驻侧栏，不再按区域整体换组。
 * 顶栏功能（Logo/通知中心/用户身份/帮助）全部归位进 AppSidebar。
 * <lg 视口：内流侧栏由 AppSidebar hidden，经左上悬浮汉堡打开一级导航抽屉（与桌面同数据源 SidebarNavList）。
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const activeItem = useMemo(() => getNavigationItem(location.pathname), [location.pathname]);
  // 合并 Open Core 基线与 internal overlay 注入的额外导航项（capability 同步返回，无请求）。
  const mergedItems = useMemo(() => getMergedNavigationItems(), []);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const isDesktop = useMinWidth(1024);

  // 空间上下文初始化时机（3.1）：进入 Bot 分组任一路由时触发（替换原 area==='manage'）——覆盖切换器
  // 被形态开关隐藏（spaceSwitcher=false）时 Bot 页面对 spaceContextStore 的消费；
  // 切换器自身的挂载/打开触发由 SpaceSwitcher 承担（幂等+并发单飞，同帧不重复请求）。
  const activeSection = activeItem?.section;
  useEffect(() => {
    if (activeSection === 'bot') void initSpaceContext();
  }, [activeSection]);
  // 视口回到桌面（≥lg）时收起一级导航抽屉，避免抽屉压住重新出现的内流侧栏。
  useEffect(() => {
    if (isDesktop) setMobileNavOpen(false);
  }, [isDesktop]);
  // <lg 抽屉内点导航：跳转并收起抽屉。
  const handleMobileNavigate = (path: string) => {
    history.push(path);
    setMobileNavOpen(false);
  };

  // 挂载（进入项目）即初始化一次个人空间：不等进 Bot 分组，幂等单飞、失败静默（详见 useSpaceContext）。
  useEffect(() => {
    void ensurePersonalSpaceOnAppEntry();
  }, []);

  // 挂载即刷新协作身份列表。initWorkspace 会在成功后写回 workspaceStore.identities 与 activeIdentity，
  // 避免只在 getHumanIdentity 能力已 ready（如 external auth / internal cookie）时跳过身份列表落 store。
  // identityService 单飞保证与 /workspace 初始化共用同一 /mine 请求。
  // 失败静默（AccountBadge / IdentitySelector 各自呈现 error 态）。
  //
  // 关键：currentUser 经 useHumanIdentity 反应式派生（内部走 capability 契约 getHumanIdentity，不直接
  // 透传后端 DTO），不在加载回调里一次性快照 —— Open Core（oauth-provider）下 /auth/user（AppLayout
  // boot 的 checkAuth）与 mine 并跑，早于 auth 落位 captured 的 mine 兜底身份会被冻结进侧栏用户行，
  // 登录后头像/花名不一致（此前需切 tab 触发 re-render 才纠正）。
  useEffect(() => {
    void workspaceService.initWorkspace();
  }, []);
  const { identity } = useHumanIdentity();
  const currentUser = useMemo(
    () => (identity ? { displayName: identity.displayName, avatarUrl: identity.avatarUrl } : undefined),
    [identity],
  );

  return (
    <div className="flex h-full flex-col bg-[var(--color-bg)]">
      <OpenSourceExperienceNotice />
      <div className="flex min-h-0 flex-1">
        <AppSidebar
          activePath={location.pathname}
          collapsed={sidebarCollapsed}
          items={mergedItems}
          onNavigate={(path) => history.push(path)}
          onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
          currentUser={currentUser}
        />
        <main className="relative min-w-0 flex-1 overflow-hidden">
          {/* <lg 一级导航入口（原顶栏汉堡归位为悬浮按钮）：≥lg 隐藏(内流侧栏可见)。 */}
          <div className="absolute left-2 top-2 z-40 lg:hidden">
            <IconButton label="打开导航" icon={<Menu className="h-4 w-4" />} onClick={() => setMobileNavOpen(true)} />
          </div>
          {children}
        </main>
      </div>
      {/* <lg 一级导航抽屉：≥lg 内流侧栏可见；<lg 由左上悬浮汉堡触发本抽屉。 */}
      <Drawer open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
          <DrawerTitle className="sr-only">主导航</DrawerTitle>
          <SidebarNavList activePath={location.pathname} items={mergedItems} onNavigate={handleMobileNavigate} />
        </DrawerContent>
      </Drawer>
    </div>
  );
}
