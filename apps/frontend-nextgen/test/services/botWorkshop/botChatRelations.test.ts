import type { BotChatDetail, BotChatPage } from '@/domain/botChats';
import {
  getBotChatRelationOptions,
  groupBotChatRelatedTraces,
  resolveBotChatRelationScope,
} from '@/services/botWorkshop/botChatRelations';

describe('botChatRelations', () => {
  const detail: BotChatDetail = {
    id: 'trace-1',
    timestamp: '2026-08-19T00:00:00Z',
    name: 'Trace',
    sessionId: 'session-1',
    bizScene: 'scene',
    bizTaskId: 'task-1',
    groupId: 'group-1',
    status: 'SUCCESS',
    latencyMs: 10,
    totalTokens: 1,
    totalCost: 0,
    observations: [],
  };

  it('exposes all available relation dimensions and their values', () => {
    expect(getBotChatRelationOptions(detail)).toEqual([
      expect.objectContaining({ value: 'session', valueText: 'session-1', disabledReason: undefined }),
      expect.objectContaining({ value: 'task', valueText: 'task-1', disabledReason: undefined }),
      expect.objectContaining({ value: 'group', valueText: 'group-1', disabledReason: undefined }),
    ]);
  });

  it('falls back from an unavailable preferred dimension', () => {
    expect(resolveBotChatRelationScope({ ...detail, sessionId: undefined, sessionKey: undefined }, 'session')).toBe(
      'task',
    );
    expect(resolveBotChatRelationScope({ ...detail, bizTaskId: undefined, groupId: undefined }, 'task')).toBe(
      'session',
    );
  });

  it('groups task traces by session and sorts groups by latest trace', () => {
    const page: BotChatPage = {
      total: 4,
      page: 1,
      limit: 100,
      hasMore: false,
      items: [
        { ...detail, id: 'a', timestamp: '2026-08-19T10:00:00Z', sessionId: 's-a' },
        { ...detail, id: 'b', timestamp: '2026-08-19T12:00:00Z', sessionId: 's-b' },
        { ...detail, id: 'c', timestamp: '2026-08-19T11:00:00Z', sessionId: 's-a' },
        { ...detail, id: 'd', timestamp: '2026-08-19T09:00:00Z', sessionId: undefined, sessionKey: undefined },
      ],
    };

    expect(
      groupBotChatRelatedTraces(page, 'task').map((group) => [group.label, group.items.map((item) => item.id)]),
    ).toEqual([
      ['s-b', ['b']],
      ['s-a', ['c', 'a']],
      ['未关联 Session', ['d']],
    ]);
  });
});
