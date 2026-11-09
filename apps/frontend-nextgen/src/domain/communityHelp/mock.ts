import { advanceHelpTask, createHelpTask } from './model';
import { HELP_ROLES, type HelpBoard, type HelpBot, type HelpTask } from './types';

export const HELP_MOCK_BOTS: HelpBot[] = [
  { id: 'mock-product', name: '需求分析 Bot', description: '问题澄清 · 验收标准' },
  { id: 'mock-architect', name: '架构 Bot', description: '方案设计 · 影响分析' },
  { id: 'mock-app', name: '应用 Bot', description: '应用集成 · 交互设计' },
  { id: 'mock-coder', name: 'Coding Bot', description: '代码实现 · 单元测试' },
  { id: 'mock-verify', name: 'Verify Bot', description: '测试回归 · 质量验证' },
  { id: 'mock-pr', name: 'PR Bot', description: '变更说明 · 交付整理' },
];

export function createHelpMockState(): { boards: HelpBoard[]; tasks: HelpTask[]; selectedBoardId: string } {
  const board: HelpBoard = {
    id: 'mock-aicoding',
    type: 'AiCoding',
    collaborationMode: 'selected',
    name: '全图风控AiCoding',
    description: '让多位 Bot 接力协作，从问题澄清到代码交付，一起解决开发难题。',
    members: HELP_MOCK_BOTS.map((bot, index) => ({ botId: bot.id, roles: [HELP_ROLES[index]] })),
    sources: ['Dima', 'Task Discover', '手动输入'],
    sourceConfig: {
      dima: { projectSpace: '全图风控 · 开发协作示例空间' },
      taskDiscover: { botName: '风控任务发现 Bot' },
    },
  };
  const tcBoard: HelpBoard = {
    id: 'mock-tc-aicoding',
    type: 'AiCoding',
    collaborationMode: 'selected',
    name: 'TC-AiCoding',
    description: '让多位 Bot 接力协作，从问题澄清到代码交付，一起解决开发难题。',
    members: HELP_MOCK_BOTS.map((bot, index) => ({ botId: bot.id, roles: [HELP_ROLES[index]] })),
    sources: ['Dima', 'Task Discover', '手动输入'],
    sourceConfig: {
      dima: { projectSpace: 'TC · 开发协作示例空间' },
      taskDiscover: { botName: 'TC 任务发现 Bot' },
    },
  };
  const seeds = [
    {
      title: '修复工作台会话切换后消息重复渲染的问题',
      description:
        '快速切换会话后，部分历史消息重复展示。需要定位消息合并逻辑，补充回归测试，并整理 PR 摘要。验收要求：连续切换会话时消息顺序正确、没有重复。',
      source: 'Dima' as const,
      sourceRef: 'DIMA-DEMO-1042',
      steps: 4,
    },
    {
      title: '为 Bot 配置页补充批量角色分配能力',
      description: '在 Bot 配置页增加批量角色分配交互，保留单个 Bot 的个性化设置，并覆盖批量修改和取消流程。',
      source: 'Task Discover' as const,
      sourceRef: 'TD-DEMO-2086',
      steps: 0,
    },
    {
      title: '排查任务进度偶发停滞，补齐异常恢复路径',
      description: '任务进入验证阶段后偶发停滞，缺少失败提示。需要澄清重试策略，增加可观察的错误状态与人工恢复入口。',
      source: 'Task Discover' as const,
      sourceRef: 'TD-DEMO-2079',
      steps: 5,
    },
    {
      title: '优化社区移动端任务卡片的响应式布局',
      description: '小屏幕上任务信息与操作按钮发生挤压。调整卡片布局、长标题换行和抽屉宽度，确保键盘和触屏均可操作。',
      source: 'Dima' as const,
      sourceRef: 'DIMA-DEMO-1031',
      steps: 7,
    },
  ];
  const tasks = seeds.map((seed, index) => {
    const at = `2026-11-09T0${index + 1}:00:00.000Z`;
    let task = createHelpTask(
      `mock-help-${index + 1}`,
      board,
      { ...seed, syncToDima: false, dimaProjectSpace: '' },
      HELP_MOCK_BOTS,
      at,
    );
    for (let step = 0; step < seed.steps; step += 1) {
      task = advanceHelpTask(task, `2026-11-09T0${index + 1}:${String(step + 1).padStart(2, '0')}:00.000Z`);
    }
    if (index === 2) {
      task = {
        ...task,
        status: 'attention',
        stages: task.stages.map((stage) =>
          stage.status === 'running'
            ? { ...stage, status: 'attention', output: '等待确认：失败重试是否需要人工审批？请补充验收口径。' }
            : stage,
        ),
        activities: [
          {
            id: `${task.id}-attention`,
            at: '2026-11-09T03:08:00.000Z',
            message: 'Verify Bot 发现验收口径缺失，等待人工确认（Mock）。',
          },
          ...task.activities,
        ],
      };
    }
    return task;
  });
  return { boards: [board, tcBoard], tasks, selectedBoardId: board.id };
}
