/** @jest-environment jsdom */
import type { CollaborationPrivacyOverview } from '@/domain/collaborationPrivacy/types';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import * as identityModule from '@/hooks/useHumanIdentity';
import { collaborationPrivacyService } from '@/services/collaborationPrivacy';
import { useCollaborationPrivacyStore } from '@/stores/collaborationPrivacyStore';
import { afterAll, afterEach, describe, expect, it, jest } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';

const mockedUseHumanIdentity = jest.spyOn(identityModule, 'useHumanIdentity');

const overview = {
  currentUser: { displayName: '真实用户', employeeNumber: '447147', departmentPath: ['协作平台'] },
  organizationOptions: [],
  bots: [],
} as CollaborationPrivacyOverview;

afterEach(() => {
  jest.clearAllMocks();
  useCollaborationPrivacyStore.getState().reset();
});

afterAll(() => {
  jest.restoreAllMocks();
});

describe('useCollaborationPrivacy identity wiring', () => {
  it('waits for identity and passes the employee number to the overview service', async () => {
    mockedUseHumanIdentity.mockReturnValue({
      status: 'ready',
      identity: { userId: '447147', displayName: '真实用户', online: true },
    });
    const mockedLoadOverview = jest.spyOn(collaborationPrivacyService, 'loadOverview').mockResolvedValue(overview);

    renderHook(() => useCollaborationPrivacy());

    await waitFor(() => expect(mockedLoadOverview).toHaveBeenCalledWith('447147', expect.any(AbortSignal)));
  });

  it('does not call the overview service while identity is loading', () => {
    mockedUseHumanIdentity.mockReturnValue({ status: 'loading', identity: null });

    renderHook(() => useCollaborationPrivacy());

    expect(collaborationPrivacyService.loadOverview).not.toHaveBeenCalled();
    expect(useCollaborationPrivacyStore.getState().loading).toBe(true);
  });
});
