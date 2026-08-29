/** @jest-environment jsdom */
import { useOpenDefaultGroupSession } from '@/pages/Workspace/hooks/useOpenDefaultGroupSession';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/sessionService');

const ss = sessionService as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
  useWorkspaceStore.getState().selectGroup(null);
  useWorkspaceStore.getState().selectSession(null);
});

it('opens the first default session of the created group', async () => {
  ss.loadSessionsByIdsOrBcs.mockResolvedValue([{ sessionId: 's-default' }, { sessionId: 's-second' }]);
  const { result } = renderHook(() => useOpenDefaultGroupSession());

  await act(async () => {
    await result.current('g-new');
  });

  expect(ss.loadSessionsByIdsOrBcs).toHaveBeenCalledWith('g-new', 0);
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g-new');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-default');
  expect(useWorkspaceStore.getState().expandedGroupIds['g-new']).toBe(true);
});
