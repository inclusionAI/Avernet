// BCN 开源切片路由（assemble-oss 注入）：仅 / 产品首页 + /bcn/* 独立页面。
// 均 layout:false，不依赖 MainLayout（开源切片不含 src/layouts 与其余主应用页面）。
// 内部仓核心 config/routes.ts 仍为全量；本文件是 Phase 7 切片专用的 BCN-only 形态。
export const routes = [
  {
    name: 'BCN 首页',
    path: '/',
    component: './BcnHome',
    layout: false,
  },
  {
    path: '/bcn',
    layout: false,
    component: './Layout/BcnContainer',
    routes: [
      {
        name: 'BCN 入网注册',
        path: 'register',
        component: './BcnRegister',
      },
      {
        name: 'BCN 群聊页面',
        path: 'chat',
        routes: [
          { path: '', redirect: '/bcn/chat/list' },
          { name: '群聊列表', path: 'list', component: './GroupChat' },
          { name: '群聊详情', path: 'detail', component: './GroupChat' },
          {
            name: '邀请加入',
            path: 'invite/:type/:token',
            component: './InviteJoin',
            hideInMenu: true,
          },
          {
            name: '会话独立页',
            path: 'session',
            component: './GroupChat/SessionOnlyPage',
            hideInMenu: true,
          },
        ],
      },
      {
        // 目标驱动任务执行 — 任务入口页(副屏整体动态 DAG,FR-OBS-01~11)
        name: '任务执行',
        path: 'task-loop',
        routes: [
          { path: '', component: './TaskLoop', hideInMenu: true },
          {
            name: '任务执行流程',
            path: ':taskId',
            component: './TaskLoop',
            hideInMenu: true,
          },
        ],
      },
    ],
  },
];
