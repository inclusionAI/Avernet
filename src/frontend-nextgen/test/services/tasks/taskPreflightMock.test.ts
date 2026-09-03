import type { TaskComposerForm } from '@/services/tasks/taskMapper';
import { isDemoOkrMarketingTask, runTaskPreflightMock } from '@/services/tasks/taskPreflightMock';

const baseForm: TaskComposerForm = {
  title: '18 周年店庆客流与护理双增长 OKR',
  objective: '18 周年店庆实现客流与护理双增长，沉淀可复购会员',
  instruction: '目标：18 周年店庆实现客流与护理双增长',
  acceptances: [],
  taskType: 'dynamic',
};

describe('taskPreflightMock', () => {
  it('仅在同时包含「周年店庆」语义和「客流/护理/会员复购增长」场景时命中', () => {
    expect(isDemoOkrMarketingTask(baseForm)).toBe(true);
    expect(
      isDemoOkrMarketingTask({
        ...baseForm,
        title: '护理客流活动',
        objective: '提升护理客流',
        instruction: '目标：提升护理客流',
      }),
    ).toBe(false);
    expect(
      isDemoOkrMarketingTask({
        ...baseForm,
        title: '周年店庆',
        objective: '举办周年店庆',
        instruction: '目标：举办周年店庆',
      }),
    ).toBe(false);
  });

  it('命中后返回固定的需求分析与店主委派剧本', async () => {
    const result = await runTaskPreflightMock(baseForm);

    expect(result.matched).toBe(true);
    expect(result.message).toContain('我已收到任务需求，正在进行分析。');
    expect(result.message).toContain('当前 Bot 无法独立完成该需求');
    expect(result.message).toContain('未发现');
    expect(result.message).toContain('现将该任务指派给「店主Bot」执行');
  });
});
