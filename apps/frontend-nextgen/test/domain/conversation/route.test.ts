import { parseConversationRoute, serializeConversationRoute } from '@/domain/conversation';
import { describe, expect, it } from '@jest/globals';

describe('Conversation route contract', () => {
  describe('serialize', () => {
    it('serializes managed mine with favorite scope', () => {
      expect(
        serializeConversationRoute({ section: 'managed', botId: 'bot-a:2088', origin: 'mine', scope: 'favorite' }),
      ).toBe('section=managed&bot=bot-a%3A2088&origin=mine&scope=favorite');
    });

    it('serializes others with friend and no scope', () => {
      expect(
        serializeConversationRoute({
          section: 'managed',
          botId: 'bot-a:2088',
          origin: 'others',
          scope: 'favorite',
          friendUserId: '447147',
          sessionId: 's1',
        }),
      ).toBe('section=managed&bot=bot-a%3A2088&origin=others&friend=447147&session=s1');
    });

    it('serializes managed mine default scope with session', () => {
      expect(
        serializeConversationRoute({
          section: 'managed',
          botId: 'bot-a:2088',
          origin: 'mine',
          scope: 'all',
          sessionId: 's1',
        }),
      ).toBe('section=managed&bot=bot-a%3A2088&origin=mine&session=s1');
    });

    it('serializes friend Bot section and drops managed-only fields', () => {
      expect(
        serializeConversationRoute({
          section: 'friend',
          botId: 'friend-bot:1',
          origin: 'others',
          scope: 'favorite',
          friendUserId: '447147',
          sessionId: 's2',
        }),
      ).toBe('section=friend&bot=friend-bot%3A1&session=s2');
    });

    it('defaults missing origin on managed section to mine', () => {
      expect(serializeConversationRoute({ section: 'managed', botId: 'bot-a:2088' })).toBe(
        'section=managed&bot=bot-a%3A2088&origin=mine',
      );
    });

    it('round-trips a managed others route', () => {
      const route = parseConversationRoute('section=managed&bot=bot-a%3A2088&origin=others&friend=447147&session=s1');
      expect(serializeConversationRoute(route)).toBe(
        'section=managed&bot=bot-a%3A2088&origin=others&friend=447147&session=s1',
      );
    });
  });

  describe('parse', () => {
    it('parses managed mine with scope', () => {
      expect(parseConversationRoute('section=managed&bot=bot-a%3A2088&origin=mine&scope=favorite')).toEqual({
        section: 'managed',
        botId: 'bot-a:2088',
        origin: 'mine',
        scope: 'favorite',
      });
    });

    it('keeps friend Bot section distinct from managed Bot', () => {
      expect(parseConversationRoute('section=friend&bot=bot-a%3A2088&session=s1')).toEqual({
        section: 'friend',
        botId: 'bot-a:2088',
        sessionId: 's1',
      });
    });

    it('drops scope when origin is others', () => {
      expect(
        parseConversationRoute('section=managed&bot=bot-a%3A2088&origin=others&scope=favorite&friend=447147'),
      ).toEqual({
        section: 'managed',
        botId: 'bot-a:2088',
        origin: 'others',
        friendUserId: '447147',
      });
    });

    it('friend section removes origin, scope and friend params', () => {
      expect(
        parseConversationRoute('section=friend&bot=bot-a%3A2088&origin=others&scope=favorite&friend=447147&session=s1'),
      ).toEqual({
        section: 'friend',
        botId: 'bot-a:2088',
        sessionId: 's1',
      });
    });

    it('normalizes empty strings to undefined', () => {
      expect(parseConversationRoute('section=managed&bot=&origin=&scope=&friend=&session=')).toEqual({
        section: 'managed',
      });
    });

    it('keeps missing section unresolved for directory matching', () => {
      expect(parseConversationRoute('bot=bot-a%3A2088&session=s1')).toEqual({
        botId: 'bot-a:2088',
        sessionId: 's1',
      });
    });

    it('drops unknown enum values', () => {
      expect(parseConversationRoute('section=impostor&bot=bot-a&origin=weird&scope=none')).toEqual({
        botId: 'bot-a',
      });
    });
  });

  describe('legacy share link id alias', () => {
    it('parses legacy id param as botId when bot param is absent', () => {
      expect(parseConversationRoute('id=20260407_jmg31orw%3A152819')).toEqual({
        botId: '20260407_jmg31orw:152819',
      });
    });

    it('prefers bot param over legacy id alias', () => {
      expect(parseConversationRoute('bot=bot-a%3A1&id=20260407_jmg31orw%3A152819')).toEqual({
        botId: 'bot-a:1',
      });
    });

    it('ignores legacy id without composite separator', () => {
      expect(parseConversationRoute('id=not-a-composite-id')).toEqual({});
    });

    it('round-trips legacy id alias into canonical bot param', () => {
      const route = parseConversationRoute('id=20260407_jmg31orw%3A152819');
      expect(serializeConversationRoute(route)).toBe('bot=20260407_jmg31orw%3A152819');
    });

    it('ignores empty legacy id', () => {
      expect(parseConversationRoute('id=')).toEqual({});
    });
  });
});
