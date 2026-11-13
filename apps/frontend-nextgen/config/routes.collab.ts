// collab（协作群极简站）路由表：FRONTEND_FORM=collab 时整表替换 config/routes.ts。
// 与 internal overlay 的覆盖语义无关——本表是独立装配形态的全量路由（不含 design-system/welcome）。
export const routes = [
  // 默认入口：协作群。
  { path: '/', redirect: '/workspace/collaboration' },
  {
    path: '/',
    component: '@/layouts/AppLayout',
    routes: [
      { path: '/workspace', redirect: '/workspace/collaboration' },
      { path: '/workspace/collaboration', component: '@/pages/Workspace/Collaboration' },
      // 聚焦/嵌入深链（无全局导航）原样保留。
      { path: '/workspace/collaboration-only', component: '@/pages/Workspace/Collaboration/Focused' },
      { path: '/workspace/collaboration/group', component: '@/pages/Workspace/Collaboration/Focused' },
      { path: '/workspace/collaboration/session', component: '@/pages/Workspace/Collaboration/Focused' },
      // 邀请加群链路与 BCN 外部协作会话外链落地页保留。
      { path: '/workspace/invite/:type/:token', component: '@/pages/Workspace/InviteAcceptPanel' },
      { path: '/workspace/bcn/chat/detail', component: '@/pages/Workspace/BcnChatDetail' },
      // BCS-only 公开发现：不注册专家、任务广场或单聊。
      { path: '/collaboration-square', redirect: '/collaboration-square/bots' },
      { path: '/collaboration-square/bots', component: '@/pages/CollaborationSquare/Bots' },
      { path: '/collaboration-square/groups', component: '@/pages/CollaborationSquare/Groups' },
      // 外部 Bot 管理（Bot 管理「外部 Bot」面板的独立薄页面）。
      { path: '/external-bots', component: '@/pages/ExternalBots' },
      // 404 兜底：本形态不含的页面（单聊/BotWorkshop/Lab/…）统一落到 NotFound。
      { path: '*', component: '@/pages/NotFound' },
    ],
  },
];
