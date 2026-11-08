import { Drawer, DrawerContent, DrawerTitle, IconButton } from '@/components/ui';
import { PersistentChatLayoutPanelViewport } from '@/components/Workspace/PersistentChatLayoutPanelViewport';
import { PrimaryNavContext, type PrimaryNavApi } from '@/components/Workspace/PrimaryNav';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useMinWidth } from '@/hooks/useMediaQuery';
import { ensurePersonalSpaceOnAppEntry, initSpaceContext } from '@/hooks/useSpaceContext';
import { workspaceService } from '@/services/workspace/workspaceService';
import { isTaskNavigationAtCurrentRoute } from '@/services/tasks/taskConversationNavigation';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { history, useLocation } from '@umijs/max';
import { Menu } from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppSidebar } from './AppSidebar';
import { OpenSourceExperienceNotice } from './OpenSourceExperienceNotice';
import { SidebarNavList } from './SidebarNavList';
import { getMergedNavigationItems, getNavigationItem } from './navigation';
import { getRouteMeta } from './routeMeta';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

/**
 * 全局壳（refactor-global-nav-shell）：单侧边栏 + 内容区两栏布局，顶栏（AppHeader）已退役，
 * 原「工作/管理」区域 Tab 随之移除——协作/Bot 双分组常驻侧栏，不再按区域整体换组。
 * 顶栏功能（Logo/通知中心/用户身份/帮助）全部归位进 AppSidebar。
 * <lg 视口：chat/group 页头经 PrimaryNavContext 就地渲染一级导航入口（与「打开会话列表」
 * 同排、位于其左）；页头登记内联入口后，内容区顶部占位条带被抑制（2026-10-19 用户反馈：
 * 条带+页头双行占位）。无页头的路由（非对话页、空 Bot/空群的页头缺席阶段）由条带兜底，
 * 一级导航任何公开路由始终可达；精简视图（navigationVisibility=hidden）不渲染条带、零预留。
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const hideNavigation = getRouteMeta(location.pathname)?.navigationVisibility === 'hidden';
  const activeItem = useMemo(() => getNavigationItem(location.pathname), [location.pathname]);
  // 合并 Open Core 基线与 internal overlay 注入的额外导航项（capability 同步返回，无请求）。
  const mergedItems = useMemo(() => getMergedNavigationItems(), []);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // 页头内联一级导航入口的挂载数：>0 时抑制壳的 h-12 兜底条带（登记来自 PrimaryNavToggle）。
  const [inlineNavEntries, setInlineNavEntries] = useState(0);
  const isDesktop = useMinWidth(1024);

  // 页头「打开导航」通道：回调恒稳定（上下文值不变 → 页头登记 effect 不重跑、无登记/撤销环）。
  const openNav = useCallback(() => setMobileNavOpen(true), []);
  const mountNavEntry = useCallback(() => setInlineNavEntries((count) => count + 1), []);
  const unmountNavEntry = useCallback(() => setInlineNavEntries((count) => Math.max(0, count - 1)), []);
  const primaryNavApi = useMemo<PrimaryNavApi>(
    () => ({ openNav, mountNavEntry, unmountNavEntry }),
    [openNav, mountNavEntry, unmountNavEntry],
  );

  // 空间上下文初始化时机（3.1）：进入 Bot 分组任一路由时触发（替换原 area==='manage'）——覆盖切换器
  // 被形态开关隐藏（spaceSwitcher=false）时 Bot 页面对 spaceContextStore 的消费；
  // 切换器自身的挂载/打开触发由 SpaceSwitcher 承担（幂等+并发单飞，同帧不重复请求）。
  const activeSection = activeItem?.section;
  // 任务副屏的保活 lease 只覆盖「源会话 → 导航目标会话」这一次 handoff。
  // 如果路由实际离开目标（退出会话页/普通路由切换），必须通知持久副屏宿主强制关闭；
  // 否则旧任务 tab 会跨路由悬挂，并在后续会话里把别的会话任务继续展示出来。
  const previousTaskRouteKeyRef = useRef<string | null>(null);
  const taskRouteKey = `${location.pathname}${location.search ?? ''}${location.hash ?? ''}`;
  useEffect(() => {
    const previousTaskRouteKey = previousTaskRouteKeyRef.current;
    previousTaskRouteKeyRef.current = taskRouteKey;
    if (previousTaskRouteKey === null) return;
    const { navigation, requestStaleRouteRelease } = useTaskConversationNavigationStore.getState();
    if (navigation?.routeCommitted && !isTaskNavigationAtCurrentRoute(navigation)) {
      requestStaleRouteRelease();
    }
  }, [taskRouteKey]);
  useEffect(() => {
    if (activeSection === 'bot') void initSpaceContext();
  }, [activeSection]);
  // 视口回到桌面（≥lg）时收起一级导航抽屉，避免抽屉压住重新出现的内流侧栏。
  useEffect(() => {
    if (isDesktop || hideNavigation) setMobileNavOpen(false);
  }, [isDesktop, hideNavigation]);
  // <lg 抽屉内点导航：跳转并收起抽屉。
  const handleMobileNavigate = (path: string) => {
    Tracert.click(Points.shell.navClick, { nav_target: path });
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
    <PrimaryNavContext.Provider value={hideNavigation ? null : primaryNavApi}>
      <div className="flex h-full flex-col bg-[var(--color-bg)]">
        <OpenSourceExperienceNotice />
        <div className="flex min-h-0 flex-1">
          {!hideNavigation && (
            <AppSidebar
              activePath={location.pathname}
              collapsed={sidebarCollapsed}
              items={mergedItems}
              onNavigate={(path) => {
                Tracert.click(Points.shell.navClick, { nav_target: path });
                history.push(path);
              }}
              onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
              currentUser={currentUser}
            />
          )}
          {/* 内容列：<lg 顶条带与 main 正常流堆叠；main 以 min-h-0+flex-1 承接剩余高度，保持 h-full 消费链。 */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {/* <lg 一级导航兜底条带：仅当页头未登记内联入口时渲染（chat/group 页头改为同排行内钮后，
                此处不再占独立 48px 行，避免与页头双行堆叠）。正常流常驻占位，不悬浮压住页面控件。 */}
            {!hideNavigation && inlineNavEntries === 0 && (
              <div
                data-testid="primary-nav-fallback-strip"
                className="flex h-12 shrink-0 items-center border-b border-border bg-card px-2 lg:hidden"
              >
                <IconButton label="打开导航" icon={<Menu className="h-4 w-4" />} onClick={openNav} />
              </div>
            )}
            <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
              {children}
              <PersistentChatLayoutPanelViewport />
            </main>
          </div>
        </div>
        {/* <lg 一级导航抽屉：≥lg 内流侧栏可见；<lg 由页头内联入口或兜底条带的汉堡钮触发本抽屉。 */}
        {!hideNavigation && (
          <Drawer open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
            <DrawerContent side="left" size="sm" showClose={false} bodyClassName="p-0 flex flex-col">
              <DrawerTitle className="sr-only">主导航</DrawerTitle>
              <SidebarNavList activePath={location.pathname} items={mergedItems} onNavigate={handleMobileNavigate} />
            </DrawerContent>
          </Drawer>
        )}
      </div>
    </PrimaryNavContext.Provider>
  );
}
