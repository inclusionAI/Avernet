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


describe('任务协作群点名', () => {
  const members: GroupView['participants'] = [
    { ...participants[0], role: 'manager' },
    { ...participants[1], role: 'worker' },
    participants[3],
    { ...participants[0], actorId: 'bot-unknown-role', role: 'member' },
  ];

  it('可提及主节点和在线 Human，从节点置灰，不提供 ALL-Bots', () => {
    const items = buildGroupMentionConfig(members, { groupKind: 'task_master_slave' }).categories[0].items;
    expect(items?.map((item) => item.id)).toEqual(['bot-a', 'bot-b', 'human_2', 'bot-unknown-role']);
    expect(items?.find((item) => item.id === 'bot-a')?.disabled).toBeFalsy();
    expect(items?.find((item) => item.id === 'bot-b')?.disabled).toBe(true);
    expect(items?.find((item) => item.id === 'human_2')?.disabled).toBeFalsy();
    expect(items?.find((item) => item.id === 'bot-unknown-role')?.disabled).toBe(true);
  });

  it('提交时过滤旧草稿里的从节点；旧 ALL-Bots 只展开主节点', () => {
    const items = [{ id: 'bot-b', name: '乙' }, { id: 'human_2', name: '李四' }, { id: 'bot-a', name: '甲' }];
    expect(expandMentionIds(items, members, 'task_master_slave')).toEqual(['human_2', 'bot-a']);
    expect(expandMentionIds([items[0]], members, 'task_master_slave')).toBeUndefined();
    expect(expandMentionIds([{ id: 'ALL-Bots', name: 'ALL-Bots' }], members, 'task_master_slave')).toEqual(['bot-a']);
  });
});
