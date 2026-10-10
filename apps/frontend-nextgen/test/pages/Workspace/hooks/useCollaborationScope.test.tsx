/** @jest-environment jsdom */
import { useCollaborationScope } from '@/pages/Workspace/hooks/useCollaborationScope';
import { collaborationScopeService } from '@/services/workspace/collaborationScopeService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { scopeGroup, scopeIdentity, scopeSession } from '../../../mocks/collaborationScope';

jest.mock('@/services/workspace/collaborationScopeService');
jest.mock('@/services/workspace/workspaceService', () => ({
  workspaceService: { initWorkspace: jest.fn(async () => ({ ok: true })) },
}));
beforeEach(() => {
  jest.clearAllMocks();
  jest
    .mocked(collaborationScopeService.activate)
    .mockImplementation(
      jest.requireActual<typeof import('@/services/workspace/collaborationScopeService')>(
        '@/services/workspace/collaborationScopeService',
      ).collaborationScopeService.activate,
    );
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({ identities: [scopeIdentity], activeIdentityId: scopeIdentity.id });
  jest
    .mocked(collaborationScopeService.load)
    .mockResolvedValue({ ok: true, data: { group: scopeGroup, session: scopeSession } });
});
it('validates before mounting, then hydrates the pinned selection', async () => {
  const { result } = renderHook(() => useCollaborationScope('session', 'current=human_test&group=g&session=g%3As'));
  expect(result.current.scope).toBeNull();
  await waitFor(() => expect(result.current.scope?.group.groupId).toBe('g'));
  expect(useWorkspaceStore.getState()).toMatchObject({
    selectedGroupId: 'g',
    selectedSessionId: 'g:s',
    activeIdentityId: 'human_test',
  });
});
it('rejects missing params and an explicit unknown identity without requests', async () => {
  const { result, rerender } = renderHook(({ query }) => useCollaborationScope('group', query), {
    initialProps: { query: 'current=human_test' },
  });
  await waitFor(() => expect(result.current.error).toContain('group'));
  rerender({ query: 'current=unknown&group=g' });
  await waitFor(() => expect(result.current.error).toContain('身份'));
  expect(collaborationScopeService.load).not.toHaveBeenCalled();
});
it('ignores old responses after a URL change', async () => {
  let resolveOld!: (v: Awaited<ReturnType<typeof collaborationScopeService.load>>) => void;
  jest.mocked(collaborationScopeService.load).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  const { result, rerender } = renderHook(({ query }) => useCollaborationScope('group', query), {
    initialProps: { query: 'group=old' },
  });
  rerender({ query: 'group=g' });
  await waitFor(() => expect(result.current.scope?.group.groupId).toBe('g'));
  await act(async () => resolveOld({ ok: true, data: { group: { ...scopeGroup, groupId: 'old' }, session: null } }));
  expect(result.current.scope?.group.groupId).toBe('g');
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g');
});
it('keeps current identity when current is omitted and allows retry after failure', async () => {
  jest
    .mocked(collaborationScopeService.load)
    .mockResolvedValueOnce({ ok: false, error: { code: 'DENIED', friendlyMessage: '无权访问', canRetry: true } });
  const { result } = renderHook(() => useCollaborationScope('group', 'group=g'));
  await waitFor(() => expect(result.current.error).toBe('无权访问'));
  expect(result.current.scope).toBeNull();
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.scope?.identityId).toBe('human_test'));
});

it('acknowledges its own group URL projection without remounting or reactivating selection', async () => {
  const projectedQuery = { current: null as string | null };
  const { result, rerender } = renderHook(({ query }) => useCollaborationScope('group', query, projectedQuery), {
    initialProps: { query: '?group=g' },
  });
  await waitFor(() => expect(result.current.scope).not.toBeNull());
  const scope = result.current.scope;
  act(() => useWorkspaceStore.getState().selectSession('next'));
  projectedQuery.current = 'group=g&session=next';
  rerender({ query: '?group=g&session=next' });
  expect(result.current.scope).toBe(scope);
  expect(collaborationScopeService.load).toHaveBeenCalledTimes(1);
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('next');
});
it('does not expose the old scope if the active identity changes behind the locked page', async () => {
  const { result } = renderHook(() => useCollaborationScope('group', 'current=human_test&group=g'));
  await waitFor(() => expect(result.current.scope).not.toBeNull());
  act(() => useWorkspaceStore.getState().setActiveIdentityId('other'));
  expect(result.current.scope).toBeNull();
  expect(result.current.error).toContain('身份');
});
