import type { GroupView } from '@/domain/collaboration';
import {
  buildGroupMentionConfig,
  expandMentionIds,
  isHumanOnlyMention,
} from '@/pages/Workspace/components/GroupChatPane/mentionHelpers';
import { describe, expect, it } from '@jest/globals';
import type { MentionItem } from '@tc-chat/ui';

const participants: GroupView['participants'] = [
  { actorId: 'bot-a', kind: 'bot', name: '甲', role: 'driver', mode: 'auto' },
  { actorId: 'bot-b', kind: 'bot', name: '乙', role: 'member', mode: 'auto' },
  { actorId: 'human_1', kind: 'human', name: '我', role: 'member', mode: 'present' },
  { actorId: 'human_2', kind: 'human', name: '李四', role: 'member', mode: 'present' },
  { actorId: 'human_3', kind: 'human', name: '旁观者', role: 'member', mode: 'absent' },
];

describe('mentionHelpers', () => {
  it('builds ALL + session members and excludes the current human', () => {
    const config = buildGroupMentionConfig(participants, { excludedActorIds: ['human_1'] });
    expect(config.categories).toHaveLength(1);
    expect(config.categories[0].label).toBe('会话成员');
    expect(config.categories[0].items?.map((item) => item.id)).toEqual([
      'ALL-Bots',
      'bot-a',
      'bot-b',
      'human_2',
      'human_3',
    ]);
    expect(config.categories[0].items?.find((item) => item.id === 'human_3')).toMatchObject({
      description: '用户',
      disabled: true,
    });
  });

  it('keeps explicit bot mentions unchanged', () => {
    const selected: MentionItem[] = [{ id: 'bot-a', name: '甲' }];
    expect(expandMentionIds(selected, participants)).toEqual(['bot-a']);
  });

  it('expands ALL to every bot id', () => {
    const selected: MentionItem[] = [{ id: 'ALL-Bots', name: 'ALL-Bots' }];
    expect(expandMentionIds(selected, participants)).toEqual(['bot-a', 'bot-b']);
  });

  it('keeps explicit human mentions unchanged', () => {
    const selected: MentionItem[] = [{ id: 'human_2', name: '李四' }];
    expect(expandMentionIds(selected, participants)).toEqual(['human_2']);
  });

  it('detects human-only mentions', () => {
    expect(isHumanOnlyMention(['human_2', 'human_3'], participants)).toBe(true);
    expect(isHumanOnlyMention(['human_2', 'bot-a'], participants)).toBe(false);
    expect(isHumanOnlyMention([], participants)).toBe(false);
    expect(isHumanOnlyMention(['unknown'], participants)).toBe(false);
  });

  it('returns undefined when no mention is selected', () => {
    expect(expandMentionIds([], participants)).toBeUndefined();
  });
});
