/** @jest-environment jsdom */
import { GroupSidebar } from '@/pages/Workspace/components/GroupSidebar';
import { WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY } from '@/pages/Workspace/components/workspaceSidebarSizing';
import { useGroupWorkspace } from '@/pages/Workspace/hooks/useGroupWorkspace';
import { useGroupSessions } from '@/pages/Workspace/hooks/useGroupSessions';
import { useGroupSessionLocation } from '@/pages/Workspace/hooks/useGroupSessionLocation';
import { useWorkspacePage } from '@/pages/Workspace/hooks/useWorkspacePage';
import { groupService } from '@/services/workspace/groupService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history, useSearchParams } from '@umijs/max';
import { act, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import '@testing-library/jest-dom';
import { baseGroup, makeProps, setupResizeObserverMock } from './components/GroupSidebar/GroupSidebar.testUtils';

jest.mock('@umijs/max', () => ({ history: { replace: jest.fn() }, useSearchParams: jest.fn() }));
jest.mock('@/services/workspace/groupService');
jest.mock('@/services/workspace/sessionService');
const human = { id: 'human_staff-1', kind: 'user' as const, displayName: '我', online: true };
const target = { ...baseGroup.sessions[0], sessionId: 'off-page', title: '邀请目标会话' };
const targetGroup = { ...baseGroup, membership: 'session_only' as const };
const mounted = jest.fn();
const unmounted = jest.fn();

function Area({ request }: { request: ReturnType<typeof useWorkspacePage>['sessionLocationRequest'] }) {
  const ws = useGroupWorkspace();
  const sessions = useGroupSessions(ws.selectedGroupId, Object.keys(ws.expandedGroupIds));
  const sessionTabsByGroup = useWorkspaceStore((s) => s.sessionTabsByGroup);
  const location = useGroupSessionLocation(ws.selectedGroup, sessions.selectedSession, undefined, request, Boolean(ws.selectedGroupError));
  useEffect(() => { mounted(); return () => { unmounted(); }; }, []);
  return <GroupSidebar {...makeProps({
    groups: ws.groups, isLoading: ws.isLoadingGroups,
    selectedGroupId: ws.selectedGroupId, selectedSessionId: sessions.selectedSessionId,
    expandedGroupIds: ws.expandedGroupIds, sessionsByGroupId: sessions.sessionsByGroupId,
    sessionTabsByGroup, favoriteSessionIds: sessions.favoriteSessionIds,
    hasMoreSessionsByGroupId: sessions.hasMoreSessionsByGroupId, totalSessionsByGroupId: sessions.totalSessionsByGroupId,
    sessionSearchText: sessions.sessionSearchText,
    location: location.location, onDismissLocation: location.dismissLocation,
  })} />;
}
function Page() {
  const route = useWorkspacePage();
  return route.urlHydrated ? <Area request={route.sessionLocationRequest} /> : <div>等待身份</div>;
}

beforeEach(() => {
  jest.clearAllMocks();
  setupResizeObserverMock();
  localStorage.clear();
  localStorage.setItem(WORKSPACE_SIDEBAR_COLLAPSED_STORAGE_KEY, 'true');
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([human], human.id);
  useWorkspaceStore.setState({ groupSearchText: '旧群搜索', sessionSearchText: '旧会话搜索',
    groupKindFilter: 'task_dag', sessionTabsByGroup: { g1: 'favorite' }, historyRefreshNonce: 10 });
  window.history.replaceState({}, '', '/workspace/collaboration?current=human_staff-1&group=g1&session=off-page');
  jest.mocked(useSearchParams).mockImplementation(() => [new URLSearchParams(window.location.search), jest.fn()]);
  jest.mocked(history.replace).mockImplementation((path) => { window.history.replaceState({}, '', String(path)); });
  jest.mocked(groupService.loadGroups).mockResolvedValue({ ok: true, data: [] });
  jest.mocked(groupService.loadGroupDetailOrBcs).mockResolvedValue({ ok: true, data: targetGroup });
  jest.mocked(groupService.getVisibleGroups).mockImplementation((groups) => groups);
  jest.mocked(groupService.canManageGroup).mockReturnValue({ allowed: false });
  jest.mocked(groupService.canDissolveGroup).mockReturnValue({ allowed: false });
  jest.mocked(groupService.loadGroupSessionsOrBcs).mockResolvedValue({ ok: true, data: { items: [baseGroup.sessions[1]], offset: 0, limit: 20, total: 35, hasMore: true } });
  jest.mocked(sessionService.getSessionDetail).mockResolvedValue({ ok: true, data: target });
  jest.mocked(sessionService.getVisibleSessions).mockImplementation((sessions, opts) =>
    opts.search ? sessions.filter((session) => session.title.includes(opts.search)) : sessions);
});

it('opens and scrolls a collapsed sidebar to an off-page session whose parent is absent from the direct-membership list', async () => {
  const scroll = jest.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  let finishGroup!: (value: Awaited<ReturnType<typeof groupService.loadGroupDetailOrBcs>>) => void;
  const pendingGroup = new Promise<Awaited<ReturnType<typeof groupService.loadGroupDetailOrBcs>>>((resolve) => { finishGroup = resolve; });
  jest.mocked(groupService.loadGroupDetailOrBcs).mockReturnValue(pendingGroup);
  const view = render(<Page />);
  // Let the pre-existing debounced search settle before the target details arrive.
  await waitFor(() => expect(groupService.loadGroups).toHaveBeenCalledWith(human,
    expect.objectContaining({ q: '旧群搜索' })));
  await act(async () => finishGroup({ ok: true, data: targetGroup }));
  const row = await screen.findByRole('button', { name: target.title });
  await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
  expect(document.activeElement).toBe(row);
  expect(screen.getByLabelText('协作群列表')).toHaveAttribute('data-collapsed', 'false');
  expect(useWorkspaceStore.getState()).toMatchObject({ activeIdentityId: human.id, membership: 'direct',
    selectedGroupId: 'g1', selectedSessionId: target.sessionId, groupSearchText: '', groupKindFilter: 'all',
    sessionSearchText: '', sessionTabsByGroup: { g1: 'all' }, historyRefreshNonce: 11 });
  expect(groupService.loadGroups).toHaveBeenCalledWith(human, expect.objectContaining({ membership: 'direct' }));
  // Canonical query updates must not remount the data/history hooks or replay location.
  view.rerender(<Page />);
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(unmounted).not.toHaveBeenCalled();
  expect(scroll).toHaveBeenCalledTimes(1);
  // Clearing the old search reloads direct-membership groups and temporarily clears detail props.
  // The verified overlay must survive that refresh even though its parent still is not listed.
  await waitFor(() => expect(jest.mocked(groupService.loadGroups).mock.calls.filter((call) => !call[1]?.q).length).toBeGreaterThan(1));
  expect(screen.getByRole('button', { name: target.title })).toBeInTheDocument();
  expect(scroll).toHaveBeenCalledTimes(1);
  const historyNonce = useWorkspaceStore.getState().historyRefreshNonce;
  act(() => useWorkspaceStore.getState().setSessionSearchText('用户的新筛选'));
  view.rerender(<Page />);
  expect(useWorkspaceStore.getState().sessionSearchText).toBe('用户的新筛选');
  expect(useWorkspaceStore.getState().historyRefreshNonce).toBe(historyNonce);
  expect(scroll).toHaveBeenCalledTimes(1);
});
