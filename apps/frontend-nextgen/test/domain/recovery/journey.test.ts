import { recoveryJourney } from '@/domain/recovery/journey';
import type { RecoveryEvent, RecoveryTask } from '@/domain/recovery/types';
import { describe, expect, it } from '@jest/globals';

const task: RecoveryTask = {
  taskId: 'recovery-test',
  status: 'completed',
  phase: 'verify',
  canOperate: false,
  request: {
    target: { bot_id: 'test', entity_id: 'test-owner', env: 'prod', engine: 'OC' },
    session_key: 'agent:main:test',
    event_id: 'test-diagnosis',
    diagnosis: '无法完成原任务。',
  },
  plan: null,
  judgment: null,
  timeline: [],
};
function event(
  event_id: string,
  kind: string,
  at_ms: number,
  payload: RecoveryEvent['payload'],
  summary = '',
): RecoveryEvent {
  return { event_id, kind, at_ms, payload, summary, actor: 'agent' };
}

describe('recovery handling history', () => {
  it('only upgrades accepted guidance when the same message is confirmed', () => {
    const sent = event('sent', 'operation', 2, {
      tool_name: 'retry_message',
      status: 'succeeded',
      result: { status: 'accepted', delivery_key: 'sent-once', message: '请从原任务继续处理。' },
    });
    const observation = (key: string) =>
      event('outcome', 'observation', 3, {
        judgment: {
          delivery: 'confirmed',
          effect: 'recovered',
          summary: `## 触达\n\n已找到 ${key}。\n\n## 效果\n\n原任务已恢复。`,
        },
      });
    expect(recoveryJourney({ ...task, timeline: [sent, observation('a-different-message')] })[0].status).toBe(
      '待确认送达',
    );
    const entries = recoveryJourney({ ...task, timeline: [sent, observation('sent-once')] });
    expect(entries.map(({ title, status }) => [title, status])).toEqual([
      ['向 Bot 发送恢复指引', '已送达'],
      ['核验原任务', '已恢复'],
    ]);
  });

  it('records user continuation separately from recovery and hides investigation reads', () => {
    const read = event('read', 'operation', 2, { tool_name: 'session_read', status: 'succeeded' });
    const stopped = event(
      'terminated',
      'termination',
      3,
      { completion_reason: 'user_continued' },
      '用户新消息：「请按只读方式再查一次。」',
    );
    const events = recoveryJourney({ ...task, completionReason: 'user_continued', timeline: [read, stopped] });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      title: '用户续聊，自愈让路',
      status: '已退出',
      details: [{ label: '用户续聊原文', body: '请按只读方式再查一次。' }],
    });
  });
});
