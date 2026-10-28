import { getCapabilities } from '@/capabilities';
import type { LucideIcon } from 'lucide-react';
import { Bot, Compass, FlaskConical, MessagesSquare, Users, Zap } from 'lucide-react';
import { getRouteMeta, type RouteSection } from './routeMeta';

/** 产品侧栏只展示协作与 Bot 两个固定分组。 */
export type NavigationSection = Extract<RouteSection, 'collab' | 'bot'>;

/** 产品侧栏分组展示名。 */
export const navSectionLabels: Record<NavigationSection, string> = {
  collab: '协作',
  bot: 'Bot',
};

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  icon: LucideIcon;
  section: NavigationSection;
  description: string;
}

// Open Core 基线一级导航项。内部专属入口已剥离（capability 注入），
// 由 internal overlay 经 capability `getInternalNavigationItems` 注入，
// 字面量与图标实例随内部 overlay（.internal-paths）物理剥离，不进 Open Core 产物
// （open-core-export-plan §5.2 / §5.6「导航中的内部入口」必须按开源模式分隔）。
//
// 归组映射（refactor-global-nav-shell design.md 决议表）：
// - 同路由就地改名归组 = 迁移即完成，不打 deprecated（bot-workshop→Bot管理、collaboration-square→发现）；
// - 无继任位存量项不展示在产品侧栏；存量页面仅保留直接 URL 访问能力；
// - 【管理后台】项已由 split-admin-space-ticket-pages 退役（拆分至 /space-admin、/ticket-center
//   独立路由，入口为空间切换弹层设置 icon / 通知弹层「查看全部」，不占导航位）。
// - workspace-conversation-navigation-refactor：原「对话协作」（/workspace）拆分为
//   对话（/workspace/chat）与 协作群（/workspace/collaboration）两个一级导航项；
//   /workspace 仅重定向到 /workspace/chat，不再占导航位。
export const navigationItems: NavigationItem[] = [
  {
    id: 'conversation',
    label: '对话',
    path: '/workspace/chat',
    icon: MessagesSquare,
    section: 'collab',
    description: '与 Bot 即时对话',
  },
  {
    id: 'collaboration',
    label: '协作群',
    path: '/workspace/collaboration',
    icon: Users,
    section: 'collab',
    description: '与用户和 Bot 即时协作',
  },
  {
    id: 'collaboration-square',
    label: '发现',
    path: '/collaboration-square',
    icon: Compass,
    section: 'collab',
    description: '发现专家模板、公开 Bot 与协作群',
  },
  {
    id: 'lab',
    label: '实验室',
    path: '/lab',
    icon: FlaskConical,
    section: 'collab',
    description: '实验性能力空间，承载社区与任务',
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
    id: 'evolution-factory',
    label: '智能进化',
    path: '/evolution-factory',
    icon: Zap,
    section: 'bot',
    description: '诊断、评估和优化 Bot',
  },
];

/**
 * 合并 Open Core 基线 navigationItems 与 internal overlay 注入的额外导航项。
 * 合并策略：internal 项按自身 `section` 追加到同名分组末尾（internal overlay 的能力工坊/能力市场
 * 携带 `section: 'bot'`，落 Bot 分组），flat 序恒为 collab → bot。
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
  };
  if (internal.status === 'available' && internal.value.length) {
    // 未知 section 剔除（防御性收口：契约变更期的录入错误不进侧栏）。
    for (const item of internal.value) {
      if (grouped[item.section]) grouped[item.section].push(item);
    }
  }
  // Evolution stays last in the Bot group, including after internal overlays.
  const evolution = grouped.bot.findIndex((item) => item.id === 'evolution-factory');
  if (evolution >= 0) grouped.bot.push(...grouped.bot.splice(evolution, 1));
  return [...grouped.collab, ...grouped.bot];
}

export function getNavigationItem(pathname: string) {
  const meta = getRouteMeta(pathname);
  const merged = getMergedNavigationItems();
  // 带 navKey 的路由按 meta 命中导航项；无导航位的路由（/space-admin、/ticket-center）
  // 侧栏不高亮任何菜单项，落 prefix 兜底匹配（同样不会命中）。
  if (meta?.navKey && (meta.section === 'collab' || meta.section === 'bot')) {
    return merged.find((item) => item.id === meta.navKey);
  }

  return [...merged]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
}
