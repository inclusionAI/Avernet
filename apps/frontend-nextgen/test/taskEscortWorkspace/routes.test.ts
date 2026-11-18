import { describe, expect, it } from '@jest/globals';
import { routes } from '../../config/routes';

interface RouteNode {
  path?: string;
  component?: string;
  redirect?: string;
  routes?: RouteNode[];
}

const appLayout = (routes as RouteNode[]).find((route) => route.component === '@/layouts/AppLayout');
const appRoutes = appLayout?.routes ?? [];

describe('任务护航公共路由集成', () => {
  it('任务护航复用现有 AppLayout，而非新增全局壳层或模块页签路由', () => {
    expect(appLayout).toBeDefined();
    expect(appRoutes.filter((route) => route.path === '/evolution-factory/task-escort')).toEqual([
      { path: '/evolution-factory/task-escort', component: '@/pages/TaskEscort' },
    ]);
    expect(routes.filter((route) => route.path === '/evolution-factory/task-escort')).toHaveLength(0);
  });

  it.each([
    ['/evolution-factory', '@/pages/EvolutionFactory'],
    ['/evolution-factory/new', '@/pages/EvolutionFactory/CreateTask'],
    ['/evolution-factory/tasks/:taskId', '@/pages/EvolutionFactory/TaskDetail'],
    ['/evolution-factory/domains/:ownerUserId/:domainId', '@/pages/EvolutionFactory/DomainDetail'],
    ['/evolution-factory/bench/runs/:benchRunId', '@/pages/EvolutionFactory/BenchRunDetail'],
  ])('保留既有深链 %s 及其组件，不重定向到任务护航', (path, component) => {
    expect(appRoutes.filter((route) => route.path === path)).toEqual([{ path, component }]);
  });
});
