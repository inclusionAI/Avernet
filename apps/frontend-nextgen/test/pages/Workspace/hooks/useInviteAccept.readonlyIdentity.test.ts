/** @jest-environment jsdom */
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { invitationService } from '@/services/workspace/invitationService';
import { identityService } from '@/services/workspace/identityService';
import { listMyBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/invitationService');
jest.mock('@/services/workspace/sessionService');
jest.mock('@/services/backendApi/collaboration/collaborationBotController');
jest.mock('@/services/workspace/identityService');

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(identityService.loadIdentities).mockResolvedValue({ ok: false, error: { code: 'FAIL', friendlyMessage: 'not loaded', canRetry: true } });
  jest.mocked(identityService.isIdentityLoading).mockReturnValue(false);
  jest.mocked(identityService.isIdentityResolved).mockReturnValue(false);
  useWorkspaceStore.getState().reset();
  useExternalAuthStore.getState().reset();
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValue({ ok: true, data: {
    isValid: true, targetType: 'group', targetId: 'g1', groupId: 'g1', alreadyJoined: true,
  } });
});

it('cold auth/identity initialization never triggers the write-capable mine/identity lookup', async () => {
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  expect(result.current).toMatchObject({ humanIdentityStatus: 'loading', currentIdentityId: undefined });
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});

it('late global auth user updates the invitation identity without a mine lookup', async () => {
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  act(() => useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' }));
  await waitFor(() => expect(result.current).toMatchObject({
    status: 'accepted', currentIdentityId: 'human_staff-1', humanIdentityStatus: 'ready',
  }));
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});

it('stable authentication failure is not an indefinitely loading identity', async () => {
  useExternalAuthStore.getState().setUnauthenticated();
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  expect(result.current.humanIdentityStatus).toBe('error');
  expect(result.current.currentIdentityId).toBeUndefined();
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
});

it('an authenticated store containing only a Bot cannot supply the invitation Human', async () => {
  useWorkspaceStore.getState().setIdentities([{ id: 'bot-1', kind: 'bot', displayName: 'Bot', online: true }], 'bot-1');
  useExternalAuthStore.setState({ status: 'authenticated', user: null });
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  expect(result.current).toMatchObject({ humanIdentityStatus: 'error', currentIdentityId: undefined });
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
});

it.each(['logout', 'error'] as const)('OAuth %s invalidates a pending member result despite a cached workspace Human', async (transition) => {
  useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' });
  useWorkspaceStore.getState().setIdentities([{ id: 'human_staff-1', kind: 'user', displayName: '我', online: true }], 'human_staff-1');
  let resolveOld!: (value: Awaited<ReturnType<typeof invitationService.getAcceptPageState>>) => void;
  jest.mocked(invitationService.getAcceptPageState).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
  jest.mocked(invitationService.getAcceptPageState).mockResolvedValueOnce({
    ok: false, error: { code: 'INVITATION_UNAUTHENTICATED', friendlyMessage: '请登录', canRetry: false },
  });
  const { result } = renderHook(() => useInviteAccept('tk'));
  act(() => {
    if (transition === 'logout') useExternalAuthStore.getState().setUnauthenticated();
    else useExternalAuthStore.getState().setAuthError('expired');
  });
  await waitFor(() => expect(result.current.status).toBe('preview-error'));
  await act(async () => resolveOld({ ok: true, data: {
    isValid: true, targetType: 'group', targetId: 'g1', groupId: 'g1', alreadyJoined: true,
  } }));
  expect(result.current).toMatchObject({ status: 'preview-error', currentIdentityId: undefined, humanIdentityStatus: 'error' });
  expect(identityService.loadIdentities).not.toHaveBeenCalled();
  expect(listMyBots).not.toHaveBeenCalled();
  expect(invitationService.acceptInvitation).not.toHaveBeenCalled();
});
