import { normalizeTaskStatus } from '@/domain/tasks/status';
import { describe, expect, it } from '@jest/globals';

describe('normalizeTaskStatus', () => {
  it('把旧运行时状态和产品层状态统一为产品任务状态', () => {
    expect(normalizeTaskStatus('HUNG')).toBe('REVIEWING');
    expect(normalizeTaskStatus('DONE')).toBe('DONE');
    expect(normalizeTaskStatus('FAILED')).toBe('FAILED');
    expect(normalizeTaskStatus('CANCELLED')).toBe('CANCELLED');
    expect(normalizeTaskStatus('PENDING')).toBe('DEFINED');
    expect(normalizeTaskStatus('PLANNING')).toBe('EXECUTING');
    expect(normalizeTaskStatus('RUNNING')).toBe('EXECUTING');
    expect(normalizeTaskStatus('unexpected')).toBe('EXECUTING');
  });
});
