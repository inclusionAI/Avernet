import { getCapabilities } from '@/capabilities';

/**
 * 路由分组（refactor-global-nav-shell）：collab=协作分组、bot=Bot 分组、legacy=老功能过渡组
 * （废弃入口保底可达）、dev=开发页（不进侧栏分组导航）。原 work/manage 双区域模型随顶栏区域 Tab 退役。
 */
export type RouteSection = 'collab' | 'bot' | 'legacy' | 'dev';

export interface RouteMeta {
  path: string;
  title: string;
  section: RouteSection;
  /** 对应侧栏导航项 id；不占导航位的路由（如 /space-admin、/ticket-center）缺省。 */
  navKey?: string;
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
    section: 'collab',
    navKey: 'workspace',
    openCore: true,
  },
  {
    path: '/work/my-task',
    title: '任务列表',
    section: 'legacy',
    navKey: 'my-task',
    openCore: true,
  },
  {
    path: '/collaboration-square',
    title: '发现',
    section: 'collab',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-square/bots',
    title: '发现 Bot',
    section: 'collab',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-square/groups',
    title: '发现群组',
    section: 'collab',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-square/tasks',
    title: '发现任务',
    section: 'collab',
    navKey: 'collaboration-square',
    openCore: true,
  },
  {
    path: '/collaboration-privacy',
    title: '协作权限',
    section: 'legacy',
    navKey: 'collaboration-privacy',
    openCore: true,
  },
  {
    path: '/bot-workshop',
    title: 'Bot管理',
    section: 'bot',
    navKey: 'bot-workshop',
    openCore: true,
  },
  {
    path: '/bot-workshop/detail',
    title: 'Bot 详情',
    section: 'bot',
    navKey: 'bot-workshop',
    openCore: true,
  },
  {
    path: '/bot-workshop/logs',
    title: 'Bot 日志',
    section: 'bot',
    navKey: 'bot-workshop',
    openCore: true,
  },
  // split-admin-space-ticket-pages：管理后台单页拆分——两页面均不占导航位（故不设 navKey，
  // 侧栏不因路由高亮任何菜单项）；section 取 legacy 保持分段语义完备。原 /admin 系列 meta 随单页壳退役。
  {
    path: '/space-admin',
    title: '空间管理',
    section: 'legacy',
    openCore: true,
  },
  {
    path: '/ticket-center',
    title: '通知中心',
    section: 'legacy',
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
