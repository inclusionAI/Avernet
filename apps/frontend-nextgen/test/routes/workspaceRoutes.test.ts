// workspace-conversation-navigation-refactor Task 8:
// /workspace 拆分后的具体路由登记契约(纯数据断言,node 环境,无 DOM):
// - /workspace 经 redirect 落到 /workspace/chat;
// - /workspace/chat 与 /workspace/collaboration 挂在 AppLayout 下;
// - 邀请/BCN 外链深链路由保持不变;旧混合 /workspace 页面组件不再直接注册。
import { describe, expect, it } from '@jest/globals';
import { routes } from '../../config/routes';

interface RouteEntry {
  path: string;
  component?: string;
  redirect?: string;
}

const appLayoutRoutes: RouteEntry[] = (
  routes.find((route) => Array.isArray((route as { routes?: unknown }).routes)) as {
    routes: RouteEntry[];
  }
).routes;

const findRoute = (path: string): RouteEntry | undefined => appLayoutRoutes.find((route) => route.path === path);

describe('workspace route registration(Tasks 8 拆分页)', () => {
  it('/workspace 重定向到 /workspace/chat', () => {
    expect(findRoute('/workspace')).toEqual({ path: '/workspace', redirect: '/workspace/chat' });
  });

  it('/workspace/chat 与 /workspace/collaboration 注册为具体页面路由', () => {
    expect(findRoute('/workspace/chat')).toEqual({
      path: '/workspace/chat',
      component: '@/pages/Workspace/Chat',
    });
    expect(findRoute('/workspace/collaboration')).toEqual({
      path: '/workspace/collaboration',
      component: '@/pages/Workspace/Collaboration',
    });
  });

  it('邀请与 BCN 深链路由保持原样,旧混合页不再直接注册', () => {
    expect(findRoute('/workspace/invite/:type/:token')).toEqual({
      path: '/workspace/invite/:type/:token',
      component: '@/pages/Workspace/InviteAcceptPanel',
    });
    expect(findRoute('/workspace/bcn/chat/detail')).toEqual({
      path: '/workspace/bcn/chat/detail',
      component: '@/pages/Workspace/BcnChatDetail',
    });
    expect(appLayoutRoutes.some((route) => route.component === '@/pages/Workspace')).toBe(false);
  });
});
