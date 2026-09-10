import {
  DEFAULT_MESSAGE_VIEW_SCOPE,
  MESSAGE_VIEW_SCOPE_LABEL,
  MESSAGE_VIEW_SCOPE_OPTIONS,
  PARTICIPANT_ONLY_CHECKBOX,
} from '@/domain/collaboration/messageViewScope';
import { mapParticipant } from '@/services/workspace/mappers';
import { describe, expect, it } from '@jest/globals';

describe('messageViewScope 常量', () => {
  it('默认 full，选项覆盖 full/participant 且文案非空', () => {
    expect(DEFAULT_MESSAGE_VIEW_SCOPE).toBe('full');
    expect(MESSAGE_VIEW_SCOPE_OPTIONS.map((option) => option.value)).toEqual(['full', 'participant']);
    for (const option of MESSAGE_VIEW_SCOPE_OPTIONS) {
      expect(option.label).toBeTruthy();
      expect(option.description).toBeTruthy();
    }
  });

  it('成员卡标签文案与选项文案一致', () => {
    expect(MESSAGE_VIEW_SCOPE_LABEL).toEqual({ full: '完整视角', participant: '参与者视角' });
    for (const option of MESSAGE_VIEW_SCOPE_OPTIONS) {
      expect(MESSAGE_VIEW_SCOPE_LABEL[option.value]).toBe(option.label);
    }
  });

  it('加入会话 checkbox 文案齐备', () => {
    expect(PARTICIPANT_ONLY_CHECKBOX.label).toBe('只看公开及与我相关的消息');
    expect(PARTICIPANT_ONLY_CHECKBOX.hint).toBeTruthy();
    expect(PARTICIPANT_ONLY_CHECKBOX.tooltip).toBeTruthy();
  });
});

describe('mapParticipant 回显 message_view_scope', () => {
  it('有值时映射为 messageViewScope', () => {
    const view = mapParticipant({
      actor_id: 'human_1',
      actor_kind: 'human',
      role: 'consultant',
      mode: 'present',
      message_view_scope: 'participant',
    });
    expect(view.messageViewScope).toBe('participant');
  });

  it('无值时不回显', () => {
    const view = mapParticipant({ actor_id: 'human_1', actor_kind: 'human', role: 'consultant', mode: 'present' });
    expect(view.messageViewScope).toBeUndefined();
  });
});
