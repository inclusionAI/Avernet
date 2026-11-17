/** @jest-environment jsdom */
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { invitationService } from '@/services/workspace/invitationService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { sessionService } from '@/services/workspace/sessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/invitationService');
jest.mock('@/services/workspace/sessionService');
const preview = jest.mocked(invitationService.getAcceptPageState);
const accept = jest.mocked(invitationService.acceptInvitation);
const detail = jest.mocked(sessionService.getSessionDetail);

const checked = (alreadyJoined: boolean, targetType: 'group' | 'session' = 'group') => ({
  ok: true as const,
  data: { isValid: true as const, targetType, targetId: targetType === 'group' ? 'g1' : 's1', groupId: 'g1', alreadyJoined },
});

beforeEach(() => {
  jest.resetAllMocks();
  useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-1', displayName: '我', provider: 'github' });
});

it.each(['group', 'session'] as const)('preflight %s member is accepted without POST or detail hydration', async (type) => {
  preview.mockResolvedValueOnce(checked(true, type));
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  expect(result.current).toMatchObject({ targetType: type, groupId: 'g1', alreadyJoined: true });
  expect(result.current.sessionId).toBe(type === 'session' ? 's1' : undefined);
  expect(accept).not.toHaveBeenCalled();
  expect(detail).not.toHaveBeenCalled();
});

it('preflight nonmember waits for explicit confirmation', async () => {
  preview.mockResolvedValueOnce(checked(false));
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('confirm'));
  expect(accept).not.toHaveBeenCalled();
});

it('transient failure does not confirm and retry repeats only the preview', async () => {
  preview.mockResolvedValueOnce({ ok: false, error: { code: 'INVITATION_PREVIEW_FAILED', friendlyMessage: '稍后重试', canRetry: true } });
  preview.mockResolvedValueOnce(checked(true));
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('preview-error'));
  await act(async () => result.current.retryPreview());
  await waitFor(() => expect(result.current.status).toBe('accepted'));
  expect(preview).toHaveBeenCalledTimes(2);
  expect(accept).not.toHaveBeenCalled();
});

it('authentication failure is not labelled as an invalid or unchecked invitation', async () => {
  preview.mockResolvedValueOnce({ ok: false, error: { code: 'INVITATION_UNAUTHENTICATED', friendlyMessage: '请登录', canRetry: false } });
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('preview-error'));
  expect(result.current).toMatchObject({ friendlyMessage: '请登录', canRetry: false });
  expect(accept).not.toHaveBeenCalled();
});

it('expired preview remains invalid', async () => {
  preview.mockResolvedValueOnce({ ok: false, error: { code: 'INVITATION_INVALID', friendlyMessage: '已失效', canRetry: false } });
  const { result } = renderHook(() => useInviteAccept('tk'));
  await waitFor(() => expect(result.current.status).toBe('invalid'));
});

it('a late preview for the previous token cannot replace the current target state', async () => {
  let resolveOld!: (value: ReturnType<typeof checked>) => void;
  preview.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
  preview.mockResolvedValueOnce(checked(false, 'session'));
  const { result, rerender } = renderHook(({ token }) => useInviteAccept(token), { initialProps: { token: 'old' } });
  rerender({ token: 'new' });
  await waitFor(() => expect(result.current.status).toBe('confirm'));
  await act(async () => resolveOld(checked(true)));
  expect(result.current.status).toBe('confirm');
  expect(accept).not.toHaveBeenCalled();
});

it('a late acceptance for the previous token cannot navigate the new invitation', async () => {
  preview.mockResolvedValue(checked(false));
  let resolveAccept!: (value: Awaited<ReturnType<typeof invitationService.acceptInvitation>>) => void;
  accept.mockReturnValueOnce(new Promise((resolve) => { resolveAccept = resolve; }));
  const { result, rerender } = renderHook(({ token }) => useInviteAccept(token), { initialProps: { token: 'old' } });
  await waitFor(() => expect(result.current.status).toBe('confirm'));
  let accepting!: Promise<void>;
  act(() => { accepting = result.current.accept(); });
  rerender({ token: 'new' });
  await waitFor(() => expect(result.current.status).toBe('confirm'));
  await act(async () => {
    resolveAccept({ ok: true, data: { targetType: 'group', targetId: 'old-group', alreadyJoined: false } });
    await accepting;
  });
  expect(result.current.status).toBe('confirm');
  expect(result.current.targetId).not.toBe('old-group');
});


it('a changed authenticated Human rechecks membership and rejects the previous User response', async () => {
  let resolveOld!: (value: ReturnType<typeof checked>) => void;
  preview.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
  preview.mockResolvedValueOnce(checked(false));
  const { result } = renderHook(() => useInviteAccept('tk'));
  act(() => useExternalAuthStore.getState().setAuthenticated({ userId: 'staff-2', displayName: '另一用户', provider: 'github' }));
  await waitFor(() => expect(result.current.status).toBe('confirm'));
  await act(async () => resolveOld(checked(true)));
  expect(result.current).toMatchObject({ status: 'confirm', currentIdentityId: 'human_staff-2' });
  expect(preview).toHaveBeenCalledTimes(2);
  expect(accept).not.toHaveBeenCalled();
});
