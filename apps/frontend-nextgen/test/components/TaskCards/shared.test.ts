import { asItems, normalizeReadyTask } from '@/components/TaskCards/shared';

describe('TaskCards shared normalization', () => {
  it('asItems keeps valid list strings and trims them', () => {
    expect(asItems([' 一份报告 ', '', 1 as unknown as string])).toEqual(['一份报告']);
  });

  it('asItems treats a non-empty scalar string as a single item instead of silently dropping it', () => {
    expect(asItems('一份尽调报告')).toEqual(['一份尽调报告']);
  });

  it('normalizeReadyTask coerces legacy scalar list fields to string arrays', () => {
    const task = normalizeReadyTask({
      type: 'task_ready',
      task: {
        goal: '完成尽调',
        deliverables: '一份尽调报告' as unknown as string[],
        acceptance_criteria: '明确投资价值' as unknown as string[],
        constraints: '需引用最近三个月信息' as unknown as string[],
        resources: 'https://example.com/report' as unknown as string[],
      },
    });

    expect(task.deliverables).toEqual(['一份尽调报告']);
    expect(task.acceptance_criteria).toEqual(['明确投资价值']);
    expect(task.constraints).toEqual(['需引用最近三个月信息']);
    expect(task.resources).toEqual(['https://example.com/report']);
  });
});
