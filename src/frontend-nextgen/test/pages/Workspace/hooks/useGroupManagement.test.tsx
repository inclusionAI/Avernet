/** @jest-environment jsdom */
import { useGroupManagement } from '@/pages/Workspace/hooks/useGroupManagement';
import { channelBindingService } from '@/services/workspace/channelBindingService';
import { expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/channelBindingService');
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/groupMemberService');
jest.mock('@/services/workspace/invitationService');
jest.mock('sonner', () => ({ toast: { error: () => undefined, success: () => undefined } }));

const channelBinding = channelBindingService as unknown as Record<string, jest.Mock>;

it('does not load dingtalk binding when advanced config is disabled', () => {
  renderHook(() => useGroupManagement('group-1', jest.fn(), false));

  expect(channelBinding.loadGroupDingTalkBinding).not.toHaveBeenCalled();
});

it('loads dingtalk binding when advanced config is enabled', async () => {
  channelBinding.loadGroupDingTalkBinding.mockResolvedValue({ ok: true, data: null });

  renderHook(() => useGroupManagement('group-1', jest.fn(), true));

  await waitFor(() => expect(channelBinding.loadGroupDingTalkBinding).toHaveBeenCalledWith('group-1'));
});
