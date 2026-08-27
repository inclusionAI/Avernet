export const routes = [
  { path: '/', redirect: '/workspace' },
  { path: '/work', redirect: '/workspace' },
  { path: '/manage', redirect: '/bot-workshop' },
  { path: '/collaboration-permissions', redirect: '/collaboration-privacy' },
  { path: '/bot-studio', redirect: '/bot-workshop' },
  { path: '/capability-studio', redirect: '/capability-workshop' },
  // /capability-market → /market 的别名随 Market 路由一同下沉到 routes.internal.ts：
  // Open Core 无 /market 路由，保留此处重定向会让深链跳到不存在的 /market 而 404。
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
      { path: '/admin', component: '@/pages/Admin' },
      // 兼容旧深链：管理后台为单页 #/admin + tab，子路由重定向至 /admin（PRD 单页意图）。
      { path: '/admin/spaces', redirect: '/admin' },
      { path: '/admin/work-orders', redirect: '/admin' },
      { path: '/components', component: '@/pages/ComponentExamples' },
      // 副屏引擎能力自测页（独立 ChatLayout，零对话依赖；对齐 /components 开发页约定）
    ],
  },
  {
    name: '设计规范',
    path: '/design-system',
    component: './Demo/DesignSystem',
  },
];
