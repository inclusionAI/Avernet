import {
  getElapsedSeconds,
  isAbortedBotMessage,
  resolveAbortableBotId,
} from '@/pages/Workspace/components/GroupChatPane/groupChatAbortHelpers';
import { describe, expect, it } from '@jest/globals';

const participants = [{ actorId: 'bot-a', kind: 'bot', name: '甲', role: 'member', mode: 'auto' }] as never;

describe('groupChatAbortHelpers', () => {
  it('only accepts a streaming assistant from a current session bot participant', () => {
    expect(
      resolveAbortableBotId(
        {
          id: 'm1',
          role: 'assistant',
          content: 'x',
          status: 'streaming',
          extra: { botUuid: 'bot-a' },
        },
        participants,
      ),
    ).toBe('bot-a');
    expect(
      resolveAbortableBotId(
        {
          id: 'm2',
          role: 'assistant',
          content: 'x',
          status: 'done',
          extra: { botUuid: 'bot-a' },
        },
        participants,
      ),
    ).toBeNull();
    expect(
      resolveAbortableBotId(
        {
          id: 'm3',
          role: 'assistant',
          content: 'x',
          status: 'streaming',
          extra: { botUuid: 'bot-b' },
        },
        participants,
      ),
    ).toBeNull();
    expect(
      resolveAbortableBotId(
        {
          id: 'm4',
          role: 'user',
          content: 'x',
          status: 'streaming',
          extra: { botUuid: 'bot-a' },
        },
        participants,
      ),
    ).toBeNull();
  });

  it('uses the scoped WS bot id while session participants are still hydrating', () => {
    expect(
      resolveAbortableBotId(
        {
          id: 'm1',
          role: 'assistant',
          content: 'working',
          status: 'streaming',
          extra: { botUuid: 'bot-a' },
        },
        [],
      ),
    ).toBe('bot-a');

    expect(
      resolveAbortableBotId(
        {
          id: 'm2',
          role: 'assistant',
          content: 'working',
          status: 'streaming',
          extra: {},
        },
        [],
      ),
    ).toBeNull();
  });

  it('recognizes aborted participant bot messages and formats elapsed seconds', () => {
    expect(
      isAbortedBotMessage(
        {
          id: 'm1',
          role: 'assistant',
          content: 'x',
          status: 'aborted',
          extra: { botUuid: 'bot-a' },
        },
        participants,
      ),
    ).toBe(true);
    expect(getElapsedSeconds(1_000, 4_900_000)).toBe(3900);
    expect(getElapsedSeconds(1_700_000_000_000, 1_700_000_900_000)).toBe(900);
    expect(getElapsedSeconds(undefined, 4_900_000)).toBeNull();
  });
});
