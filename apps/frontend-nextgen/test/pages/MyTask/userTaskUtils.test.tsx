import { getUserTaskAcceptanceText, getUserTaskGoal, matchUserTaskScope } from '@/pages/MyTask/userTaskUtils';
import { describe, expect, it } from '@jest/globals';

describe('MyTask status compatibility', () => {
  it('列表筛选兼容旧运行时状态', () => {
    expect(matchUserTaskScope('HUNG', 'progress')).toBe(true);
    expect(matchUserTaskScope('PENDING', 'progress')).toBe(true);
    expect(matchUserTaskScope('RUNNING', 'progress')).toBe(true);
    expect(matchUserTaskScope('DONE', 'done')).toBe(true);
    expect(matchUserTaskScope('FAILED', 'failed')).toBe(true);
    expect(matchUserTaskScope('CANCELLED', 'cancelled')).toBe(true);
    expect(matchUserTaskScope('HUNG', 'done')).toBe(false);
  });
});

describe('MyTask task column text', () => {
  it('目标字段只取 goal.objective', () => {
    expect(
      getUserTaskGoal({
        task_spec: {
          metadata: { instruction: '不应展示的 instruction' },
          goal: { objective: '任务目标' },
        },
      }),
    ).toBe('任务目标');
  });

  it('验收标准支持多项内容并合并展示', () => {
    expect(
      getUserTaskAcceptanceText({
        task_spec: {
          goal: {
            acceptances: [{ description: '结果可复现' }, { acceptance: '输出结论' }],
          },
        },
      }),
    ).toBe('结果可复现；输出结论');
  });

  it('缺少目标时显示占位符', () => {
    expect(getUserTaskGoal({ task_spec: { metadata: { instruction: '任务描述' } } })).toBe('—');
  });
});
