import type { LucideIcon } from 'lucide-react';
import { Bot, Boxes, Compass, LayoutDashboard, ListTodo, MessagesSquare, ShieldCheck, Sparkles } from 'lucide-react';
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
    description: '发现公开 Bot 与协作群',
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
    id: 'capability-workshop',
    label: '能力工坊',
    path: '/capability-workshop',
    icon: Sparkles,
    area: 'manage',
    description: '管理 Skill 与 MCP',
  },
  {
    id: 'market',
    label: '能力市场',
    path: '/market',
    icon: Boxes,
    area: 'manage',
    description: '发现和添加通用能力',
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

export function getNavigationItem(pathname: string) {
  const meta = getRouteMeta(pathname);
  if (meta?.section === 'work' || meta?.section === 'manage') {
    return navigationItems.find((item) => item.id === meta.navKey);
  }

  return [...navigationItems]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
}
