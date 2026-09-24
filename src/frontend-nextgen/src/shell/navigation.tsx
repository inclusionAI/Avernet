import { getCapabilities } from '@/capabilities';
import type { LucideIcon } from 'lucide-react';
import { Bot, Compass, ListTodo, MessagesSquare, ShieldCheck } from 'lucide-react';
import { getRouteMeta, type RouteSection } from './routeMeta';

/** 侧栏导航分组成员：协作 / Bot 双固定分组 + legacy 过渡分组（无继任位存量项，待移除）。 */
export type NavigationSection = Extract<RouteSection, 'collab' | 'bot' | 'legacy'>;

/** 分组展示名（「老功能」为过渡分组，legacy 项清零后随 deprecated 机制一并退役）。 */
export const navSectionLabels: Record<NavigationSection, string> = {
  collab: '协作',
  bot: 'Bot',
  legacy: '老功能',
};

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  icon: LucideIcon;
  section: NavigationSection;
  description: string;
  /** 过渡标记：在新 IA 中无继任位的存量项为 true（侧栏带「待移除」徽标），迁移完成后由功能负责人摘除。 */
  deprecated?: boolean;
}

// Open Core 基线一级导航项。内部专属入口已剥离（capability 注入），
// 由 internal overlay 经 capability `getInternalNavigationItems` 注入，
// 字面量与图标实例随内部 overlay（.internal-paths）物理剥离，不进 Open Core 产物
// （open-core-export-plan §5.2 / §5.6「导航中的内部入口」必须按开源模式分隔）。
//
// 归组映射（refactor-global-nav-shell design.md 决议表）：
// - 同路由就地改名归组 = 迁移即完成，不打 deprecated（bot-workshop→Bot管理、collaboration-square→发现）；
// - 无继任位存量项（my-task / collaboration-privacy）沉 legacy 组带 deprecated，由各功能负责人逐项摘除；
// - 【管理后台】项已由 split-admin-space-ticket-pages 退役（拆分至 /space-admin、/ticket-center
//   独立路由，入口为空间切换弹层设置 icon / 通知弹层「查看全部」，不占导航位）。
export const navigationItems: NavigationItem[] = [
  {
    id: 'workspace',
    label: '对话协作',
    path: '/workspace',
    icon: MessagesSquare,
    section: 'collab',
    description: '与用户和 Bot 即时协作',
  },
  {
    id: 'collaboration-square',
    label: '发现',
    path: '/collaboration-square',
    icon: Compass,
    section: 'collab',
    description: '发现公开 Bot、协作群与任务',
  },
  {
    id: 'bot-workshop',
    label: 'Bot管理',
    path: '/bot-workshop',
    icon: Bot,
    section: 'bot',
    description: '创建、配置和发布 Bot',
  },
  {
    id: 'my-task',
    label: '任务列表',
    path: '/work/my-task',
    icon: ListTodo,
    section: 'legacy',
    description: '查看用户任务与定时任务两个 Tab',
    deprecated: true,
  },
  {
    id: 'collaboration-privacy',
    label: '协作权限',
    path: '/collaboration-privacy',
    icon: ShieldCheck,
    section: 'legacy',
    description: '管理协作关系与申请策略',
    deprecated: true,
  },
];

/**
 * 合并 Open Core 基线 navigationItems 与 internal overlay 注入的额外导航项。
 * 合并策略：internal 项按自身 `section` 追加到同名分组末尾（internal overlay 的能力工坊/能力市场
 * 携带 `section: 'bot'`，落 Bot 分组），flat 序恒为 collab → bot → legacy。
 * Open Core 形态下 capability 返回 []，结果 = Open Core 基线项。
 *
 * split-admin-space-ticket-pages：【管理后台】基线项退役，getShellVisibility().adminEntry 形态
 * 过滤随之移除（getShellVisibility 剩 spaceSwitcher / notificationBell；管理域形态开关归 getAdminSections）。
 * 见 openspec refactor-global-nav-shell / split-admin-space-ticket-pages。
 */
export function getMergedNavigationItems(): NavigationItem[] {
  const internal = getCapabilities().getInternalNavigationItems();
  const grouped: Record<NavigationSection, NavigationItem[]> = {
    collab: navigationItems.filter((item) => item.section === 'collab'),
    bot: navigationItems.filter((item) => item.section === 'bot'),
    legacy: navigationItems.filter((item) => item.section === 'legacy'),
  };
  if (internal.status === 'available' && internal.value.length) {
    // 未知 section 剔除（防御性收口：契约变更期的录入错误不进侧栏）。
    for (const item of internal.value) {
      if (grouped[item.section]) grouped[item.section].push(item);
    }
  }
  return [...grouped.collab, ...grouped.bot, ...grouped.legacy];
}

export function getNavigationItem(pathname: string) {
  const meta = getRouteMeta(pathname);
  const merged = getMergedNavigationItems();
  // 带 navKey 的路由按 meta 命中导航项；无导航位的路由（/space-admin、/ticket-center）
  // 侧栏不高亮任何菜单项，落 prefix 兜底匹配（同样不会命中）。
  if (meta?.navKey && (meta.section === 'collab' || meta.section === 'bot' || meta.section === 'legacy')) {
    return merged.find((item) => item.id === meta.navKey);
  }

  return [...merged]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
}
