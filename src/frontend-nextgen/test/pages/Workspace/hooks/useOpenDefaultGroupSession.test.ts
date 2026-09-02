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
  useWorkspaceStore.getState().resetWorkspace();
  useWorkspaceStore.setState({
    identities: [
      { id: 'human_327325', kind: 'user', displayName: '我', online: true },
      { id: '20260528_udt1y38n:327325', kind: 'bot', displayName: '驾驶 Bot', online: true },
    ],
    activeIdentityId: '20260528_udt1y38n:327325',
  });
  useWorkspaceStore.getState().selectGroup(null);
  useWorkspaceStore.getState().selectSession(null);
});

it('opens the created initial session without a fallback list request', async () => {
  const { result } = renderHook(() => useOpenDefaultGroupSession());

  await act(async () => {
    await result.current('g-new', 's-initial');
  });

  expect(ss.loadSessionsByIdsOrBcs).not.toHaveBeenCalled();
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g-new');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-initial');
  expect(useWorkspaceStore.getState().activeIdentityId).toBe('20260528_udt1y38n:327325');
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

it('opens the create response initial session without another list request', async () => {
  const { result } = renderHook(() => useOpenDefaultGroupSession());

  await act(async () => {
    await result.current('g-initial', 's-initial');
  });

  expect(ss.loadSessionsByIdsOrBcs).not.toHaveBeenCalled();
  expect(useWorkspaceStore.getState().selectedGroupId).toBe('g-initial');
  expect(useWorkspaceStore.getState().selectedSessionId).toBe('s-initial');
  expect(useWorkspaceStore.getState().expandedGroupIds['g-initial']).toBe(true);
});
