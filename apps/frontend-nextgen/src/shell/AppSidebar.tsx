import { getCapabilities } from '@/capabilities';
import { NotificationBell } from '@/components/Admin/NotificationBell';
import { IconButton } from '@/components/ui';
import { PanelLeftClose, PanelLeftOpen, Search } from 'lucide-react';
import { useState } from 'react';
import { AccountBadge, type AccountUser } from './AccountBadge';
import { GlobalSearchDialog } from './GlobalSearch';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';
import { HelpMenu } from './HelpMenu';
import type { NavigationItem } from './navigation';
import { LegacyVersionSwitch } from './LegacyVersionSwitch';
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
 * 内流单侧边栏外壳（refactor-global-nav-shell）：顶部品牌区（Logo + 搜索 + 通知中心）+
 * 协作/Bot 分组导航 + 底部用户行（账号身份 + 「更多」浮层）+ 边缘折叠控制。
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
  // 全局搜索弹窗（设计稿 artboard-016）：入口固定在通知中心左侧；可见性经 capability
  // （getShellVisibility.globalSearch，collab 形态隐藏：搜索结果会深链到该形态不含的页面）。
  const [searchOpen, setSearchOpen] = useState(false);
  // 品牌语义经 capability 解析：Open Core = Avernet 横版 wordmark；internal = 现状「蓝底色块 + TeamClaw」。
  const brand = getCapabilities().getProductBrand().value;
  // 通知中心为形态级入口（getShellVisibility.notificationBell）：Open Core（阿里云部署）默认展示；
  // 隐藏形态下未读数轮询随组件不挂载自然停止，通知 service 层不改。
  const { notificationBell, globalSearch } = getCapabilities().getShellVisibility().value;
  // 产品引导（新版助手）常驻入口：capability 门控（Open Core null → 不渲染，布局零变化，
  // 与 LegacyVersionSwitch 同款模式）
  const guideAssistant = getCapabilities().getGuideAssistant();
  const GuideEntry = guideAssistant.status === 'available' ? guideAssistant.value?.Entry : undefined;

  const shell = (
    <>
      {/* 展开态品牌区（SVG 稿 artboard-016 单行 52px）：mark 锚 (12,14) 由 px-3+居中给出，图标组三钮紧贴右锚（右缘 12px）。
          单行 200px 预算（rail 宽 224 - px-3 双侧 24，品牌锁版换新版后由 176 加宽）= 图标组 96px + compact 锁版
          ≤104px：锁版宽度由各形态自管（Open Core 方版 mark 24px；internal mark 图 + 14px 字标锁版自缩至 ~94px），
          行结构不感知形态差异。前科：锁版超预算同排曾致文字被压盖，各形态改 compact 锁版时必须按此预算自检。 */}
      {/* 折叠态（2026-11 收起态规范）：顶部居中展示品牌 mark（与展开态同源 brand.Logo，行高同为 52px
          保持折叠/展开切换时顶部高度稳定）。rail 宽 56px 装不下 compact 锁版（mark+字标），传 markOnly
          仅渲染图形 mark——带字标锁版的形态若忽略该语义，字标会在窄容器被 truncate 成省略号残迹。 */}
      {collapsed ? (
        <>
          <div className="flex h-[52px] shrink-0 items-center justify-center px-3">
            <brand.Logo compact markOnly />
          </div>
          <div className="flex flex-col items-center gap-1 px-2 pb-2">
            {onToggleCollapsed && (
              <IconButton
                size="sm"
                className="text-muted-foreground"
                label="展开导航"
                icon={<PanelLeftOpen className="h-4 w-4" />}
                onClick={onToggleCollapsed}
              />
            )}
            {globalSearch && (
            <IconButton
              size="sm"
              className="text-muted-foreground"
              label="搜索"
              icon={<Search className="h-4 w-4" />}
              onClick={() => {
                Tracert.click(Points.shell.globalSearchOpen);
                setSearchOpen(true);
              }}
            />
            )}
            {notificationBell && <NotificationBell size="sm" className="text-muted-foreground" />}
          </div>
          {/* 分割线（收起态专属）：三钮簇与其下方导航图标之间的发丝线分界 */}
          <div aria-hidden className="mx-3 h-px shrink-0 bg-divider" />
        </>
      ) : (
        <div className="flex h-[52px] items-center px-3">
          <brand.Logo compact />
          <span className="ml-auto flex shrink-0 items-center gap-0">
            {globalSearch && (
              <IconButton label="搜索" icon={<Search className="h-4 w-4" />} onClick={() => {
                Tracert.click(Points.shell.globalSearchOpen);
                setSearchOpen(true);
              }} />
            )}
            {notificationBell && <NotificationBell />}
            {onToggleCollapsed && (
              <IconButton label="折叠导航" icon={<PanelLeftClose className="h-4 w-4" />} onClick={onToggleCollapsed} />
            )}
          </span>
        </div>
      )}

      {globalSearch ? <GlobalSearchDialog open={searchOpen} onOpenChange={setSearchOpen} /> : null}

      <SidebarNavList activePath={activePath} items={items} onNavigate={onNavigate} collapsed={collapsed} />

      {/* 底部用户行（SVG 稿）：32px 圆头像 + 14px 姓名 #09090B + 右侧「更多」浮层，
          行与导航区之间无分隔线（稿为纯留白过渡）。
          「返回旧版」常驻入口（灰度切流，capability 门控）置用户行上方：Open Core
          （无旧版形态）判定 capability null，组件渲染 null，布局零变化。 */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-1 bg-background/70 px-2 pb-3 pt-2">
          <LegacyVersionSwitch collapsed />
          <AccountBadge currentUser={currentUser} collapsed />
          {GuideEntry ? <GuideEntry /> : null}
          <HelpMenu variant="more" />
        </div>
      ) : (
        <div className="mt-auto space-y-0.5 bg-background/70 px-2 pb-3 pt-2">
          <LegacyVersionSwitch />
          <div className="flex items-center gap-1">
            <AccountBadge currentUser={currentUser} />
            <span className="ml-auto flex shrink-0 items-center">
              {GuideEntry ? <GuideEntry /> : null}
              <HelpMenu variant="more" />
            </span>
          </div>
        </div>
      )}
    </>
  );

  if (collapsed)
    return (
      // 右缘分割线按稿走 0.5px 发丝线 + 浅色（#EEE ≈ border-border/60），与 Bot 列表右缘 1px 分割线拉开层级
      <aside className="relative hidden w-14 shrink-0 flex-col border-r border-divider bg-background py-1 lg:flex">
        {shell}
      </aside>
    );

  return (
    <aside className="relative hidden w-[var(--shell-sidebar-width)] shrink-0 flex-col border-r border-divider bg-background py-1 lg:flex">
      {shell}
    </aside>
  );
}
