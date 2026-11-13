// collab 形态路由表契约（纯数据断言，node 环境）：白名单内路由 + 默认入口 + 404 兜底。
import { describe, expect, it } from '@jest/globals';
import { routes } from '../../config/routes.collab';

interface RouteEntry {
  path: string;
  component?: string;
  redirect?: string;
}

const topLevel: RouteEntry[] = routes as RouteEntry[];
const appLayoutEntry = topLevel.find((route) => Array.isArray((route as { routes?: unknown }).routes)) as unknown as {
  routes: RouteEntry[];
};
const appLayoutRoutes = appLayoutEntry.routes;
const findRoute = (path: string): RouteEntry | undefined => appLayoutRoutes.find((route) => route.path === path);

describe('collab 形态路由表', () => {
  it('默认入口重定向到协作群', () => {
    expect(topLevel.find((route) => route.path === '/')).toEqual({
      path: '/',
      redirect: '/workspace/collaboration',
    });
    expect(findRoute('/workspace')).toEqual({ path: '/workspace', redirect: '/workspace/collaboration' });
  });

  it('协作群主路由与聚焦深链注册', () => {
    expect(findRoute('/workspace/collaboration')).toEqual({
      path: '/workspace/collaboration',
      component: '@/pages/Workspace/Collaboration',
    });
    for (const path of ['/workspace/collaboration-only', '/workspace/collaboration/group', '/workspace/collaboration/session']) {
      expect(findRoute(path)?.component).toBe('@/pages/Workspace/Collaboration/Focused');
    }
  });

  it('邀请/BCN 深链与外部 Bot 页注册', () => {
    expect(findRoute('/workspace/invite/:type/:token')?.component).toBe('@/pages/Workspace/InviteAcceptPanel');
    expect(findRoute('/workspace/bcn/chat/detail')?.component).toBe('@/pages/Workspace/BcnChatDetail');
    expect(findRoute('/external-bots')).toEqual({ path: '/external-bots', component: '@/pages/ExternalBots' });
  });

  it('白名单：不含单聊/BotWorkshop/Lab/进化工厂/管理域/欢迎页', () => {
    const allPaths = appLayoutRoutes.map((route) => route.path);
    const forbidden = [
      '/workspace/chat',
      '/bot-workshop',
      '/collaboration-square/experts',
      '/collaboration-square/tasks',
      '/collaboration-square/community',
      '/lab',
      '/evolution-factory',
      '/work/my-task',
      '/space-admin',
      '/ticket-center',
      '/components',
      '/private-chat/chat',
    ];
    for (const prefix of forbidden) {
      expect(allPaths.some((path) => path === prefix || path?.startsWith(`${prefix}/`))).toBe(false);
    }
    expect(topLevel.some((route) => route.path === '/welcome' || route.path === '/design-system')).toBe(false);
  });

  it('发现只注册公开 Bot 和公开协作群，入口默认指向公开 Bot', () => {
    expect(findRoute('/collaboration-square')?.redirect).toBe('/collaboration-square/bots');
    expect(findRoute('/collaboration-square/bots')?.component).toBe('@/pages/CollaborationSquare/Bots');
    expect(findRoute('/collaboration-square/groups')?.component).toBe('@/pages/CollaborationSquare/Groups');
  });

  it('404 兜底为 NotFound', () => {
    expect(findRoute('*')).toEqual({ path: '*', component: '@/pages/NotFound' });
  });
});
