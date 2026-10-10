import { getCapabilities } from '@/capabilities';
import { Badge, Button, IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { navSectionLabels, type NavigationItem, type NavigationSection } from './navigation';
import { SpaceSwitcher } from './SpaceSwitcher';

interface SidebarNavListProps {
  activePath: string;
  items: NavigationItem[];
  onNavigate: (path: string) => void;
  /** true=仅图标列（≥lg 折叠态）；false=分组完整列表（≥lg 展开态与 <lg 抽屉）。 */
  collapsed?: boolean;
}

const SECTION_ORDER: NavigationSection[] = ['collab', 'bot', 'legacy'];

/** deprecated 项的可读名称（IconButton 内建 tooltip 同步携带「待移除」语义）。 */
function navAriaLabel(item: NavigationItem) {
  return item.deprecated ? `${item.label}（待移除）` : item.label;
}

/**
 * 一级导航内容本体（不含 `<aside>` 外壳）：协作 / Bot 双固定分组 + 老功能过渡组常驻，
 * 不随路由整体换组（refactor-global-nav-shell）。由 AppSidebar 的内流外壳与 <lg 抽屉复用，保证两处一致。
 */
export function SidebarNavList({ activePath, items, onNavigate, collapsed = false }: SidebarNavListProps) {
  // 空间切换器为形态级入口（getShellVisibility.spaceSwitcher）：Open Core（阿里云部署）默认隐藏。
  // 隐藏 UI 不影响空间数据链路：initSpaceContext 由 AppShell 按 Bot 分组路由触发，与开关无关。
  const showSpaceSwitcher = getCapabilities().getShellVisibility().value.spaceSwitcher;
  const sections = SECTION_ORDER.filter((section) => items.some((item) => item.section === section));

  if (collapsed) {
    return (
      <div className="app-scrollbar flex w-full flex-1 flex-col items-center overflow-y-auto px-1 py-2">
        {sections.map((section, index) => {
          const sectionItems = items.filter((item) => item.section === section);
          return (
            <div key={section} className="flex w-full flex-col items-center gap-2">
              {index > 0 && <div aria-hidden className="my-1 w-8" />}
              {/* 空间切换（icon 形态）位于 Bot 分组之上 */}
              {section === 'bot' && showSpaceSwitcher && <SpaceSwitcher compact collapsed />}
              {sectionItems.map((item) => {
                const Icon = item.icon;
                const active = activePath.startsWith(item.path);
                return (
                  <IconButton
                    key={item.id}
                    ariaLabel={navAriaLabel(item)}
                    label={navAriaLabel(item)}
                    className={cn(active && 'bg-selected text-foreground')}
                    icon={<Icon className="h-4 w-4" />}
                    onClick={() => onNavigate(item.path)}
                  />
                );
              })}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <nav aria-label="主导航" className="app-scrollbar flex-1 overflow-y-auto px-2 py-2">
      {sections.map((section, index) => {
        const sectionItems = items.filter((item) => item.section === section);
        return (
          <section key={section} aria-label={navSectionLabels[section]} className={index > 0 ? 'mt-4' : 'mb-1'}>
            {/* 分组标题（SVG + dmore DOM 实测：行 h=32、与项行同为 32px 高）：x=16 与图标列对齐、
                #868686 12px 常规字重；分组间无分隔线（SVG 稿纯留白过渡，B1 的 border-t 分隔线已按稿撤除）。 */}
            <div className="flex min-h-8 items-center justify-between gap-2 px-4 pb-0.5 pt-0.5">
              <p className="text-xs font-normal text-content-soft">{navSectionLabels[section]}</p>
              {section === 'bot' && showSpaceSwitcher && <SpaceSwitcher compact />}
            </div>
            <div className="flex flex-col gap-0.5">
              {sectionItems.map((item) => {
                const Icon = item.icon;
                const active = activePath.startsWith(item.path);
                return (
                  <Button
                    key={item.id}
                    variant="ghost"
                    className={cn(
                      // SVG 实测：药囊 h32 rx6 inset-x8（34px 行距=32+gap2）、icon 16 @x16、
                      // 文案 14px @x40；未选 #525252 文案 + #71717A 图标、选中 #EBEBEB 胶囊 + #09090B。
                      'h-8 w-full justify-start gap-2 rounded-md px-2 text-sm font-normal',
                      'text-content-strong hover:bg-muted/60 [&_svg]:text-muted-foreground',
                      active && 'bg-selected text-foreground hover:bg-selected [&_svg]:text-foreground',
                    )}
                    leftIcon={<Icon className="h-4 w-4" />}
                    rightIcon={
                      item.deprecated ? (
                        <Badge tone="neutral" className="ml-auto shrink-0">
                          待移除
                        </Badge>
                      ) : undefined
                    }
                    onClick={() => onNavigate(item.path)}
                  >
                    {item.label}
                  </Button>
                );
              })}
            </div>
          </section>
        );
      })}
    </nav>
  );
}
