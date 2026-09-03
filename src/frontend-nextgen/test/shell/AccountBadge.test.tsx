/** @jest-environment jsdom */
import { AccountBadge } from '@/shell/AccountBadge';
import { expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

it('显示当前登录用户名称和真实头像，但不显示用户状态', () => {
  render(<AccountBadge currentUser={{ displayName: '验收用户', avatarUrl: 'https://avatar.example/user.png' }} />);

  expect(screen.getByText('验收用户')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: '验收用户' })).toHaveAttribute('src', 'https://avatar.example/user.png');
  expect(screen.queryByText('在线')).not.toBeInTheDocument();
  expect(screen.queryByText('离线')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('用户在线')).not.toBeInTheDocument();
  expect(screen.queryByText('张三')).not.toBeInTheDocument();
});

it('当前用户信息缺失时使用安全降级文案且不显示状态', () => {
  render(<AccountBadge currentUser={null} />);

  expect(screen.getByText('当前用户')).toBeInTheDocument();
  expect(screen.queryByText('在线')).not.toBeInTheDocument();
  expect(screen.queryByText('离线')).not.toBeInTheDocument();
  expect(screen.queryByText('张三')).not.toBeInTheDocument();
});
