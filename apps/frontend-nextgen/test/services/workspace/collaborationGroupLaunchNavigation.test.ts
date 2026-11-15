import { navigateToLaunchedGroup } from '@/services/workspace/collaborationGroupLaunchNavigation';
import { useWorkspaceStore } from '@/stores/workspaceStore';
it('opens a fresh workspace for submitted Bot B using its group and initial session', () => {
  const assign = jest.fn();
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { assign } } });
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities(
    [
      { id: 'bot-A', kind: 'bot', displayName: 'A', online: true },
      { id: 'bot-B:owner', kind: 'bot', displayName: 'B', online: true },
    ],
    'bot-A',
  );
  useWorkspaceStore.getState().selectGroup('old-group');
  useWorkspaceStore.getState().selectSession('old-session');
  useWorkspaceStore.getState().setGroupSearchText('old-search');
  useWorkspaceStore.getState().setGroupKindFilter('free_chat');
  navigateToLaunchedGroup({ groupId: 'new/group', initialSessionId: 'session+1', driverBotId: 'bot-B:owner' });
  const url = String(assign.mock.calls[0][0]);
  expect(url.startsWith('/workspace/collaboration?')).toBe(true);
  const query = new URLSearchParams(url.split('?')[1]);
  expect(Object.fromEntries(query)).toMatchObject({ current: 'bot-B:owner', group: 'new/group', session: 'session+1' });
  Reflect.deleteProperty(globalThis, 'window');
});
