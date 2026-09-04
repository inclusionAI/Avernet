import { getCapabilities } from '@/capabilities';
import type { LucideIcon } from 'lucide-react';
import { Bot, Compass, LayoutDashboard, ListTodo, MessagesSquare, ShieldCheck } from 'lucide-react';
import { getRouteMeta, type RouteSection } from './routeMeta';

export type NavigationArea = Extract<RouteSection, 'work' | 'manage'>;

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  icon: LucideIcon;
  area: NavigationArea;
  description: string;
}

// Open Core 基线一级导航项。内部专属入口已剥离（capability 注入），
// 由 internal overlay 经 capability `getInternalNavigationItems` 注入，
// 字面量与图标实例随内部 overlay（.internal-paths）物理剥离，不进 Open Core 产物
// （open-core-export-plan §5.2 / §5.6「导航中的内部入口」必须按开源模式分隔）。
export const navigationItems: NavigationItem[] = [
  {
    id: 'workspace',
    label: '对话协作',
    path: '/workspace',
    icon: MessagesSquare,
    area: 'work',
    description: '与用户和 Bot 即时协作',
  },
  {
    id: 'my-task',
    label: '我的任务',
    path: '/work/my-task',
    icon: ListTodo,
    area: 'work',
    description: '查看用户任务与定时任务两个 Tab',
  },
  {
    id: 'collaboration-square',
    label: '协作广场',
    path: '/collaboration-square',
    icon: Compass,
    area: 'work',
    description: '发现公开 Bot、协作群与任务',
  },
  {
    id: 'collaboration-privacy',
    label: '协作权限',
    path: '/collaboration-privacy',
    icon: ShieldCheck,
    area: 'work',
    description: '管理协作关系与申请策略',
  },
  {
    id: 'bot-workshop',
    label: 'Bot 工坊',
    path: '/bot-workshop',
    icon: Bot,
    area: 'manage',
    description: '创建、配置和发布 Bot',
  },
  {
    id: 'admin',
    label: '管理后台',
    path: '/admin',
    icon: LayoutDashboard,
    area: 'manage',
    description: '空间、工单和通知管理',
  },
];

/**
 * 合并 Open Core 基线 navigationItems 与 internal overlay 注入的额外导航项。
 * 合并策略：在「基线 manage 分区末项」之前 splice 插入内部项，
 * 保持原版菜单业务序「bot-workshop → 能力工坊 → 能力市场 → 管理后台」。
 * Open Core 形态下 capability 返回 []，结果 = Open Core 基线项。
 *
 * 形态级入口收敛：getShellVisibility().adminEntry=false 时从合并结果剔除【管理后台】项。
 * Open Core（阿里云部署）默认 adminEntry=true——管理后台项展示；过滤单点仍按此开关收敛（预留未来形态），
 * 基线 navigationItems 数组与 routeMeta 字面量保留不删（/admin 直访不再重定向，管理后台已开放）。
 * 见 openspec open-core-shell-visibility-capability / avernet-admin-notification-reveal。
 */
export function getMergedNavigationItems(): NavigationItem[] {
  const internal = getCapabilities().getInternalNavigationItems();
  let merged = navigationItems;
  if (internal.status === 'available' && internal.value.length) {
    // 自尾向前找基线 manage 分区末项作为 anchor 位，internal 项插在其前。
    let insertAt = navigationItems.length;
    for (let i = navigationItems.length - 1; i >= 0; i--) {
      if (navigationItems[i].area === 'manage') {
        insertAt = i;
        break;
      }
    }
    merged = [...navigationItems.slice(0, insertAt), ...internal.value, ...navigationItems.slice(insertAt)];
  }
  const { adminEntry } = getCapabilities().getShellVisibility().value;
  return adminEntry ? merged : merged.filter((item) => item.id !== 'admin');
}

export function getNavigationItem(pathname: string) {
  const meta = getRouteMeta(pathname);
  const merged = getMergedNavigationItems();
  if (meta?.section === 'work' || meta?.section === 'manage') {
    return merged.find((item) => item.id === meta.navKey);
  }

  return [...merged]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
}
