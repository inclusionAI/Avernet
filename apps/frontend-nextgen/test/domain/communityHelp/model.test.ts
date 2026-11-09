import { createHelpMockState, HELP_MOCK_BOTS } from '@/domain/communityHelp/mock';
import { advanceHelpTask, createHelpTask, helpTaskProgress, validateHelpBoard } from '@/domain/communityHelp/model';
import { HELP_ROLES, type HelpBoardDraft, type HelpTaskDraft } from '@/domain/communityHelp/types';

const at = '2026-11-09T06:00:00.000Z';
const draft: HelpTaskDraft = {
  title: '  任务标题  ',
  description: '  修复问题并补充验证  ',
  source: 'Dima',
  sourceRef: '  DIMA-1  ',
};

describe('求助_new Mock domain', () => {
  test('初始只有 AiCoding，包含四种任务状态及六角色协作', () => {
    const { boards, tasks } = createHelpMockState();
    expect(boards).toHaveLength(1);
    expect(boards[0].name).toBe('AiCoding');
    expect(tasks.map((task) => task.status)).toEqual(['running', 'pending', 'attention', 'done']);
    expect(tasks[0].stages.map((stage) => stage.role)).toEqual(HELP_ROLES);
    expect(helpTaskProgress(tasks[3])).toBe(100);
  });

  test.each<[string, Partial<HelpBoardDraft>, string]>([
    ['空白名称', { name: '  ' }, '请填写版面名称'],
    ['重复名称', { name: ' aicoding ' }, '版面名称已存在'],
    ['没有来源', { sources: [] }, '请至少启用一个有效任务来源'],
    ['没有 Bot', { members: [] }, '请至少选择一个 Bot'],
    ['没有角色', { members: [{ botId: 'mock-coder', roles: [] }] }, '请为每个选中的 Bot 分配至少一个角色'],
    ['无效 Bot', { members: [{ botId: 'real-bot', roles: ['代码'] }] }, '请选择有效的 Mock Bot'],
    [
      '重复 Bot',
      {
        members: [
          { botId: 'mock-coder', roles: ['代码'] },
          { botId: 'mock-coder', roles: ['verify'] },
        ],
      },
      '请勿重复选择 Bot',
    ],
  ])('拒绝%s', (_label, overrides, error) => {
    const { boards } = createHelpMockState();
    expect(validateHelpBoard({ ...boards[0], name: '新版面', ...overrides }, boards, HELP_MOCK_BOTS)).toBe(error);
  });

  test('编辑原版面允许保留名称，一个 Bot 可以承担多角色', () => {
    const { boards } = createHelpMockState();
    expect(
      validateHelpBoard(
        { ...boards[0], members: [{ botId: 'mock-coder', roles: ['代码', 'verify'] }] },
        boards,
        HELP_MOCK_BOTS,
        boards[0].id,
      ),
    ).toBeNull();
  });

  test.each([
    [{ ...draft, title: ' ' }, '请填写任务标题'],
    [{ ...draft, description: ' ' }, '请填写任务说明'],
    [{ ...draft, source: 'Task Discover' as const }, '请选择当前版面已启用的任务来源'],
  ])('创建任务校验输入 %#', (input, message) => {
    const board = { ...createHelpMockState().boards[0], sources: ['Dima' as const] };
    expect(() => createHelpTask('new', board, input, HELP_MOCK_BOTS, at)).toThrow(message);
  });

  test('按已分配角色顺序构建阶段，并保存 Bot 分工快照', () => {
    const board = createHelpMockState().boards[0];
    board.members = [
      { botId: 'mock-coder', roles: ['verify', '代码'] },
      { botId: 'mock-verify', roles: ['verify'] },
    ];
    const task = createHelpTask('new', board, draft, HELP_MOCK_BOTS, at);
    expect(task.title).toBe('任务标题');
    expect(task.sourceRef).toBe('DIMA-1');
    expect(task.stages.map((stage) => stage.role)).toEqual(['代码', 'verify']);
    expect(task.stages[1].botNames).toEqual(['Coding Bot', 'Verify Bot']);
    board.members[0].roles = ['架构'];
    board.sources = ['Task Discover'];
    expect(task.stages.map((stage) => stage.role)).toEqual(['代码', 'verify']);
    expect(task.source).toBe('Dima');
  });

  test('开始和逐阶段推进直至完成，产物与进度一致且不修改输入', () => {
    const original = createHelpTask('new', createHelpMockState().boards[0], draft, HELP_MOCK_BOTS, at);
    let task = advanceHelpTask(original, at);
    expect(task.status).toBe('running');
    expect(helpTaskProgress(task)).toBe(0);
    expect(original.status).toBe('pending');
    for (let completed = 1; completed <= 6; completed += 1) {
      task = advanceHelpTask(task, at);
      expect(task.artifacts).toHaveLength(completed);
      expect(helpTaskProgress(task)).toBe(Math.round((completed / 6) * 100));
    }
    expect(task.status).toBe('done');
    expect(task.stages.every((stage) => stage.status === 'done')).toBe(true);
    expect(task.artifacts[5].content).toContain('未创建真实 PR');
    expect(advanceHelpTask(task, at)).toBe(task);
  });

  test('需关注任务先解除阻塞，不跳过当前阶段', () => {
    const attention = createHelpMockState().tasks[2];
    const task = advanceHelpTask(attention, at);
    expect(task.status).toBe('running');
    expect(task.stages[4].status).toBe('running');
    expect(task.stages[4].output).toBe('');
    expect(task.artifacts).toEqual(attention.artifacts);
    expect(helpTaskProgress(task)).toBe(helpTaskProgress(attention));
  });
});
