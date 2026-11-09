import { createHelpMockState, HELP_MOCK_BOTS } from '@/domain/communityHelp/mock';
import { advanceHelpTask, createHelpTask, helpTaskProgress, validateHelpBoard } from '@/domain/communityHelp/model';
import { HELP_ROLES, type HelpBoardDraft, type HelpTaskDraft } from '@/domain/communityHelp/types';

const at = '2026-11-09T06:00:00.000Z';
const draft: HelpTaskDraft = {
  title: '  任务标题  ',
  description: '  修复问题并补充验证  ',
  source: 'Dima',
  sourceRef: '  DIMA-1  ',
  syncToDima: false,
  dimaProjectSpace: '',
};

describe('求助_new Mock domain', () => {
  test('初始包含两个 AiCoding 模版，默认模版保留四种任务状态及六角色协作', () => {
    const { boards, tasks, selectedBoardId } = createHelpMockState();
    expect(boards.map((board) => board.name)).toEqual(['全图风控AiCoding', 'TC-AiCoding']);
    expect(boards.map((board) => board.type)).toEqual(['AiCoding', 'AiCoding']);
    expect(new Set(boards.map((board) => board.id)).size).toBe(2);
    expect(selectedBoardId).toBe(boards[0].id);
    expect(tasks.every((task) => task.boardId === boards[0].id)).toBe(true);
    expect(tasks.map((task) => task.status)).toEqual(['running', 'pending', 'attention', 'done']);
    expect(tasks[0].stages.map((stage) => stage.role)).toEqual(HELP_ROLES);
    expect(helpTaskProgress(tasks[3])).toBe(100);
  });

  test('两个初始模版的 Bot 角色和来源配置相互独立', () => {
    const { boards } = createHelpMockState();
    const [riskBoard, tcBoard] = boards;
    expect(tcBoard.members).toEqual(riskBoard.members);
    expect(tcBoard.sources).toEqual(['Dima', 'Task Discover', '手动输入']);
    expect(tcBoard.members).not.toBe(riskBoard.members);
    tcBoard.members.forEach((member, index) => {
      expect(member).not.toBe(riskBoard.members[index]);
      expect(member.roles).not.toBe(riskBoard.members[index].roles);
    });
    expect(tcBoard.sources).not.toBe(riskBoard.sources);
    expect(validateHelpBoard(tcBoard, boards, HELP_MOCK_BOTS, tcBoard.id)).toBeNull();
  });

  test.each<[string, Partial<HelpBoardDraft>, string]>([
    ['空白名称', { name: '  ' }, '请填写模版名称'],
    ['重复名称', { name: ' 全图风控aicoding ' }, '模版名称已存在'],
    ['重复 TC 名称', { name: ' tc-aicoding ' }, '模版名称已存在'],
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
    expect(validateHelpBoard({ ...boards[0], name: '新模版', ...overrides }, boards, HELP_MOCK_BOTS)).toBe(error);
  });

  test('编辑原模版允许保留名称，一个 Bot 可以承担多角色', () => {
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
    [{ ...draft, source: 'Task Discover' as const }, '请选择当前模版已启用的任务来源'],
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

  test('开放模版无需固定 Bot 或角色，名称和来源仍需有效', () => {
    const { boards } = createHelpMockState();
    const open = { ...boards[0], name: '开放协作', collaborationMode: 'open' as const, members: [] };
    expect(validateHelpBoard(open, boards, [])).toBeNull();
    expect(validateHelpBoard({ ...open, name: ' ' }, boards, [])).toBe('请填写模版名称');
    expect(validateHelpBoard({ ...open, name: boards[0].name }, boards, [])).toBe('模版名称已存在');
    expect(validateHelpBoard({ ...open, sources: [] }, boards, [])).toBe('请至少启用一个有效任务来源');
  });

  test('开放任务使用六角色但不绑定当前 Mock Bot，协作范围独立于后续模版修改', () => {
    const board = { ...createHelpMockState().boards[0], collaborationMode: 'open' as const };
    const task = createHelpTask('open-task', board, draft, HELP_MOCK_BOTS, at);
    expect(task.collaborationMode).toBe('open');
    expect(task.stages.map((stage) => stage.role)).toEqual(HELP_ROLES);
    expect(task.stages.every((stage) => stage.botNames.length === 0)).toBe(true);
    expect(createHelpTask('without-bots', { ...board, members: [] }, draft, [], at).stages).toHaveLength(6);
    board.members[0].roles = ['pr'];
    expect(task.stages.map((stage) => stage.role)).toEqual(HELP_ROLES);
  });

  test('开放任务可模拟推进至完成，产物不虚构负责 Bot', () => {
    const board = { ...createHelpMockState().boards[0], collaborationMode: 'open' as const, members: [] };
    let task = createHelpTask('open-task', board, draft, [], at);
    for (let step = 0; step < 7; step += 1) task = advanceHelpTask(task, at);
    expect(task.status).toBe('done');
    expect(helpTaskProgress(task)).toBe(100);
    expect(task.artifacts).toHaveLength(6);
    expect(task.stages.every((stage) => stage.botNames.length === 0)).toBe(true);
    task.artifacts.forEach((artifact) => {
      expect(artifact.content).toContain('开放协作 · 不限定特定 Bot');
      expect(artifact.content).not.toContain('Coding Bot');
    });
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
