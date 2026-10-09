/** @jest-environment jsdom */
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { invitationService } from '@/services/workspace/invitationService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/invitationService');
jest.mock('@/services/workspace/sessionService');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const inv = invitationService as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.clearAllMocks();
  inv.getAcceptPageState.mockResolvedValue({ ok: true, data: { isValid: true } });
  inv.acceptInvitation.mockResolvedValue({
    ok: true,
    data: { targetType: 'group', targetId: 'g1', alreadyJoined: false },
  });
});

it('accept(scope) 透传给 invitationService.acceptInvitation', async () => {
  const { result } = renderHook(() => useInviteAccept('tk'));
  await act(async () => {
    await result.current.accept('participant');
  });
  expect(inv.acceptInvitation).toHaveBeenCalledWith('tk', 'participant');
});

it('不传 scope 时透传 undefined', async () => {
  const { result } = renderHook(() => useInviteAccept('tk'));
  await act(async () => {
    await result.current.accept();
  });
  expect(inv.acceptInvitation).toHaveBeenCalledWith('tk', undefined);
});
