/** @jest-environment jsdom */
import type { GroupView, SessionView } from '@/domain/collaboration';
import { useGroupSessionLocation } from '@/pages/Workspace/hooks/useGroupSessionLocation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { baseGroup } from '../components/GroupSidebar/GroupSidebar.testUtils';

const target = { request: 1, identityId: 'human-1', groupId: 'g1', sessionId: 's1' };
beforeEach(() => {
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId: target.identityId, selectedGroupId: 'g1', selectedSessionId: 's1',
    groupSearchText: 'old', groupKindFilter: 'task_dag', sessionSearchText: 'old', sessionTabsByGroup: { g1: 'favorite' },
    membership: 'direct', expandedGroupIds: { g1: true }, historyRefreshNonce: 8 });
});

it('waits for verified group and session then recovers sidebar visibility exactly once without changing membership, selection or history', () => {
  const { result, rerender } = renderHook(({ group, session }: { group: GroupView | null; session: SessionView | null }) =>
    useGroupSessionLocation(group, session, undefined, target), { initialProps: { group: null as GroupView | null, session: null as SessionView | null } });
  expect(result.current.location).toBeUndefined();
  expect(useWorkspaceStore.getState().groupSearchText).toBe('old');
  rerender({ group: { ...baseGroup, membership: 'session_only' }, session: baseGroup.sessions[0] });
  expect(result.current.location?.session.sessionId).toBe('s1');
  const first = result.current.location?.request;
  expect(useWorkspaceStore.getState()).toMatchObject({ groupSearchText: '', groupKindFilter: 'all', sessionSearchText: '',
    sessionTabsByGroup: { g1: 'all' }, membership: 'direct', selectedGroupId: 'g1', selectedSessionId: 's1',
    expandedGroupIds: { g1: true }, historyRefreshNonce: 8 });
  rerender({ group: { ...baseGroup }, session: { ...baseGroup.sessions[0] } });
  expect(result.current.location?.request).toBe(first);
  act(() => result.current.dismissLocation());
  rerender({ group: { ...baseGroup }, session: { ...baseGroup.sessions[0] } });
  expect(result.current.location).toBeUndefined();
});

it('does not erase user filters or replay a consumed deep-link request on data refresh', () => {
  const { result, rerender } = renderHook(() => useGroupSessionLocation(baseGroup, baseGroup.sessions[0], undefined, target));
  expect(result.current.location).toBeDefined();
  act(() => useWorkspaceStore.getState().setSessionSearchText('new search'));
  rerender();
  expect(result.current.location).toBeUndefined();
  expect(useWorkspaceStore.getState().sessionSearchText).toBe('new search');
});

it.each(['identity', 'group', 'session'] as const)('cancels a pending target when the user changes %s before details arrive', (change) => {
  const { result, rerender } = renderHook(({ group, session }: { group: GroupView | null; session: SessionView | null }) =>
    useGroupSessionLocation(group, session, undefined, target), { initialProps: { group: null as GroupView | null, session: null as SessionView | null } });
  act(() => useWorkspaceStore.setState(change === 'identity' ? { activeIdentityId: 'human-2' }
    : change === 'group' ? { selectedGroupId: 'g2' } : { selectedSessionId: 's2' }));
  rerender({ group: baseGroup, session: baseGroup.sessions[0] });
  expect(result.current.location).toBeUndefined();
  act(() => useWorkspaceStore.setState({ activeIdentityId: target.identityId, selectedGroupId: 'g1', selectedSessionId: 's1' }));
  rerender({ group: baseGroup, session: baseGroup.sessions[0] });
  expect(result.current.location).toBeUndefined();
  expect(useWorkspaceStore.getState().groupSearchText).toBe('old');
});

it('keeps the verified location through transient detail loading after clearing the old search', () => {
  const { result, rerender } = renderHook(({ group, session }: { group: GroupView | null; session: SessionView | null }) =>
    useGroupSessionLocation(group, session, undefined, target), {
      initialProps: { group: baseGroup as GroupView | null, session: baseGroup.sessions[0] as SessionView | null },
    });
  const request = result.current.location?.request;
  expect(request).toBeDefined();
  rerender({ group: null, session: null });
  expect(result.current.location?.request).toBe(request);
  rerender({ group: { ...baseGroup }, session: { ...baseGroup.sessions[0] } });
  expect(result.current.location?.request).toBe(request);
});

it.each(['groupSearch', 'kind', 'sessionSearch', 'favorite'] as const)('cancels pending location instead of clearing a newer user %s filter', (filter) => {
  const { result, rerender } = renderHook(({ ready }) => useGroupSessionLocation(
    ready ? baseGroup : null, ready ? baseGroup.sessions[0] : null, undefined, target,
  ), { initialProps: { ready: false } });
  act(() => {
    const store = useWorkspaceStore.getState();
    if (filter === 'groupSearch') store.setGroupSearchText('new group search');
    if (filter === 'kind') store.setGroupKindFilter('all');
    if (filter === 'sessionSearch') store.setSessionSearchText('new session search');
    if (filter === 'favorite') store.setSessionTabForGroup('g1', 'all');
  });
  const before = useWorkspaceStore.getState();
  rerender({ ready: true });
  expect(result.current.location).toBeUndefined();
  expect(useWorkspaceStore.getState()).toBe(before);
});

it('explicit dismissal also cancels a pending route request', () => {
  const { result, rerender } = renderHook(({ ready }) => useGroupSessionLocation(
    ready ? baseGroup : null, ready ? baseGroup.sessions[0] : null, undefined, target,
  ), { initialProps: { ready: false } });
  act(() => result.current.dismissLocation());
  rerender({ ready: true });
  expect(result.current.location).toBeUndefined();
});

it('drops the cached overlay on terminal verification failure and does not resurrect it on retry', () => {
  const { result, rerender } = renderHook(({ unavailable }) => useGroupSessionLocation(
    unavailable ? null : baseGroup, unavailable ? null : baseGroup.sessions[0], undefined, target, unavailable,
  ), { initialProps: { unavailable: false } });
  expect(result.current.location).toBeDefined();
  rerender({ unavailable: true });
  expect(result.current.location).toBeUndefined();
  rerender({ unavailable: false });
  expect(result.current.location).toBeUndefined();
});
