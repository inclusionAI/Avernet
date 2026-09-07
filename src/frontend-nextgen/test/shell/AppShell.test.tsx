/** @jest-environment jsdom */
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { useState } from 'react';

let mockPathname = '/workspace';
const mockInitSpaceContext = jest.fn(async () => undefined);
const mockEnsurePersonalSpaceOnAppEntry = jest.fn(async () => undefined);

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useLocation: () => ({ pathname: mockPathname }),
}));
jest.mock('@/hooks/useSpaceContext', () => ({
  initSpaceContext: mockInitSpaceContext,
  ensurePersonalSpaceOnAppEntry: mockEnsurePersonalSpaceOnAppEntry,
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

beforeEach(() => {
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  window.localStorage.clear();
});

it('Open Core 体验提示位于 AppHeader 前', () => {
  const view = render(<AppShell>工作内容</AppShell>);
  const notice = view.getByRole('status', { name: '开源体验环境提示' });
  const header = view.getByTestId('app-header');
  expect(notice.compareDocumentPosition(header) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('关闭体验提示不重挂载页面业务状态', () => {
  function StatefulPage() {
    const [count, setCount] = useState(0);
    return (
      <button type="button" data-testid="stateful-page" onClick={() => setCount((value) => value + 1)}>
        {count}
      </button>
    );
  }

  const view = render(
    <AppShell>
      <StatefulPage />
    </AppShell>,
  );
  fireEvent.click(view.getByTestId('stateful-page'));
  expect(view.getByTestId('stateful-page')).toHaveTextContent('1');

  fireEvent.click(view.getByRole('button', { name: '我已知悉' }));

  expect(view.queryByRole('status', { name: '开源体验环境提示' })).toBeNull();
  expect(view.getByTestId('stateful-page')).toHaveTextContent('1');
});

it('仅进入管理区域时初始化空间上下文', async () => {
  const view = render(<AppShell>工作内容</AppShell>);
  await waitFor(() => expect(mockInitSpaceContext).not.toHaveBeenCalled());

  mockPathname = '/bot-workshop';
  view.rerender(<AppShell>管理内容</AppShell>);
  await waitFor(() => expect(mockInitSpaceContext).toHaveBeenCalledTimes(1));
});

it('挂载即初始化一次个人空间（不等进入管理区域）', async () => {
  mockEnsurePersonalSpaceOnAppEntry.mockClear(); // mock 为文件级共享：清掉前一用例的累计调用
  render(<AppShell>工作内容</AppShell>); // 初始 pathname=/workspace（工作区域）也需触发
  await waitFor(() => expect(mockEnsurePersonalSpaceOnAppEntry).toHaveBeenCalledTimes(1));
});

it('将 mine 返回的 Human 身份传给顶栏账号区', async () => {
  const view = render(<AppShell>工作内容</AppShell>);

  await waitFor(() => expect(view.getByTestId('app-header')).toHaveTextContent('验收用户'));
});

it('登录回归：/auth/user（checkAuth）晚于 mine 落位时，顶栏账号区随 externalAuthStore 刷新', async () => {
  // Open Core（oauth-provider）：/auth/user 与 mine 并跑且常更晚返回；capability 契约规定
  // externalAuthStore.user 优先。AppShell 一次性快照 currentUser 会把 mine 兜底身份冻结进顶栏
  // （登录后头像/花名不一致，需切 tab 触发 re-render 才纠正）。
  const view = render(<AppShell>工作内容</AppShell>);
  await waitFor(() => expect(view.getByTestId('app-header')).toHaveTextContent('验收用户'));

  act(() => {
    useExternalAuthStore.getState().setAuthenticated({
      userId: 'Asbku1dJX8Pe',
      displayName: '福惠',
      provider: 'alipay',
      avatarUrl: 'https://tfs.example/avatar.png',
    });
  });

  await waitFor(() => expect(view.getByTestId('app-header')).toHaveTextContent('福惠'));
});
