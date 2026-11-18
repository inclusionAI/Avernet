import type { Experience, TaskEscortSettings } from './types';

// Archived source issues are previously manually verified synthetic records,
// intentionally outside the five currently visible issue records.
export const demoExperiences: Experience[] = [
  {
    id: 'exp-market-dedupe',
    title: '多来源资讯的幂等去重',
    description: '先规范化标题，再结合来源与日期生成幂等键，合并重复项同时保留原始引用。',
    category: '重复处理',
    workflowName: '行业动态追踪',
    reuseCount: 18,
    verified: true,
    sourceIssueId: 'issue-market-dedupe',
  },
  {
    id: 'exp-archive-schema',
    title: '结构化输出的双层校验',
    description: '历史演示问题已人工验证：模型端约束字段结构，消费端校验必填字段，异常降级至人工。',
    category: '输出结构',
    workflowName: '客服工单智能分流',
    reuseCount: 32,
    verified: true,
    sourceIssueId: 'issue-archived-schema-1028',
  },
  {
    id: 'exp-archive-retry',
    title: '外部工具调用的有限重试',
    description: '历史演示问题已人工验证：指数退避最多 2 次，失败返回明确原因，避免无界等待。',
    category: '执行超时',
    workflowName: '经营日报自动汇总',
    reuseCount: 24,
    verified: true,
    sourceIssueId: 'issue-archived-retry-1102',
  },
  {
    id: 'exp-archive-citation',
    title: '文案引用缺失的人工兜底',
    description: '历史演示问题已人工验证：引用缺失时停止自动发布，向复核人展示待补充项与原始片段。',
    category: '内容质量',
    workflowName: '内容发布前合规检查',
    reuseCount: 12,
    verified: true,
    sourceIssueId: 'issue-archived-citation-1106',
  },
];

export const demoSettings: TaskEscortSettings = {
  enabled: true,
  autoAnalyze: true,
  humanReview: true,
  notifications: true,
  recurrence: true,
  sensitivity: 'balanced',
  webhook: '',
};
