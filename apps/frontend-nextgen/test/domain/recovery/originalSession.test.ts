import { parseConversationRoute } from '@/domain/conversation/route';
import { recoveryOriginalSessionPath } from '@/domain/recovery/originalSession';
import { describe, expect, it } from '@jest/globals';

const bot = { botId: 'test-bot', ownerId: 'bot-owner' };

describe('recovery original-session links', () => {
  it.each([
    ['agent:main:session:conversation-id:user:bot-owner', 'mine', undefined],
    ['agent:main:session:conversation-id:user:another-user', 'others', 'another-user'],
    ['agent:main:cron:schedule-id:run:run-id', 'mine', undefined],
  ])('preserves the full logical session key %s through the chat router', (sessionKey, origin, friendUserId) => {
    const path = recoveryOriginalSessionPath({ ...bot, sessionKey });
    expect(path).toBeDefined();
    const url = new URL(path!, 'https://workbench.example');
    expect(url.pathname).toBe('/workspace/chat');
    expect(url.hash).toBe('');
    expect(parseConversationRoute(url.searchParams)).toEqual({
      section: 'managed',
      botId: 'test-bot:bot-owner',
      sessionId: sessionKey,
      origin,
      friendUserId,
    });
  });

  it('does not link to the Bot alone when no original session was recorded', () => {
    expect(recoveryOriginalSessionPath({ ...bot, sessionKey: '' })).toBeUndefined();
  });
});
