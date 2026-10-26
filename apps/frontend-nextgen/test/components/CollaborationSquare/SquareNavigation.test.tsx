/** @jest-environment jsdom */
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => <a href={to} {...props}>{children}</a>,
}));

test('发现导航保留公开Bot/群并提供专家市场', () => {
  const { rerender } = render(<SquareNavigation active="bot" />);
  const bot = screen.getByRole('link', { name: '公开Bot' });
  const group = screen.getByRole('link', { name: '公开协作群' });
  expect(bot).toHaveClass('rounded-lg', 'bg-primary/10', 'text-primary');
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


test('专家市场使用独立可访问路由与选中状态', () => {
  render(<SquareNavigation active="expert" />);
  const expert = screen.getByRole('link', { name: '专家市场' });
  expect(expert).toHaveAttribute('href', '/collaboration-square/experts');
  expect(expert).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: '公开Bot' })).not.toHaveAttribute('aria-current');
});
