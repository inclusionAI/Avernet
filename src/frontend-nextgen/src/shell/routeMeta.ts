import { getCapabilities } from '@/capabilities';

export type RouteSection = 'work' | 'manage' | 'dev';

export interface RouteMeta {
  path: string;
  title: string;
  section: RouteSection;
  navKey: string;
  openCore: boolean;
}

// Open Core 基线 route meta。内部专属路由已剥离，
// 由 internal overlay 经 capability `getInternalRouteMetas` 注入，避免内部业务
// 入口字面量进 Open Core 产物（open-core-export-plan §5.2 / §5.6
// 「导航中的内部入口」必须物理分隔）。
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

/**
 * 合并基线 routeMetaList 与内部 overlay 注入的内部 route meta。
 * 同步签名：capability 不发请求，直接返回当前形态可用的内部 meta 列表。
 * Open Core 形态下 capability 返回 `[]`，结果 = Open Core 基线；internal 形态下追加内部 7 条。
 */
export function getMergedRouteMetas(): RouteMeta[] {
  const internal = getCapabilities().getInternalRouteMetas();
  return internal.status === 'available' && internal.value.length
    ? [...routeMetaList, ...internal.value]
    : routeMetaList;
}

export function getRouteMeta(pathname: string) {
  return [...getMergedRouteMetas()]
    .sort((a, b) => b.path.length - a.path.length)
    .find((meta) => pathname === meta.path || pathname.startsWith(`${meta.path}/`));
}
