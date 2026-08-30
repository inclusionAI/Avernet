import type { GroupView } from '@/domain/collaboration';
import { buildGroupMentionConfig, expandMentionIds } from '@/pages/Workspace/components/GroupChatPane/mentionHelpers';
import { describe, expect, it } from '@jest/globals';
import type { MentionItem } from '@tc-chat/ui';

const participants: GroupView['participants'] = [
  { actorId: 'bot-a', kind: 'bot', name: '甲', role: 'driver', mode: 'auto' },
  { actorId: 'bot-b', kind: 'bot', name: '乙', role: 'member', mode: 'auto' },
  { actorId: 'human_1', kind: 'human', name: '我', role: 'member', mode: 'present' },
];

describe('mentionHelpers', () => {
  it('builds ALL + bot-only mention categories', () => {
    const config = buildGroupMentionConfig(participants);
    expect(config.categories).toHaveLength(1);
    expect(config.categories[0].items?.map((item) => item.id)).toEqual(['ALL', 'bot-a', 'bot-b']);
  });

  it('keeps explicit bot mentions unchanged', () => {
    const selected: MentionItem[] = [{ id: 'bot-a', name: '甲' }];
    expect(expandMentionIds(selected, participants)).toEqual(['bot-a']);
  });

  it('expands ALL to every bot id', () => {
    const selected: MentionItem[] = [{ id: 'ALL', name: 'ALL' }];
    expect(expandMentionIds(selected, participants)).toEqual(['bot-a', 'bot-b']);
  });

  it('returns undefined when no mention is selected', () => {
    expect(expandMentionIds([], participants)).toBeUndefined();
  });
});
