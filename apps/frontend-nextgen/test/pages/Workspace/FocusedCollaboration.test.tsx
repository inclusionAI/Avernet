/** @jest-environment jsdom */
import FocusedCollaborationPage from '@/pages/Workspace/Collaboration/Focused';
import { collaborationScopeService } from '@/services/workspace/collaborationScopeService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { scopeGroup, scopeIdentity, scopeSession } from '../../mocks/collaborationScope';

let mockPath = '/workspace/collaboration-only';
let mockSearch = '';
const mockPush = jest.fn();
const mockReplace = jest.fn();
const mockArea = jest.fn();
jest.mock('@umijs/max', () => ({
  useLocation: () => ({ pathname: mockPath, search: mockSearch }),
  history: { push: (...args: unknown[]) => mockPush(...args), replace: (...args: unknown[]) => mockReplace(...args) },
}));
jest.mock('@/services/workspace/collaborationScopeService');
jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: () => ({ identity: null, status: 'ready' }) }));
jest.mock('@/services/workspace/workspaceService', () => ({ workspaceService: { initWorkspace: jest.fn() } }));
jest.mock('@/pages/Workspace/Collaboration', () => ({ __esModule: true, default: () => <div>完整协作区域内容</div> }));
jest.mock('@/pages/Workspace/GroupWorkspaceArea', () => ({
  GroupWorkspaceArea: (props: unknown) => {
    mockArea(props);
    return <div>受限协作区域内容</div>;
  },
}));
beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({ identities: [scopeIdentity], activeIdentityId: scopeIdentity.id });
  jest
    .mocked(collaborationScopeService.activate)
    .mockImplementation(
      jest.requireActual<typeof import('@/services/workspace/collaborationScopeService')>(
        '@/services/workspace/collaborationScopeService',
      ).collaborationScopeService.activate,
    );
  jest
    .mocked(collaborationScopeService.load)
    .mockResolvedValue({ ok: true, data: { group: scopeGroup, session: scopeSession } });
});
it('only page reuses the full collaboration area and returns with selection', () => {
  mockPath = '/workspace/collaboration-only';
  mockSearch = '';
  useWorkspaceStore.setState({ selectedGroupId: 'g', selectedSessionId: 'g:s' });
  render(<FocusedCollaborationPage />);
  expect(screen.getByText('完整协作区域内容')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '返回完整协作区' }));
  expect(mockPush).toHaveBeenCalledWith(
    expect.stringContaining('/workspace/collaboration?current=human_test&group=g&session=g%3As'),
  );
});
it('invalid group link shows an error and safe return but mounts no chat', async () => {
  mockPath = '/workspace/collaboration/group';
  mockSearch = '?current=human_test';
  render(<FocusedCollaborationPage />);
  expect(await screen.findByText(/链接缺少必填参数 group/)).toBeInTheDocument();
  expect(mockArea).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '返回完整协作区' }));
  expect(mockPush).toHaveBeenCalledWith('/workspace/collaboration');
});
it.each(['group', 'session'])('%s mode passes validated scope and the matching presentation', async (mode) => {
  mockPath = `/workspace/collaboration/${mode}`;
  mockSearch = '?current=human_test&group=g&session=g%3As';
  render(<FocusedCollaborationPage />);
  await screen.findByText('受限协作区域内容');
  expect(mockArea).toHaveBeenLastCalledWith(
    expect.objectContaining({
      sessionOnly: mode === 'session',
      scope: { group: scopeGroup, session: scopeSession, identityId: 'human_test' },
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: '返回完整协作区' }));
  expect(mockPush).toHaveBeenCalledWith(expect.stringContaining('group=g&session=g%3As'));
});

it('group selection updates only the scoped URL', async () => {
  mockPath = '/workspace/collaboration/group';
  mockSearch = '?current=human_test&group=g&session=g%3As';
  render(<FocusedCollaborationPage />);
  await screen.findByText('受限协作区域内容');
  act(() => useWorkspaceStore.getState().selectSession('next'));
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith(expect.stringContaining('session=next')));
});
