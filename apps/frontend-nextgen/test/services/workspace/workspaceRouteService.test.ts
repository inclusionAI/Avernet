import { parseWorkspaceRoute } from '@/domain/workspaceRoute';
import { hydrateWorkspaceRoute } from '@/services/workspace/workspaceRouteService';
import { useWorkspaceStore } from '@/stores/workspaceStore';

const user = { id: 'human-1', kind: 'user' as const, displayName: '我', online: true };
const bot = { id: 'bot-1', kind: 'bot' as const, displayName: 'Bot', online: true };

beforeEach(() => {
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([user, bot], user.id);
});

describe('hydrateWorkspaceRoute(协作群 hydration)', () => {
  it('falls back to the user identity when current points to an unknown identity', () => {
    useWorkspaceStore.getState().setActiveIdentity(bot.id);

    hydrateWorkspaceRoute(parseWorkspaceRoute('current=unknown&group=g1'));

    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: user.id,
      view: 'group',
      selectedGroupId: 'g1',
    });
  });

  it('hydrates a Bot identity group deep link with group, session and membership', () => {
    hydrateWorkspaceRoute(parseWorkspaceRoute('current=bot-1&group=g1&session=s1&membership=session_only'));

    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: bot.id,
      view: 'group',
      selectedGroupId: 'g1',
      selectedSessionId: 's1',
      membership: 'session_only',
      expandedGroupIds: { g1: true },
    });
  });

  it('keeps the active identity for a collaboration URL without identity or session', () => {
    useWorkspaceStore.getState().setActiveIdentity(bot.id);

    hydrateWorkspaceRoute(parseWorkspaceRoute('group=g1'));

    expect(useWorkspaceStore.getState().activeIdentityId).toBe(bot.id);
    expect(useWorkspaceStore.getState().selectedGroupId).toBe('g1');
  });

  it('passes through a non-explicit query without touching the store', () => {
    useWorkspaceStore.getState().setActiveIdentity(bot.id);

    hydrateWorkspaceRoute(parseWorkspaceRoute(''));

    expect(useWorkspaceStore.getState().activeIdentityId).toBe(bot.id);
  });

  it('prefills a group deep link while identities are still loading', () => {
    useWorkspaceStore.getState().reset();

    const result = hydrateWorkspaceRoute(parseWorkspaceRoute('group=g1&session=s1'));

    expect(result.status).toBe('waiting_for_identities');
    expect(useWorkspaceStore.getState()).toMatchObject({ selectedGroupId: 'g1', selectedSessionId: 's1' });
  });

  it('maps a session-only deep link to an unresolved group session when the group is missing', () => {
    hydrateWorkspaceRoute(parseWorkspaceRoute('session=orphan-session'));

    const state = useWorkspaceStore.getState();
    expect(state.activeIdentityId).toBe(user.id);
    expect(state.selectedGroupId).toBeNull();
  });
});
