import { describe, expect, it } from '@jest/globals';
import { useNoopServiceBotRouting } from '@/pages/Workspace/hooks/noopServiceBotRouting';

describe('useNoopServiceBotRouting', () => {
  it('恒 ready、不打标签、不做分类查询', () => {
    const routing = useNoopServiceBotRouting();
    expect(routing.ready).toBe(true);
    expect(routing.states).toEqual({});
    const participants = [{ actor_id: 'bot_1' }];
    expect(routing.applyTags(participants)).toBe(participants);
  });
});
