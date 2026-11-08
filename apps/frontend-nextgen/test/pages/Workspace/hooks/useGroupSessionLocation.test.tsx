/** @jest-environment jsdom */
import { useGroupSessionLocation } from '@/pages/Workspace/hooks/useGroupSessionLocation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { act, renderHook } from '@testing-library/react';
import { baseGroup } from '../components/GroupSidebar/GroupSidebar.testUtils';

beforeEach(() => useWorkspaceStore.getState().reset());

it('定位清除筛选并请求 UI 展开，保留 session、展开缓存与历史 nonce，支持重复定位', () => {
  useWorkspaceStore.setState({
    selectedGroupId: 'g1',
    selectedSessionId: 's2',
    activeIdentityId: 'human-1',
    groupSearchText: '别的群',
    groupKindFilter: 'task_dag',
    sessionSearchText: '无匹配',
    sessionTabsByGroup: { g1: 'favorite' },
    expandedGroupIds: { other: true },
    historyRefreshNonce: 9,
  });
  const openMobile = jest.fn();
  const { result } = renderHook(() => useGroupSessionLocation(baseGroup, baseGroup.sessions[1], openMobile));
  act(() => result.current.locate());
  expect(useWorkspaceStore.getState()).toMatchObject({
    selectedGroupId: 'g1',
    selectedSessionId: 's2',
    groupSearchText: '',
    groupKindFilter: 'all',
    sessionSearchText: '',
    sessionTabsByGroup: { g1: 'all' },
    expandedGroupIds: { other: true },
    historyRefreshNonce: 9,
  });
  const firstRequest = result.current.location?.request;
  act(() => result.current.locate());
  expect(result.current.location?.request).not.toBe(firstRequest);
  expect(openMobile).not.toHaveBeenCalled();
});

it('移动端定位打开会话抽屉，切换身份后丢弃定位目标', () => {
  window.matchMedia = jest.fn().mockReturnValue({ matches: true });
  useWorkspaceStore.setState({ activeIdentityId: 'human-1' });
  const openMobile = jest.fn();
  const { result } = renderHook(() => useGroupSessionLocation(baseGroup, baseGroup.sessions[0], openMobile));
  act(() => result.current.locate());
  expect(openMobile).toHaveBeenCalledTimes(1);
  act(() => useWorkspaceStore.setState({ activeIdentityId: 'human-2' }));
  expect(result.current.location).toBeUndefined();
  delete (window as Partial<Window>).matchMedia;
});

it('未选中会话或过渡帧群与会话不匹配时定位不改变侧栏', () => {
  const before = useWorkspaceStore.getState();
  const { result, rerender } = renderHook(({ session }) => useGroupSessionLocation(baseGroup, session), {
    initialProps: { session: null as (typeof baseGroup.sessions)[0] | null },
  });
  act(() => result.current.locate());
  rerender({ session: { ...baseGroup.sessions[0], groupId: 'other-group' } });
  act(() => result.current.locate());
  expect(result.current.location).toBeUndefined();
  expect(useWorkspaceStore.getState()).toBe(before);
});

it.each(['groupSearch', 'kind', 'sessionSearch', 'favorite'] as const)(
  '定位后修改 %s 筛选会永久取消旧定位',
  (filter) => {
    useWorkspaceStore.setState({ activeIdentityId: 'human-1' });
    const { result } = renderHook(() => useGroupSessionLocation(baseGroup, baseGroup.sessions[0]));
    act(() => result.current.locate());
    expect(result.current.location).toBeDefined();
    act(() => {
      const store = useWorkspaceStore.getState();
      if (filter === 'groupSearch') store.setGroupSearchText('其他群');
      if (filter === 'kind') store.setGroupKindFilter('task_dag');
      if (filter === 'sessionSearch') store.setSessionSearchText('其他会话');
      if (filter === 'favorite') store.setSessionTabForGroup('g1', 'favorite');
    });
    expect(result.current.location).toBeUndefined();
    act(() => {
      const store = useWorkspaceStore.getState();
      store.setGroupSearchText('');
      store.setGroupKindFilter('all');
      store.setSessionSearchText('');
      store.setSessionTabForGroup('g1', 'all');
    });
    expect(result.current.location).toBeUndefined();
  },
);

it('显式取消在共享父状态中生效，再次定位使用新请求而不是重放旧请求', () => {
  const { result } = renderHook(() => useGroupSessionLocation(baseGroup, baseGroup.sessions[0]));
  act(() => result.current.locate());
  const previousRequest = result.current.location?.request;
  act(() => result.current.dismissLocation());
  expect(result.current.location).toBeUndefined();
  act(() => result.current.locate());
  expect(result.current.location?.request).toBeGreaterThan(previousRequest!);
});
