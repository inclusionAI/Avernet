import { parseWorkspaceRoute, serializeWorkspaceRoute, serializeWorkspaceRoutePreserving } from '@/domain/workspaceRoute';

describe('workspaceRoute(协作群 URL 契约)', () => {
  it('parses current as identity and group/session/membership as selection', () => {
    expect(parseWorkspaceRoute('current=human-1&group=g1&session=s1&membership=session_only')).toMatchObject({
      explicit: true,
      currentIdentityId: 'human-1',
      legacyGroupIdentityId: undefined,
      groupId: 'g1',
      sessionId: 's1',
      membership: 'session_only',
    });
  });

  it('parses legacy group bot parameter as identity', () => {
    expect(parseWorkspaceRoute('bot=legacy-bot&group=g1&session=s1')).toMatchObject({
      explicit: true,
      currentIdentityId: undefined,
      legacyGroupIdentityId: 'legacy-bot',
      groupId: 'g1',
      sessionId: 's1',
      membership: undefined,
    });
  });

  it('normalizes invalid membership values', () => {
    expect(parseWorkspaceRoute('group=g1&membership=everything')).toMatchObject({
      explicit: true,
      groupId: 'g1',
      membership: undefined,
    });
  });

  it('is not explicit for queries without collaboration keys', () => {
    expect(parseWorkspaceRoute('')).toMatchObject({ explicit: false, groupId: undefined });
    expect(parseWorkspaceRoute('utm_source=share')).toMatchObject({ explicit: false });
  });

  it('serializes group identity, selection and membership in one canonical order without tab', () => {
    expect(
      serializeWorkspaceRoute({
        currentIdentityId: 'bot-1',
        groupId: 'g1',
        sessionId: 's1',
        membership: 'session_only',
      }),
    ).toBe('current=bot-1&group=g1&session=s1&membership=session_only');
  });

  it('serializes a group deep link without identity', () => {
    expect(serializeWorkspaceRoute({ groupId: 'g1', sessionId: 's1' })).toBe('group=g1&session=s1');
    expect(serializeWorkspaceRoute({})).toBe('');
  });

  it('preserves non-route params such as showReturn while projecting', () => {
    expect(
      serializeWorkspaceRoutePreserving('current=human-1&group=g1&session=s1&showReturn=false', {
        currentIdentityId: 'human-1',
        groupId: 'g1',
        sessionId: 's1',
      }),
    ).toBe('current=human-1&group=g1&session=s1&showReturn=false');
  });

  it('drops route keys removed by projection but keeps extras', () => {
    expect(
      serializeWorkspaceRoutePreserving('group=g1&session=s1&showReturn=false&bot=legacy', {
        groupId: 'g1',
      }),
    ).toBe('group=g1&showReturn=false');
  });

  it('serializes extras only when projection is empty and nothing otherwise', () => {
    expect(serializeWorkspaceRoutePreserving('showReturn=false', {})).toBe('showReturn=false');
    expect(serializeWorkspaceRoutePreserving('group=g1', { groupId: 'g1' })).toBe('group=g1');
  });
});
