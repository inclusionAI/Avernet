/** @jest-environment jsdom */
import CollaborationPage from '@/pages/Workspace/Collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() }, useSearchParams: jest.fn() }));
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: () => ({ identity: null }) }));
jest.mock('@/hooks/useMediaQuery', () => ({ useMinWidth: () => true }));
jest.mock('@/pages/Workspace/GroupWorkspaceArea', () => ({ GroupWorkspaceArea: () => <div>工作区内容</div> }));

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([{ id: 'bot-1', kind: 'bot', displayName: 'Bot', online: true }], 'bot-1');
  window.history.replaceState({}, '', '/workspace/collaboration?current=human_missing&group=g1&session=s1');
  jest.mocked(useSearchParams).mockImplementation(() => [new URLSearchParams(window.location.search), jest.fn()]);
  jest.mocked(history.replace).mockImplementation((path) => { window.history.replaceState({}, '', String(path)); });
});
afterEach(() => { jest.useRealTimers(); });

it('bounds missing-Human hydration and provides explicit refresh/return recovery without rendering a Bot workspace', () => {
  const page = render(<CollaborationPage />);
  expect(screen.getByText('正在打开协作区…')).toBeInTheDocument();
  act(() => jest.advanceTimersByTime(10_000));
  expect(screen.getByText('无法打开指定协作区')).toBeInTheDocument();
  expect(screen.queryByText('工作区内容')).not.toBeInTheDocument();
  expect(window.location.search).toContain('current=human_missing');
  expect(screen.getByRole('button', { name: '刷新页面' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '返回协作区' }));
  page.rerender(<CollaborationPage />);
  expect(screen.getByText('工作区内容')).toBeInTheDocument();
});
