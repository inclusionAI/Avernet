/** @jest-environment jsdom */
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, waitFor } from '@testing-library/react';

let mockPathname = '/workspace';
const mockInitSpaceContext = jest.fn(async () => undefined);

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useLocation: () => ({ pathname: mockPathname }),
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  initSpaceContext: mockInitSpaceContext,
}));
jest.mock('@/services/workspace/identityService', () => ({
  identityService: {
    loadIdentities: jest.fn(async () => ({
      ok: true as const,
      data: {
        identities: [{ id: 'human-1', kind: 'user' as const, displayName: '验收用户', online: true }],
        defaultActiveId: 'human-1',
      },
    })),
    // AppShell 经 useHumanIdentity 调用上述两个状态访问器推导 loading/ready/error；
    // 此处同为 false：能力初值为 null → 触发单飞 loadIdentities → 写 store → ready。
    isIdentityLoading: jest.fn(() => false),
    isIdentityResolved: jest.fn(() => false),
  },
}));
jest.mock('@/shell/AppHeader', () => ({
  AppHeader: ({ currentUser }: { currentUser?: { displayName: string } | null }) => (
    <div data-testid="app-header">{currentUser?.displayName}</div>
  ),
}));
jest.mock('@/shell/AppSidebar', () => ({ AppSidebar: () => <div data-testid="app-sidebar" /> }));

const { AppShell } = require('@/shell/AppShell') as typeof import('@/shell/AppShell');

it('仅进入管理区域时初始化空间上下文', async () => {
  const view = render(<AppShell>工作内容</AppShell>);
  await waitFor(() => expect(mockInitSpaceContext).not.toHaveBeenCalled());

  mockPathname = '/bot-workshop';
  view.rerender(<AppShell>管理内容</AppShell>);
  await waitFor(() => expect(mockInitSpaceContext).toHaveBeenCalledTimes(1));
});

it('将 mine 返回的 Human 身份传给顶栏账号区', async () => {
  const view = render(<AppShell>工作内容</AppShell>);

  await waitFor(() => expect(view.getByTestId('app-header')).toHaveTextContent('验收用户'));
});
