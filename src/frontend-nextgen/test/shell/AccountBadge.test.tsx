/** @jest-environment jsdom */
import { AccountBadge } from '@/shell/AccountBadge';
import { expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

it('显示当前登录用户名称和在线状态', () => {
  render(<AccountBadge currentUser={{ displayName: '验收用户', online: true }} />);

  expect(screen.getByText('验收用户')).toBeInTheDocument();
  expect(screen.getByText('在线')).toBeInTheDocument();
  expect(screen.queryByText('张三')).not.toBeInTheDocument();
});

it('当前用户信息缺失时使用安全降级文案', () => {
  render(<AccountBadge currentUser={null} />);

  expect(screen.getByText('当前用户')).toBeInTheDocument();
  expect(screen.getByText('在线')).toBeInTheDocument();
  expect(screen.queryByText('张三')).not.toBeInTheDocument();
});
