import type { TaskComposerForm } from '@/services/tasks/taskMapper';
import { isDemoOkrMarketingTask, runTaskPreflightMock } from '@/services/tasks/taskPreflightMock';

const baseForm: TaskComposerForm = {
  title: '大促 GMV 增长 OKR',
  objective: '提升大促期间 GMV 增长',
  instruction: '目标：提升大促期间 GMV 增长',
  acceptances: [],
  taskType: 'dynamic',
};

describe('taskPreflightMock', () => {
  it('仅在同时包含 GMV 增长目标和大促场景时命中', () => {
    expect(isDemoOkrMarketingTask(baseForm)).toBe(true);
    expect(
      isDemoOkrMarketingTask({
        ...baseForm,
        title: '大促活动页面',
        objective: '开发活动页面',
        instruction: '目标：开发活动页面',
      }),
    ).toBe(false);
    expect(
      isDemoOkrMarketingTask({
        ...baseForm,
        title: '增长目标',
        objective: '提升 GMV 增长',
        instruction: '目标：提升 GMV 增长',
      }),
    ).toBe(false);
  });

  it('命中后返回固定的需求分析与专家委派剧本', async () => {
    const result = await runTaskPreflightMock(baseForm);

    expect(result.matched).toBe(true);
    expect(result.message).toContain('我已收到任务需求，正在进行分析。');
    expect(result.message).toContain('当前 Bot 不具备完整的大促营销策略制定能力');
    expect(result.message).toContain('已发现「大促营销策略专家 Bot」');
    expect(result.message).toContain('现将该任务指派给「大促营销策略专家 Bot」执行');
  });
});
