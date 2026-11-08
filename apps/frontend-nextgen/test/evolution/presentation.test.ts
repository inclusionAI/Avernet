import type { Step } from '@/domain/evolution/types';
import { displayValue, finishedStep, progressStages, stepName, taskTimes } from '@/pages/EvolutionFactory/presentation';
import { describe, expect, it } from '@jest/globals';

describe('evolution presentation without business changes', () => {
  it('uses Chinese labels while retaining unknown real step types', () => {
    expect(stepName('plan')).toBe('目标规划');
    expect(stepName('optimize')).toBe('优化');
    expect(stepName('future_step')).toBe('future_step');
  });
  it('keeps stage order and last-attempt state without changing original history', () => {
    const steps: Step[] = [
      { stepId: 'plan', stepType: 'plan', status: 'succeeded' },
      { stepId: 'opt-1', stepType: 'optimize', status: 'succeeded', roundNo: 1 },
      { stepId: 'opt-2', stepType: 'optimize', status: 'failed', roundNo: 2 },
    ];
    expect(progressStages(steps).map((step) => step.stepId)).toEqual(['plan', 'opt-2']);
    expect(steps).toHaveLength(3);
    expect(steps.filter(finishedStep)).toHaveLength(2);
    expect(progressStages([])).toEqual([]);
  });
  it('formats finite numbers without changing source values or missing data', () => {
    const value = 0.94 - 0.73;
    expect(displayValue(value)).toBe('0.21');
    expect(value).not.toBe(0.21);
    expect(displayValue(null)).toBe('—');
    expect(displayValue(Infinity)).toBe('—');
    expect(displayValue('v2')).toBe('v2');
  });
});

it('uses actual task/Step timestamps, not creation or an earlier finished step as task end', () => {
  const base = {
    gmt_create: 1,
    status: 'completed',
    steps: [
      { stepId: 'a', stepType: 'plan', status: 'succeeded', startedAt: 10, completedAt: 20 },
      { stepId: 'b', stepType: 'optimize', status: 'succeeded', startedAt: 30, completedAt: 40 },
    ],
  } as import('@/domain/evolution/types').Task;
  expect(taskTimes(base)).toEqual({ start: 10000, end: 40000 });
  expect(taskTimes({ ...base, steps: [] })).toEqual({ start: null, end: null });
  expect(taskTimes({ ...base, status: 'running' }).end).toBeNull();
  expect(
    taskTimes({ ...base, status: 'failed', steps: [base.steps![0], { ...base.steps![1], completedAt: null }] }).end,
  ).toBeNull();
});

it('normalizes deployed date strings and seconds before aggregating task times', () => {
  const { timestampMs, formatTime } = require('@/domain/evolution/types') as typeof import('@/domain/evolution/types');
  const start = '2026-10-14T10:20:30.000Z';
  const end = '2026-10-14T10:30:30.000Z';
  const task = {
    status: 'completed',
    gmt_create: '2026-10-14T10:00:00.000Z',
    steps: [{ stepId: 'plan', stepType: 'plan', status: 'succeeded', startedAt: start, completedAt: end }],
  } as import('@/domain/evolution/types').Task;
  expect(taskTimes(task)).toEqual({ start: Date.parse(start), end: Date.parse(end) });
  expect(formatTime(start)).toBe(new Date(start).toLocaleString());
  expect(timestampMs(Date.parse(start) / 1000)).toBe(Date.parse(start));
  expect(timestampMs(String(Date.parse(start)))).toBe(Date.parse(start));
  expect(timestampMs('invalid date')).toBeNull();
  expect(formatTime('invalid date')).toBe('—');
  expect(taskTimes({ ...task, started_at: start, completed_at: end, steps: [] })).toEqual({
    start: Date.parse(start),
    end: Date.parse(end),
  });
  expect(taskTimes({ ...task, status: 'running' }).end).toBeNull();
  expect(taskTimes({ ...task, steps: [] })).toEqual({ start: null, end: null });
});
