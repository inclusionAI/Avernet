import type { Run } from '@/domain/taskEscortWorkspace';
import {
  liveRunPresentation,
  selectLiveRuns,
  summarizeLiveSample,
} from '@/services/taskEscortWorkspace/livePresentation';
const runs: Run[] = [
  {
    id: 'r1',
    workflowId: 'w1',
    name: '日报',
    bot: '—',
    status: 'success',
    duration: 0,
    startedAt: null,
    trigger: 'manual',
    node: '—',
  },
  {
    id: 'r2',
    workflowId: 'w2',
    name: '周报',
    bot: '—',
    status: 'unknown',
    rawStatus: 'new-status',
    duration: null,
    startedAt: null,
    trigger: 'unknown',
    node: '—',
  },
];
it('未知时间、状态、耗时保留未知，不转成成功或零耗时', () => {
  expect(liveRunPresentation(runs[1])).toMatchObject({
    statusLabel: '未知状态',
    durationLabel: '—',
    startedAtLabel: '—',
  });
  expect(liveRunPresentation(runs[0]).durationLabel).toBe('0 秒');
});
it('真实样本组合筛选及统计，不冒充全局总量', () => {
  expect(selectLiveRuns(runs, { query: '周报', workflowId: 'w2', status: 'unknown' })).toEqual([runs[1]]);
  expect(selectLiveRuns(runs, { query: '', workflowId: 'w1', status: 'failed' })).toEqual([]);
  expect(summarizeLiveSample(runs)).toEqual({ count: 2, failed: 0, running: 0, unknown: 1 });
});
