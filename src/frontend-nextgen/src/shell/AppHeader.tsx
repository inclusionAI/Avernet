import { getCapabilities } from '@/capabilities';
import { NotificationBell } from '@/components/Admin/NotificationBell';
import { Button, IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
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
  // 品牌语义经 capability 解析：Open Core = Avernet 横版 wordmark（capabilities/brandLogos.tsx）；
  // internal overlay = 现状「蓝底色块 + AppWindow + TeamClaw」组合（src/internal/brand，随其剥离）。
  const brand = getCapabilities().getProductBrand().value;
  // 通知中心为形态级入口（getShellVisibility.notificationBell）：Open Core（阿里云部署）默认展示，
  // internal overlay 同样 true（与改造前一致）；隐藏形态下未读数轮询随组件不挂载自然停止，通知 service 层不改。
  const { notificationBell } = getCapabilities().getShellVisibility().value;
  return (
    <header className="relative z-40 flex h-[var(--shell-header-height)] items-center border-b border-[var(--color-border)] bg-white px-4">
      <div className="flex min-w-[200px] items-center gap-2">
        <brand.Logo className="h-9" />
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
      <nav aria-label="产品区域" className="ml-2 flex items-center rounded-md bg-[var(--color-panel-strong)] p-[3px]">
        <Button
          size="sm"
          variant="ghost"
          className={cn(
            'h-[30px] rounded px-3 text-sm hover:text-primary hover:font-medium',
            area === 'work' ? 'bg-background text-primary shadow-sm hover:bg-background' : 'text-muted-foreground',
          )}
          onClick={() => onAreaChange('work')}
        >
          工作
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className={cn(
            'h-[30px] rounded px-3 text-sm hover:text-primary hover:font-medium',
            area === 'manage' ? 'bg-background text-primary shadow-sm hover:bg-background' : 'text-muted-foreground',
          )}
          onClick={() => onAreaChange('manage')}
        >
          管理
        </Button>
      </nav>
      <div className="ml-auto flex items-center gap-1">
        <HelpMenu />
        {notificationBell && <NotificationBell />}
        {/* 右上角账号身份栏（迁移自左下角） */}
        <AccountBadge currentUser={currentUser} />
      </div>
    </header>
  );
}
