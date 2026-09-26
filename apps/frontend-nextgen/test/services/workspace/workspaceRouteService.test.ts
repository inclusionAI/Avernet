import { parseWorkspaceRoute } from '@/domain/workspaceRoute';
import { hydrateWorkspaceRoute } from '@/services/workspace/workspaceRouteService';
import { useWorkspaceStore } from '@/stores/workspaceStore';

const user = { id: 'human-1', kind: 'user' as const, displayName: '我', online: true };
const bot = { id: 'bot-1', kind: 'bot' as const, displayName: 'Bot', online: true };

beforeEach(() => {
  useWorkspaceStore.getState().reset();
  useWorkspaceStore.getState().setIdentities([user, bot], user.id);
});

describe('hydrateWorkspaceRoute', () => {
  it('falls back to the user identity when current points to an unknown identity', () => {
    useWorkspaceStore.getState().setActiveIdentity(bot.id);

    hydrateWorkspaceRoute(parseWorkspaceRoute('tab=group&current=unknown&group=g1'));

    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: user.id,
      view: 'group',
      selectedGroupId: 'g1',
    });
  });

  it('hydrates a Bot identity friend Human and Session from chat URL', () => {
    hydrateWorkspaceRoute(parseWorkspaceRoute('tab=chat&current=bot-1&human=447147&session=dm1'));

    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: bot.id,
      view: 'chat',
      expandedFriendUserId: '447147',
      selectedFriendUserSessionId: 'dm1',
      expandedBotIds: {},
      selectedBotSessionId: null,
    });
  });

  it('keeps Bot and Human chat target parameters isolated by identity kind', () => {
    hydrateWorkspaceRoute(parseWorkspaceRoute('tab=chat&current=bot-1&bot=target&session=dm1'));
    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: bot.id,
      view: 'chat',
      expandedFriendUserId: null,
      selectedFriendUserSessionId: null,
      expandedBotIds: {},
      selectedBotSessionId: null,
    });

    hydrateWorkspaceRoute(parseWorkspaceRoute('tab=chat&current=human-1&human=447147&session=dm2'));
    expect(useWorkspaceStore.getState()).toMatchObject({
      activeIdentityId: user.id,
      view: 'chat',
      expandedFriendUserId: null,
      selectedFriendUserSessionId: null,
      expandedBotIds: {},
      selectedBotSessionId: null,
    });
  });

  it('keeps the active Bot for the legacy internal tab=group URL without identity or session', () => {
    useWorkspaceStore.getState().setActiveIdentity(bot.id);

    hydrateWorkspaceRoute(parseWorkspaceRoute('tab=group'));

    expect(useWorkspaceStore.getState().activeIdentityId).toBe(bot.id);
  });

  it('prefills a group deep link while identities are still loading', () => {
    useWorkspaceStore.getState().reset();

    const result = hydrateWorkspaceRoute(parseWorkspaceRoute('tab=group&group=g1&session=s1'));

    expect(result.status).toBe('waiting_for_identities');
    expect(useWorkspaceStore.getState()).toMatchObject({ selectedGroupId: 'g1', selectedSessionId: 's1' });
  });
});
