export const routes = [
  { path: '/', redirect: '/workspace' },
  { path: '/work', redirect: '/workspace' },
  { path: '/manage', redirect: '/bot-workshop' },
  { path: '/collaboration-permissions', redirect: '/collaboration-privacy' },
  { path: '/bot-studio', redirect: '/bot-workshop' },
  // /capability-studio → /capability-workshop、/capability-market → /market 的旧深链
  // 别名随 Market / CapabilityWorkshop 路由一同下沉到 routes.internal.ts：
  // Open Core 无这些内部路由，保留此处重定向会让深链跳到不存在的目标而 404。
  {
    path: '/',
    component: '@/layouts/AppLayout',
    routes: [
      { path: '/workspace', component: '@/pages/Workspace' },
      { path: '/work/my-task', component: '@/pages/MyTask' },
      { path: '/workspace/invite/:token', component: '@/pages/Workspace/InviteAcceptPanel' },
      { path: '/collaboration-square', redirect: '/collaboration-square/bots' },
      { path: '/collaboration-square/bots', component: '@/pages/CollaborationSquare/Bots' },
      { path: '/collaboration-square/groups', component: '@/pages/CollaborationSquare/Groups' },
      { path: '/collaboration-privacy', component: '@/pages/CollaborationPrivacy' },
      { path: '/bot-workshop', component: '@/pages/BotWorkshop' },
      { path: '/bot-workshop/logs', component: '@/pages/BotWorkshop/Logs' },
      { path: '/bot-workshop/detail', component: '@/pages/BotWorkshop/Detail' },
      { path: '/bot-workshop/health-check', component: '@/pages/BotWorkshop/HealthCheck' },
      { path: '/admin', component: '@/pages/Admin' },
      // 兼容旧深链：管理后台为单页 #/admin + tab，子路由重定向至 /admin（PRD 单页意图）。
      { path: '/admin/spaces', redirect: '/admin' },
      { path: '/admin/work-orders', redirect: '/admin' },
      { path: '/components', component: '@/pages/ComponentExamples' },
      // 副屏引擎能力自测页（独立 ChatLayout，零对话依赖；对齐 /components 开发页约定）
      // 404 兜底：匹配 AppLayout 下未注册的子路由（如 /xyz），避免空白页。
      // 通配符 * 优先级最低，不会覆盖已注册精确路由；内源 overlay 注入的路由
      // 会追加到本数组末尾，仍按精确匹配优先命中，不受 * 位置影响。
      { path: '*', component: '@/pages/NotFound' },
    ],
  },
  {
    name: '设计规范',
    path: '/design-system',
    component: './Demo/DesignSystem',
  },
];
