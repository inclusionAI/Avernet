import { getCapabilities } from '@/capabilities';
import { NotificationBell } from '@/components/Admin/NotificationBell';
import { IconButton } from '@/components/ui';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { AccountBadge, type AccountUser } from './AccountBadge';
import { HelpMenu } from './HelpMenu';
import type { NavigationItem } from './navigation';
import { SidebarNavList } from './SidebarNavList';

interface AppSidebarProps {
  activePath: string;
  collapsed: boolean;
  items: NavigationItem[];
  onNavigate: (path: string) => void;
  /** 折叠/展开边缘控制回调（≥lg 内流外壳）；<lg 抽屉复用 SidebarNavList 时不传。 */
  onToggleCollapsed?: () => void;
  currentUser?: AccountUser | null;
}

/**
 * 内流单侧边栏外壳（refactor-global-nav-shell）：顶部品牌区（Logo + 通知中心）+
 * 协作/Bot/老功能分组导航 + 底部用户行（账号身份 + 「更多」浮层）+ 边缘折叠控制。
 * 顶栏（AppHeader）已退役，原顶栏功能全部归位至本组件（功能行为零损失）。
 * ≥lg 始终在流内（折叠态=图标列，展开态=完整列表）；<lg 由 AppShell 改用抽屉呈现，本组件 hidden。
 */
export function AppSidebar({
  activePath,
  collapsed,
  items,
  onNavigate,
  onToggleCollapsed,
  currentUser,
}: AppSidebarProps) {
  // 品牌语义经 capability 解析：Open Core = Avernet 横版 wordmark；internal = 现状「蓝底色块 + TeamClaw」。
  const brand = getCapabilities().getProductBrand().value;
  // 通知中心为形态级入口（getShellVisibility.notificationBell）：Open Core（阿里云部署）默认展示；
  // 隐藏形态下未读数轮询随组件不挂载自然停止，通知 service 层不改。
  const { notificationBell } = getCapabilities().getShellVisibility().value;

  const shell = (
    <>
      {/* 品牌区（SVG 稿）：行高 52px；左 Logo mark 紧凑锁版（12,15 24×22）、右上一排功能图标（通知 + 折叠控制）32px 居中。 */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-1 px-2 pb-1 pt-3.5">
          {notificationBell && <NotificationBell />}
          {onToggleCollapsed && (
            <IconButton
              label={collapsed ? '展开导航' : '折叠导航'}
              icon={collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
              onClick={onToggleCollapsed}
            />
          )}
        </div>
      ) : (
        <div className="flex h-[52px] items-center gap-2 px-3">
          <brand.Logo compact className="min-w-0" />
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {notificationBell && <NotificationBell />}
            {onToggleCollapsed && (
              <IconButton label="折叠导航" icon={<PanelLeftClose className="h-4 w-4" />} onClick={onToggleCollapsed} />
            )}
          </span>
        </div>
      )}

      <SidebarNavList activePath={activePath} items={items} onNavigate={onNavigate} collapsed={collapsed} />

      {/* 底部用户行（SVG 稿）：32px 圆头像 + 14px 姓名 #09090B + 右侧「更多」浮层，
          行与导航区之间无分隔线（稿为纯留白过渡）。 */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-1 bg-background/70 px-2 pb-3 pt-2">
          <AccountBadge currentUser={currentUser} collapsed />
          <HelpMenu variant="more" />
        </div>
      ) : (
        <div className="flex items-center gap-1 bg-background/70 px-2 pb-3 pt-2">
          <AccountBadge currentUser={currentUser} />
          <span className="ml-auto flex shrink-0 items-center">
            <HelpMenu variant="more" />
          </span>
        </div>
      )}
    </>
  );

  if (collapsed)
    return (
      <aside className="relative hidden w-14 shrink-0 flex-col border-r border-border bg-background py-1 lg:flex">
        {shell}
      </aside>
    );

  return (
    <aside className="relative hidden w-[var(--shell-sidebar-width)] shrink-0 flex-col border-r border-border bg-background py-1 lg:flex">
      {shell}
    </aside>
  );
}
