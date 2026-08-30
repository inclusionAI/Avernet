import { matchUserTaskScope } from '@/pages/MyTask/userTaskUtils';
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
