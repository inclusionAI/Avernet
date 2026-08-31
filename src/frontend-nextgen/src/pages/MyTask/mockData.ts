export const routineSeeds = [
  {
    id: 'routine-weekly-brief',
    name: '每周需求简报',
    status: 'enabled' as const,
    botName: '需求协作 Bot',
    ownerName: 'Carol',
    model: 'gpt-4.1-mini',
    frequency: '每周一 09:00',
    timezone: 'Asia/Shanghai',
    nextRunAt: '2026-08-25T09:00:00+08:00',
    lastRunAt: '2026-08-18T09:00:00+08:00',
    prompt: '汇总上周需求变更、评审结论和待确认项，输出一份团队周报。',
    totalRuns: 12,
    successRuns: 11,
    failedRuns: 1,
  },
  {
    id: 'routine-daily-summary',
    name: '每日执行摘要',
    status: 'enabled' as const,
    botName: '执行监控 Bot',
    ownerName: 'Carol',
    model: 'qwen-plus',
    frequency: '每天 18:30',
    timezone: 'Asia/Shanghai',
    nextRunAt: '2026-08-23T18:30:00+08:00',
    lastRunAt: '2026-08-21T18:30:00+08:00',
    prompt: '收敛当天执行结果、失败项和未完成项，整理为可读摘要。',
    totalRuns: 28,
    successRuns: 27,
    failedRuns: 1,
  },
  {
    id: 'routine-risk-review',
    name: '风险事件巡检',
    status: 'paused' as const,
    botName: '风险决策 Bot',
    ownerName: 'Carol',
    model: 'deepseek-v3',
    frequency: '每 2 小时',
    timezone: 'Asia/Shanghai',
    nextRunAt: '2026-08-22T16:00:00+08:00',
    lastRunAt: '2026-08-22T14:00:00+08:00',
    prompt: '巡检最近两小时内的风险事件，筛出需要人工介入的条目。',
    totalRuns: 15,
    successRuns: 14,
    failedRuns: 1,
  },
  {
    id: 'routine-release-check',
    name: '发布前检查',
    status: 'enabled' as const,
    botName: '发布护航 Bot',
    ownerName: 'Carol',
    model: 'gpt-4.1-mini',
    frequency: '每次发布前',
    timezone: 'Asia/Shanghai',
    nextRunAt: '2026-08-22T20:00:00+08:00',
    lastRunAt: '2026-08-21T20:00:00+08:00',
    prompt: '检查发布清单、关键指标和风险提示，生成发布前说明。',
    totalRuns: 6,
    successRuns: 6,
    failedRuns: 0,
  },
] as const;

export const routineRunMap: Record<
  string,
  Array<{
    id: string;
    instanceNo: string;
    status: 'success' | 'failed' | 'running' | 'pending';
    statusLabel: string;
    plannedTriggerAt: string;
    actualTriggerAt?: string;
    duration: string;
    taskName?: string;
    outputSummary?: string;
    errorMessage?: string;
  }>
> = {
  'routine-weekly-brief': [
    {
      id: 'weekly-brief-001',
      instanceNo: 'RUN-20260818-01',
      status: 'success',
      statusLabel: '成功',
      plannedTriggerAt: '2026-08-18T09:00:00+08:00',
      actualTriggerAt: '2026-08-18T09:00:21+08:00',
      duration: '4 分钟',
      taskName: '每周需求简报',
      outputSummary: '已整理本周共识、变更项与 3 个待确认问题，适合直接转发给团队。',
    },
    {
      id: 'weekly-brief-002',
      instanceNo: 'RUN-20260811-01',
      status: 'success',
      statusLabel: '成功',
      plannedTriggerAt: '2026-08-11T09:00:00+08:00',
      actualTriggerAt: '2026-08-11T09:00:05+08:00',
      duration: '5 分钟',
      taskName: '每周需求简报',
      outputSummary: '输出摘要 8 条，包含评审结论、风险提示和下周计划。',
    },
  ],
  'routine-daily-summary': [
    {
      id: 'daily-summary-004',
      instanceNo: 'RUN-20260821-01',
      status: 'success',
      statusLabel: '成功',
      plannedTriggerAt: '2026-08-21T18:30:00+08:00',
      actualTriggerAt: '2026-08-21T18:30:17+08:00',
      duration: '3 分钟',
      taskName: '每日执行摘要',
      outputSummary: '当天 14 个执行项中 12 个完成，2 个待补充输入。',
    },
    {
      id: 'daily-summary-003',
      instanceNo: 'RUN-20260820-01',
      status: 'failed',
      statusLabel: '失败',
      plannedTriggerAt: '2026-08-20T18:30:00+08:00',
      actualTriggerAt: '2026-08-20T18:30:02+08:00',
      duration: '—',
      taskName: '每日执行摘要',
      errorMessage: '摘要生成失败：上游执行日志暂不可用。',
    },
  ],
  'routine-risk-review': [
    {
      id: 'risk-review-002',
      instanceNo: 'RUN-20260822-02',
      status: 'running',
      statusLabel: '运行中',
      plannedTriggerAt: '2026-08-22T14:00:00+08:00',
      actualTriggerAt: '2026-08-22T14:00:31+08:00',
      duration: '进行中',
      taskName: '风险事件巡检',
      outputSummary: '正在扫描最新风险事件与告警记录。',
    },
  ],
  'routine-release-check': [
    {
      id: 'release-check-001',
      instanceNo: 'RUN-20260821-01',
      status: 'success',
      statusLabel: '成功',
      plannedTriggerAt: '2026-08-21T20:00:00+08:00',
      actualTriggerAt: '2026-08-21T20:00:10+08:00',
      duration: '2 分钟',
      taskName: '发布前检查',
      outputSummary: '已完成清单校验、关键指标复核和风险提醒。',
    },
  ],
};
