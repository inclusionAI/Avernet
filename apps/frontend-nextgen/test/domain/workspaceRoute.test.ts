import { parseWorkspaceRoute, serializeWorkspaceRoute } from '@/domain/workspaceRoute';

describe('workspaceRoute', () => {
  it('parses current as identity and bot as single-chat target', () => {
    expect(parseWorkspaceRoute('tab=chat&current=human-1&bot=friend-bot&session=dm-1')).toMatchObject({
      explicit: true,
      view: 'chat',
      currentIdentityId: 'human-1',
      targetBotId: 'friend-bot',
      sessionId: 'dm-1',
      legacyGroupIdentityId: undefined,
    });
  });

  it('parses human as the Bot-identity chat target', () => {
    expect(parseWorkspaceRoute('tab=chat&current=bot-a%3A327325&human=447147&session=session-1')).toMatchObject({
      explicit: true,
      view: 'chat',
      currentIdentityId: 'bot-a:327325',
      targetBotId: undefined,
      targetHumanId: '447147',
      sessionId: 'session-1',
    });
  });

  it('serializes Bot-to-Human chat without a bot target', () => {
    expect(
      serializeWorkspaceRoute({
        view: 'chat',
        currentIdentityId: 'bot-a:327325',
        targetHumanId: '447147',
        sessionId: 'session-1',
      }),
    ).toBe('tab=chat&current=bot-a%3A327325&human=447147&session=session-1');
  });

  it('parses legacy group bot parameter as identity instead of chat target', () => {
    expect(parseWorkspaceRoute('tab=group&bot=legacy-bot&group=g1&session=s1')).toMatchObject({
      view: 'group',
      currentIdentityId: undefined,
      legacyGroupIdentityId: 'legacy-bot',
      targetBotId: undefined,
      groupId: 'g1',
      sessionId: 's1',
    });
  });

  it('serializes a selected Bot even when it has no session', () => {
    expect(serializeWorkspaceRoute({ view: 'chat', currentIdentityId: 'human-1', targetBotId: 'empty-bot' })).toBe(
      'tab=chat&current=human-1&bot=empty-bot',
    );
  });

  it('serializes group identity, selection and membership in one canonical order', () => {
    expect(
      serializeWorkspaceRoute({
        view: 'group',
        currentIdentityId: 'bot-1',
        groupId: 'g1',
        sessionId: 's1',
        membership: 'session_only',
      }),
    ).toBe('tab=group&current=bot-1&group=g1&session=s1&membership=session_only');
  });
});
