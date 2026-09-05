import { getCapabilities } from '@/capabilities';
import { Button, IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { NavigationArea, NavigationItem } from './navigation';
import { SpaceSwitcher } from './SpaceSwitcher';
import { WorkspaceIdentitySwitcher } from './WorkspaceIdentitySwitcher';

interface SidebarNavListProps {
  area: NavigationArea;
  activePath: string;
  items: NavigationItem[];
  onNavigate: (path: string) => void;
  /** true=仅图标列（用于 ≥lg 的折叠态）；false=带标题/文案的完整列表（用于 ≥lg 展开态与 <lg 抽屉）。 */
  collapsed?: boolean;
}

/** 一级导航的内容本体（不含 `<aside>` 外壳）。由 AppSidebar 的内流外壳与 <lg 抽屉复用，保证两处一致。 */
export function SidebarNavList({ area, activePath, items, onNavigate, collapsed = false }: SidebarNavListProps) {
  const areaItems = items.filter((item) => item.area === area);
  // 空间切换器为形态级入口（getShellVisibility.spaceSwitcher）：Open Core（阿里云部署）默认隐藏。
  // 隐藏 UI 不影响空间数据链路：initSpaceContext 由 AppShell 进入管理区域触发，与开关无关。
  const showSpaceSwitcher = area === 'manage' && getCapabilities().getShellVisibility().value.spaceSwitcher;

  if (collapsed) {
    return (
      <div className="flex w-full flex-col items-center gap-2 pt-2">
        {areaItems.map((item) => {
          const Icon = item.icon;
          const active = activePath.startsWith(item.path);
          return (
            <IconButton
              key={item.id}
              label={item.label}
              className={cn(active && 'bg-[var(--color-primary-soft)] text-[var(--color-primary)]')}
              icon={<Icon className="h-4 w-4" />}
              onClick={() => onNavigate(item.path)}
            />
          );
        })}
      </div>
    );
  }

  return (
    <>
      <nav
        aria-label={area === 'work' ? '工作导航' : '管理导航'}
        className="app-scrollbar flex-1 overflow-y-auto px-3 py-3"
      >
        {(area === 'work' || showSpaceSwitcher) && (
          <div className="mb-3 border-b border-[var(--color-border)] pb-3">
            {area === 'work' ? <WorkspaceIdentitySwitcher /> : <SpaceSwitcher />}
          </div>
        )}
        <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-muted)]">
          {area === 'work' ? '工作' : '管理'}
        </p>
        <div className="space-y-1">
          {areaItems.map((item) => {
            const Icon = item.icon;
            const active = activePath.startsWith(item.path);
            return (
              <Button
                key={item.id}
                variant="ghost"
                className={cn(
                  'h-11 w-full justify-start px-3 text-sm',
                  active &&
                    'bg-[var(--color-primary-soft)] text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)]',
                )}
                leftIcon={<Icon className="h-[18px] w-[18px]" />}
                onClick={() => onNavigate(item.path)}
              >
                {item.label}
              </Button>
            );
          })}
        </div>
      </nav>
    </>
  );
}
