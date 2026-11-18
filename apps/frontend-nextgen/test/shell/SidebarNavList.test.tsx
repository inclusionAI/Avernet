/** @jest-environment jsdom */
import { extendCapabilities } from '@/capabilities';
import { getMergedNavigationItems } from '@/shell/navigation';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { Compass } from 'lucide-react';

jest.mock('@/shell/SpaceSwitcher', () => ({ SpaceSwitcher: () => null }));

const { SidebarNavList } = require('@/shell/SidebarNavList') as typeof import('@/shell/SidebarNavList');

// 展开侧栏与移动端抽屉共用列表；折叠侧栏使用相同导航归属。
describe.each([false, true])('SidebarNavList (collapsed=%s)', (collapsed) => {
  const renderNavigation = (activePath: string, onNavigate = jest.fn()) =>
    render(
      <SidebarNavList
        activePath={activePath}
        items={getMergedNavigationItems()}
        onNavigate={onNavigate}
        collapsed={collapsed}
      />,
    );

  it('任务护航紧邻智能进化上方，两个入口分别导航且不增加模块页签', () => {
    const onNavigate = jest.fn();
    renderNavigation('/workspace/chat', onNavigate);
    const escort = screen.getByRole('button', { name: '任务护航' });
    const evolution = screen.getByRole('button', { name: '智能进化' });
    const buttons = screen.getAllByRole('button');
    expect(buttons.indexOf(escort)).toBe(buttons.indexOf(evolution) - 1);
    fireEvent.click(escort);
    fireEvent.click(evolution);
    expect(onNavigate.mock.calls).toEqual([['/evolution-factory/task-escort'], ['/evolution-factory']]);
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it.each(['/evolution-factory/task-escort', '/evolution-factory/task-escort/detail'])(
    '护航路径 %s 只高亮任务护航',
    (path) => {
      const view = renderNavigation(path);
      expect(screen.getByRole('button', { name: '任务护航' })).toHaveClass('bg-selected');
      expect(screen.getByRole('button', { name: '智能进化' })).not.toHaveClass('bg-selected');
      expect(view.container.querySelectorAll('button.bg-selected')).toHaveLength(1);
    },
  );

  it.each([
    '/evolution-factory',
    '/evolution-factory/new',
    '/evolution-factory/tasks/task-1',
    '/evolution-factory/domains/owner/domain',
    '/evolution-factory/bench/runs/run-1',
    '/evolution-factory/task-escort-other',
  ])('路径 %s 通过 metadata 高亮智能进化，非点击目的地前缀', (path) => {
    const view = renderNavigation(path);
    expect(screen.getByRole('button', { name: '智能进化' })).toHaveClass('bg-selected');
    expect(view.container.querySelectorAll('button.bg-selected')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Bot 管理' })).not.toHaveClass('bg-selected');
    expect(screen.getByRole('button', { name: '任务护航' })).not.toHaveClass('bg-selected');
  });

  it.each(['/space-admin', '/ticket-center', '/evolution-factory-other', '/bot-workshop-other'])(
    '无导航归属路径 %s 不会误命中相似前缀',
    (path) => {
      const view = renderNavigation(path);
      expect(view.container.querySelectorAll('button.bg-selected')).toHaveLength(0);
    },
  );

  it('保留其它分组导航的高亮与原有点击目的地', () => {
    const onNavigate = jest.fn();
    renderNavigation('/workspace/chat', onNavigate);
    const conversation = screen.getByRole('button', { name: '对话' });
    expect(conversation).toHaveClass('bg-selected');
    expect(screen.getByRole('button', { name: '智能进化' })).not.toHaveClass('bg-selected');
    fireEvent.click(conversation);
    expect(onNavigate).toHaveBeenCalledWith('/workspace/chat');
  });
});

// capability 扩展不可恢复，故注入回归放在默认形态用例之后。
it.each([false, true])('无 route meta 的 overlay 项保留最长路径兜底 (collapsed=%s)', (collapsed) => {
  extendCapabilities({
    getInternalNavigationItems: () => ({
      status: 'available',
      value: [{ id: 'custom', label: '扩展能力', path: '/custom', icon: Compass, section: 'bot', description: '' }],
    }),
  });
  const onNavigate = jest.fn();
  const view = render(
    <SidebarNavList
      activePath="/custom/detail"
      items={getMergedNavigationItems()}
      onNavigate={onNavigate}
      collapsed={collapsed}
    />,
  );
  const custom = screen.getByRole('button', { name: '扩展能力' });
  expect(custom).toHaveClass('bg-selected');
  expect(view.container.querySelectorAll('button.bg-selected')).toHaveLength(1);
  fireEvent.click(custom);
  expect(onNavigate).toHaveBeenCalledWith('/custom');
});

it.each([false, true])('overlay 合并后保持护航 / 进化相邻及互斥高亮 (collapsed=%s)', (collapsed) => {
  extendCapabilities({
    getInternalNavigationItems: () => ({
      status: 'available',
      value: [
        {
          id: 'capability-workshop',
          label: '能力管理',
          path: '/capability-workshop',
          icon: Compass,
          section: 'bot',
          description: '',
        },
        { id: 'market', label: '能力市场', path: '/market', icon: Compass, section: 'bot', description: '' },
        {
          id: 'custom-tail',
          label: '尾部扩展',
          path: '/custom-tail',
          icon: Compass,
          section: 'bot',
          description: '',
          placement: 'last',
        },
      ],
    }),
  });
  const onNavigate = jest.fn();
  const items = getMergedNavigationItems();
  const view = render(
    <SidebarNavList
      activePath="/evolution-factory/task-escort"
      items={items}
      onNavigate={onNavigate}
      collapsed={collapsed}
    />,
  );
  const labels = screen.getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent);
  expect(labels.slice(-6)).toEqual(['Bot 管理', '能力管理', '能力市场', '任务护航', '智能进化', '尾部扩展']);
  expect(screen.getByRole('button', { name: '任务护航' })).toHaveClass('bg-selected');
  expect(screen.getByRole('button', { name: '智能进化' })).not.toHaveClass('bg-selected');
  fireEvent.click(screen.getByRole('button', { name: '智能进化' }));
  expect(onNavigate).toHaveBeenCalledWith('/evolution-factory');
  view.rerender(
    <SidebarNavList
      activePath="/evolution-factory/tasks/task-1"
      items={items}
      onNavigate={onNavigate}
      collapsed={collapsed}
    />,
  );
  expect(screen.getByRole('button', { name: '任务护航' })).not.toHaveClass('bg-selected');
  expect(screen.getByRole('button', { name: '智能进化' })).toHaveClass('bg-selected');
  expect(view.container.querySelectorAll('button.bg-selected')).toHaveLength(1);
});
