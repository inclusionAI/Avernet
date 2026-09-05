/** @jest-environment jsdom */
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const space = {
  spaceId: 10000,
  spaceCode: 'personal',
  spaceName: '个人空间',
  spaceType: 'PERSONAL' as const,
  joinStatus: 'JOINED' as const,
  currentUserRole: 'ADMIN' as const,
};

jest.mock('@/hooks/useSpaceContext', () => ({
  useSpaceContext: (selector: (state: unknown) => unknown) =>
    selector({ currentSpace: space, currentSpaceId: space.spaceId, spaces: [space], loading: false, error: null }),
  refreshSpaceContext: jest.fn(async () => undefined),
  switchSpaceContext: jest.fn(),
}));

const { SpaceSwitcher } = require('@/shell/SpaceSwitcher') as typeof import('@/shell/SpaceSwitcher');

describe('SpaceSwitcher', () => {
  it('展示管理空间标题和切换卡片样式', () => {
    render(<SpaceSwitcher />);

    expect(screen.getByText('管理空间')).toBeInTheDocument();
    expect(screen.queryByLabelText('管理空间说明')).not.toBeInTheDocument();
    expect(screen.queryByText('“管理”下所有页面数据均按此空间展示')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: '个人空间图标' })).toHaveClass('h-6', 'w-6');
    expect(screen.getByText('个人空间', { selector: 'span.truncate' })).toHaveClass('text-xs');
    expect(screen.getByRole('button', { name: /个人空间/ })).toHaveClass(
      'rounded-lg',
      'border',
      'bg-muted/60',
      'min-h-9',
      'px-2.5',
      'py-1.5',
    );
  });
});
