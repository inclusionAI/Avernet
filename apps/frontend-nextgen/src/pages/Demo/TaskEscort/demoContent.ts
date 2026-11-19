/** 全部为虚构、本地数据；独立宿主不装载身份、会话或聊天服务。 */
export const DEMO_BOT_NAME = '数据巡检 Bot';
export const DEMO_SESSION_TITLE = '今日数据巡检与报告';
export const DEMO_MESSAGES = [
  {
    id: 'demo-request',
    role: 'user',
    time: '09:30',
    text: '帮我检查今天的关键指标，定位数据异常，并整理一份巡检报告。',
  },
  {
    id: 'demo-plan',
    role: 'assistant',
    time: '09:30',
    text: '好的，我会按「采集指标 → 异常分析 → 生成报告」执行。任务护航会记录每一步的进度，遇到异常时保留现场，方便检查与重试。',
  },
  {
    id: 'demo-progress',
    role: 'assistant',
    time: '09:31',
    text: '示例指标已准备完成。你可以打开右上角的「任务护航」，查看运行概览，或切换到编辑器调整流程。所有操作只影响本页的演示数据。',
  },
] as const;
