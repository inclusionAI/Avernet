/** @jest-environment jsdom */
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => <a href={to} {...props}>{children}</a>,
}));

test('发现导航保留公开 Bot/群并提供专家', () => {
  const { rerender } = render(<SquareNavigation active="bot" />);
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['公开 Bot', '专家', '公开协作群']);
  const bot = screen.getByRole('link', { name: '公开 Bot' });
  const group = screen.getByRole('link', { name: '公开协作群' });
  // dmore Frame 1：选中 pill 为 6px 圆角 + 主色软底；非选中无底色。
  expect(bot).toHaveClass('rounded-md', 'bg-primary/10', 'text-primary', 'h-8', 'px-3', 'text-[13px]');
  expect(bot).toHaveAttribute('aria-current', 'page');
  expect(bot.querySelector('[aria-hidden]')).toBeNull();
  expect(group).not.toHaveClass('bg-primary/10');
  expect(bot).toHaveAttribute('href', '/collaboration-square/bots');
  expect(group).toHaveAttribute('href', '/collaboration-square/groups');
  rerender(<SquareNavigation active="group" />);
  expect(group).toHaveClass('bg-primary/10', 'text-primary');
  expect(group).toHaveAttribute('aria-current', 'page');
  expect(bot).not.toHaveClass('bg-primary/10');
  expect(bot).not.toHaveAttribute('aria-current');
});

test('专家使用独立可访问路由与选中状态', () => {
  render(<SquareNavigation active="expert" />);
  const expert = screen.getByRole('link', { name: '专家' });
  expect(expert).toHaveAttribute('href', '/collaboration-square/experts');
  expect(expert).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: '公开 Bot' })).not.toHaveAttribute('aria-current');
});

test('页头右侧 actions 槽位仅在透传时渲染工具区', () => {
  const actions = <button type="button">工具</button>;
  const { rerender, container } = render(<SquareNavigation active="bot" actions={actions} />);
  expect(screen.getByRole('button', { name: '工具' })).toBeInTheDocument();
  // 页头为 60px 单行，工具区贴右。
  expect(container.querySelector('nav > div')).toHaveClass('h-15', 'justify-between', 'px-5');
  rerender(<SquareNavigation active="bot" />);
  expect(container.querySelector('nav > div')).not.toContainHTML('工具');
});

test('页头右侧工具区允许压缩：定宽内容过长时收缩截断而不是顶出页面', () => {
  const actions = <button type="button">工具</button>;
  render(<SquareNavigation active="bot" actions={actions} />);
  const wrapper = screen.getByRole('button', { name: '工具' }).parentElement;
  // flex 子项默认可收缩，禁止 shrink-0 锁死活动区宽度（348/338 定宽工具会把他推到视口外）。
  expect(wrapper).toHaveClass('min-w-0');
  expect(wrapper).not.toHaveClass('shrink-0');
});
