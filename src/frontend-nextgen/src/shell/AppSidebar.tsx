import { IconButton } from '@/components/ui';
import { PanelLeftOpen } from 'lucide-react';
import type { NavigationArea, NavigationItem } from './navigation';
import { SidebarNavList } from './SidebarNavList';

interface AppSidebarProps {
  area: NavigationArea;
  activePath: string;
  collapsed: boolean;
  items: NavigationItem[];
  onNavigate: (path: string) => void;
  onExpand: () => void;
}

/** 内流一级导航外壳。≥lg 始终在流内（折叠态=图标列，展开态=完整列表）；<lg 由 AppShell 改用抽屉呈现，本组件 hidden。 */
export function AppSidebar({ area, activePath, collapsed, items, onNavigate, onExpand }: AppSidebarProps) {
  if (collapsed)
    return (
      <aside className="hidden w-14 shrink-0 flex-col items-center border-r border-[var(--color-border)] bg-white py-3 lg:flex">
        <IconButton label="展开导航" icon={<PanelLeftOpen className="h-4 w-4" />} onClick={onExpand} />
        <SidebarNavList area={area} activePath={activePath} items={items} onNavigate={onNavigate} collapsed />
      </aside>
    );

  return (
    <aside className="hidden w-[var(--shell-sidebar-width)] shrink-0 flex-col border-r border-[var(--color-border)] bg-white lg:flex">
      <SidebarNavList area={area} activePath={activePath} items={items} onNavigate={onNavigate} />
    </aside>
  );
}
