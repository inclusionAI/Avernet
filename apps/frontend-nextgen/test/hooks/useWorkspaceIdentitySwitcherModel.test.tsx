/** @jest-environment jsdom */
import { extendCapabilities, getCapabilities } from '@/capabilities';
import type { UseHumanIdentityResult } from '@/hooks/useHumanIdentity';
import { useWorkspaceIdentitySwitcherModel } from '@/hooks/useWorkspaceIdentitySwitcherModel';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { afterAll, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

const mockSwitchIdentity = jest.fn<(identityId: string) => void>();
const mockUseHumanIdentity = jest.fn<() => UseHumanIdentityResult>();
const originalGetUserProfilePresentation = getCapabilities().getUserProfilePresentation;

jest.mock('@/services/workspace/workspaceService', () => ({
  workspaceService: {
    switchIdentity: (identityId: string) => mockSwitchIdentity(identityId),
  },
}));
jest.mock('@/hooks/useHumanIdentity', () => ({
  useHumanIdentity: () => mockUseHumanIdentity(),
}));

describe('useWorkspaceIdentitySwitcherModel', () => {
  beforeEach(() => {
    extendCapabilities({
      getUserProfilePresentation: () => ({
        status: 'available',
        value: { preferAuthenticatedUserProfile: true, showDepartment: false },
      }),
    });
    useWorkspaceStore.getState().reset();
    mockSwitchIdentity.mockReset();
    mockUseHumanIdentity.mockReset().mockReturnValue({
      identity: {
        userId: 'external-user-1',
        displayName: '认证用户',
        avatarUrl: 'https://example.test/user.png',
        online: true,
      },
      status: 'ready',
    });
  });

  afterAll(() => {
    extendCapabilities({ getUserProfilePresentation: originalGetUserProfilePresentation });
  });

  it('映射当前身份并把切换委托给 workspaceService', () => {
    useWorkspaceStore.setState({
      identities: [
        { id: 'human_external-user-1', kind: 'user', displayName: '服务端名称', online: true },
        { id: 'bot-1', kind: 'bot', displayName: '协作 Bot', online: true },
      ],
      activeIdentityId: 'human_external-user-1',
      isIdentityListLoading: true,
    });

    const { result } = renderHook(() => useWorkspaceIdentitySwitcherModel());

    expect(result.current.activeIdentityId).toBe('human_external-user-1');
    expect(result.current.identityListLoading).toBe(true);
    expect(result.current.identities.map((identity) => identity.name)).toEqual(['认证用户', '协作 Bot']);

    act(() => {
      result.current.switchIdentity('bot-1');
    });

    expect(mockSwitchIdentity).toHaveBeenCalledWith('bot-1');
    expect(mockSwitchIdentity).toHaveBeenCalledTimes(1);
  });
});
