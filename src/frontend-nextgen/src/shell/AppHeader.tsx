import { NotificationBell } from '@/components/Admin/NotificationBell';
import { Button, IconButton } from '@/components/ui';
import { AppWindow, Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { AccountBadge, type AccountUser } from './AccountBadge';
import { HelpMenu } from './HelpMenu';
import type { NavigationArea } from './navigation';

interface AppHeaderProps {
  area: NavigationArea;
  sidebarCollapsed: boolean;
  onAreaChange: (area: NavigationArea) => void;
  onToggleSidebar: () => void;
  /** <lg 打开一级导航抽屉。≥lg 不渲染（内流侧栏可见）。 */
  onOpenMobileNav: () => void;
  currentUser?: AccountUser | null;
}

export function AppHeader({
  area,
  sidebarCollapsed,
  onAreaChange,
  onToggleSidebar,
  onOpenMobileNav,
  currentUser,
}: AppHeaderProps) {
  return (
    <header className="relative z-40 flex h-[var(--shell-header-height)] items-center border-b border-[var(--color-border)] bg-white px-4">
      <div className="flex min-w-[200px] items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-primary)] text-white">
          <AppWindow className="h-5 w-5" aria-hidden />
        </div>
        <strong className="text-lg tracking-tight">TeamClaw</strong>
        <IconButton
          className="ml-2 hidden lg:inline-flex"
          label={sidebarCollapsed ? '展开导航' : '折叠导航'}
          icon={sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          onClick={onToggleSidebar}
        />
        {/* <lg 一级导航入口：≥lg 隐藏(内流侧栏可见),<lg 点击打开抽屉。 */}
        <IconButton
          className="ml-2 lg:hidden"
          label="打开导航"
          icon={<Menu className="h-4 w-4" />}
          onClick={onOpenMobileNav}
        />
      </div>
      <nav aria-label="产品区域" className="ml-2 flex items-center rounded-lg bg-[var(--color-panel-strong)] p-0.5">
        <Button
          size="sm"
          variant="ghost"
          className={area === 'work' ? 'bg-white text-[var(--color-primary)] shadow-sm hover:bg-white' : ''}
          onClick={() => onAreaChange('work')}
        >
          工作
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className={area === 'manage' ? 'bg-white text-[var(--color-primary)] shadow-sm hover:bg-white' : ''}
          onClick={() => onAreaChange('manage')}
        >
          管理
        </Button>
      </nav>
      <div className="ml-auto flex items-center gap-1">
        <HelpMenu />
        <NotificationBell />
        {/* 右上角账号身份栏（迁移自左下角） */}
        <AccountBadge currentUser={currentUser} />
      </div>
    </header>
  );
}
