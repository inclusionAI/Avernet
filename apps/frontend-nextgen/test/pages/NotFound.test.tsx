/** @jest-environment jsdom */
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';
import NotFound from '@/pages/NotFound';
import { routes as openRoutes } from '../../config/routes';
import { routes as collabRoutes } from '../../config/routes.collab';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

afterEach(() => {
  jest.clearAllMocks();
  jest.restoreAllMocks();
});

describe('NotFound', () => {
  it.each([
    { form: 'Open', routes: openRoutes, destination: '/workspace/chat' },
    { form: 'collab', routes: collabRoutes, destination: '/workspace/collaboration' },
  ])('$form 的返回工作台入口由路由表重定向到已注册页面', ({ routes, destination }) => {
    render(<NotFound />);

    fireEvent.click(screen.getByRole('button', { name: '返回工作台' }));

    expect(history.push).toHaveBeenCalledWith('/workspace');
    const workspacePath = jest.mocked(history.push).mock.calls[0][0];
    const appRoutes = routes.flatMap((route) => route.routes ?? []);
    expect(appRoutes.find((route) => route.path === workspacePath)?.redirect).toBe(destination);
    expect(appRoutes.find((route) => route.path === destination)?.component).toBeTruthy();
  });

  it('返回上一页仍使用浏览器历史', () => {
    const back = jest.spyOn(window.history, 'back').mockImplementation(() => {});
    render(<NotFound />);

    fireEvent.click(screen.getByRole('button', { name: '返回上一页' }));

    expect(back).toHaveBeenCalledTimes(1);
    expect(history.push).not.toHaveBeenCalled();
  });
});
