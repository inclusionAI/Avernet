/** @jest-environment jsdom */
import { useState } from 'react';
import { act, renderHook } from '@testing-library/react';
import { useGroupParticipantHydration } from '@/pages/Workspace/hooks/useGroupParticipantHydration';
import { groupService } from '@/services/workspace/groupService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { GroupView, ParticipantView } from '@/domain/collaboration';
import { scopeGroup } from '../../../mocks/collaborationScope';

jest.mock('@/services/workspace/groupService');
const load = jest.mocked(groupService.loadGroupParticipants);
function useHarness() {
  const [groups, setGroups] = useState<GroupView[]>([]);
  return { groups, setGroups, ...useGroupParticipantHydration(setGroups) };
}
beforeEach(() => {
  jest.clearAllMocks();
  useWorkspaceStore.setState({ activeIdentityId: 'b1' });
});
test('fixed scope can capture a valid request without loading the directory', () => {
  const { result } = renderHook(useHarness);
  const first = result.current.captureRequest();
  expect(result.current.isCurrentRequest(first)).toBe(true);
  useWorkspaceStore.setState({ activeIdentityId: 'b2' });
  expect(result.current.isCurrentRequest(first)).toBe(false);
  const second = result.current.captureRequest();
  expect(second.identityId).toBe('b2');
  expect(result.current.isCurrentRequest(second)).toBe(true);
});
test('present empty summary never requests group detail', async () => {
  const { result } = renderHook(useHarness);
  const request = result.current.beginListLoad('b1');
  await act(async () => {
    await result.current.enrichParticipants([
      { ...scopeGroup, participantCount: 3, participantSummaries: [] },
    ], request, 'b1');
  });
  expect(load).not.toHaveBeenCalled();
});
test('late legacy hydration cannot replace a newer summary', async () => {
  let finish!: (members: ParticipantView[]) => void;
  load.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const { result } = renderHook(useHarness);
  const old = result.current.beginListLoad('b1');
  const pending = result.current.enrichParticipants([{ ...scopeGroup, participantCount: 1 }], old, 'b1');
  result.current.beginListLoad('b1');
  act(() => result.current.setGroups([{ ...scopeGroup, participantSummaries: [] }]));
  await act(async () => {
    finish([{ actorId: 'b1', kind: 'bot', name: 'old', role: 'worker', mode: 'auto', avatarUrl: '/old.png' }]);
    await pending;
  });
  expect(result.current.groups[0].participantSummaries).toEqual([]);
  expect(result.current.groups[0].participants).toEqual([]);
});

test('legacy hydration keeps at most four detail requests in flight', async () => {
  const resolvers: Array<(members: ParticipantView[]) => void> = [];
  load.mockImplementation(() => new Promise((resolve) => { resolvers.push(resolve); }));
  const { result } = renderHook(useHarness);
  const request = result.current.beginListLoad('b1');
  const pending = result.current.enrichParticipants(
    Array.from({ length: 5 }, (_, i) => ({ ...scopeGroup, groupId: `g${i}`, participantCount: 1 })),
    request,
    'b1',
  );
  expect(load).toHaveBeenCalledTimes(4);
  await act(async () => {
    resolvers[0]([]);
    await Promise.resolve();
  });
  expect(load).toHaveBeenCalledTimes(5);
  await act(async () => {
    resolvers.slice(1).forEach((resolve) => resolve([]));
    await pending;
  });
});
