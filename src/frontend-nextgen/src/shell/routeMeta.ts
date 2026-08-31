export type RouteSection = 'work' | 'manage' | 'dev';

export interface RouteMeta {
  path: string;
  title: string;
  section: RouteSection;
  navKey: string;
  openCore: boolean;
}

export const routeMetaList: RouteMeta[] = [
  {
    path: '/workspace',
    title: '对话协作',
    section: 'work',
    navKey: 'workspace',
    openCore: true,
  },
  {
    path: '/work/my-task',
    title: '我的任务',
    section: 'work',
    navKey: 'my-task',
    openCore: true,
  },
  {
    path: '/collaboration-square',
    title: '协作广场',
    section: 'work',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-square/bots',
    title: '协作广场 Bot',
    section: 'work',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-square/groups',
    title: '协作广场群组',
    section: 'work',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-privacy',
    title: '协作权限',
    section: 'work',
    navKey: 'collaboration-privacy',
    openCore: true,
  },
  {
    path: '/bot-workshop',
    title: 'Bot 工坊',
    section: 'manage',
    navKey: 'bot-workshop',
    openCore: true,
  },
  {
    path: '/bot-workshop/detail',
    title: 'Bot 详情',
    section: 'manage',
    navKey: 'bot-workshop',
    openCore: true,
  },
  {
    path: '/bot-workshop/logs',
    title: 'Bot 日志',
    section: 'manage',
    navKey: 'bot-workshop',
    openCore: true,
  },
  {
    path: '/capability-workshop',
    title: '能力工坊',
    section: 'manage',
    navKey: 'capability-workshop',
    openCore: false,
  },
  {
    path: '/capability-workshop/skill',
    title: 'Skill 工坊',
    section: 'manage',
    navKey: 'capability-workshop',
    openCore: false,
  },
  {
    path: '/capability-workshop/skill/detail',
    title: 'Skill 详情',
    section: 'manage',
    navKey: 'capability-workshop',
    openCore: false,
  },
  {
    path: '/capability-workshop/card',
    title: '卡片工坊',
    section: 'manage',
    navKey: 'capability-workshop',
    openCore: false,
  },
  {
    path: '/market',
    title: '能力市场',
    section: 'manage',
    navKey: 'market',
    openCore: false,
  },
  {
    path: '/market/skill',
    title: 'Skill 市场',
    section: 'manage',
    navKey: 'market',
    openCore: false,
  },
  {
    path: '/market/mcp',
    title: 'MCP 市场',
    section: 'manage',
    navKey: 'market',
    openCore: false,
  },
  {
    path: '/admin',
    title: '管理后台',
    section: 'manage',
    navKey: 'admin',
    openCore: true,
  },
  {
    path: '/admin/spaces',
    title: '空间管理',
    section: 'manage',
    navKey: 'admin',
    openCore: true,
  },
  {
    path: '/admin/work-orders',
    title: '工单中心',
    section: 'manage',
    navKey: 'admin',
    openCore: true,
  },
  {
    path: '/components',
    title: '组件案例',
    section: 'dev',
    navKey: 'components',
    openCore: true,
  },
];

export function getRouteMeta(pathname: string) {
  return [...routeMetaList]
    .sort((a, b) => b.path.length - a.path.length)
    .find((meta) => pathname === meta.path || pathname.startsWith(`${meta.path}/`));
}
