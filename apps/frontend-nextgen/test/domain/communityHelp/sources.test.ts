import { createHelpMockState, HELP_MOCK_BOTS } from '@/domain/communityHelp/mock';
import { advanceHelpTask, createHelpTask, validateHelpBoard } from '@/domain/communityHelp/model';
import { helpSourceContext } from '@/domain/communityHelp/sources';
import type { HelpBoard, HelpTaskDraft } from '@/domain/communityHelp/types';

const at = '2026-11-09T08:00:00.000Z';
const board = (): HelpBoard => ({
  ...createHelpMockState().boards[0],
  sources: ['Dima', 'Task Discover', '手动输入'],
  sourceConfig: {
    dima: { projectSpace: ' 风控项目 ' },
    taskDiscover: { botName: ' 任务发现 Bot ' },
  },
});
const draft = (overrides: Partial<HelpTaskDraft> = {}): HelpTaskDraft => ({
  title: '手动登记任务',
  description: '补充需求和验收标准',
  source: '手动输入',
  sourceRef: '',
  syncToDima: false,
  dimaProjectSpace: '',
  ...overrides,
});

describe('求助任务来源详情和 Dima 同步意愿', () => {
  test('两个初始模版支持手动输入，来源详情独立且有效', () => {
    const { boards } = createHelpMockState();
    expect(boards[0].sources).toContain('手动输入');
    expect(boards[1].sources).toContain('手动输入');
    expect(boards[0].sourceConfig).not.toBe(boards[1].sourceConfig);
    expect(boards[0].sourceConfig.dima).not.toBe(boards[1].sourceConfig.dima);
    expect(boards[0].sourceConfig.taskDiscover).not.toBe(boards[1].sourceConfig.taskDiscover);
    boards.forEach((item) => expect(validateHelpBoard(item, boards, HELP_MOCK_BOTS, item.id)).toBeNull());
  });

  test.each(['selected', 'open'] as const)('%s 范围都校验已启用的 Dima 空间和发现 Bot 名称', (collaborationMode) => {
    const input = { ...board(), collaborationMode };
    input.sourceConfig.dima.projectSpace = '  ';
    expect(validateHelpBoard(input, [], HELP_MOCK_BOTS)).toBe('请填写 Dima 项目空间');
    input.sourceConfig.dima.projectSpace = 'space';
    input.sourceConfig.taskDiscover.botName = '  ';
    expect(validateHelpBoard(input, [], HELP_MOCK_BOTS)).toBe('请填写 Task Discover Bot');
    input.sources = ['手动输入'];
    input.sourceConfig.dima.projectSpace = '';
    expect(validateHelpBoard(input, [], HELP_MOCK_BOTS)).toBeNull();
  });

  test('发现 Bot 名称不限协作团队；禁用来源不要求详情', () => {
    const input = board();
    expect(validateHelpBoard(input, [], HELP_MOCK_BOTS)).toBeNull();
    input.sources = ['Dima'];
    input.sourceConfig.taskDiscover.botName = '';
    expect(validateHelpBoard(input, [], HELP_MOCK_BOTS)).toBeNull();
  });

  test('发现 Bot 来源只展示名称，不附加 ID', () => {
    const task = createHelpTask('td', board(), draft({ source: 'Task Discover' }), HELP_MOCK_BOTS, at);
    expect(task.sourceDetails).toEqual({ kind: 'Task Discover', botName: '任务发现 Bot' });
    expect(helpSourceContext(task.sourceDetails)).toBe('Task Discover Bot：任务发现 Bot');
  });

  test.each(['Dima', 'Task Discover'] as const)('%s 任务保留规范化的来源详情快照', (source) => {
    const input = board();
    const task = createHelpTask('new', input, draft({ source, sourceRef: ' ID-1 ' }), HELP_MOCK_BOTS, at);
    expect(task.sourceDetails).toEqual(
      source === 'Dima' ? { kind: source, projectSpace: '风控项目' } : { kind: source, botName: '任务发现 Bot' },
    );
    input.sourceConfig.dima.projectSpace = 'changed';
    input.sourceConfig.taskDiscover.botName = 'changed';
    expect(JSON.stringify(task.sourceDetails)).not.toContain('changed');
    expect(task.sourceRef).toBe('ID-1');
  });

  test('创建外部任务也校验所选来源的详情', () => {
    const input = board();
    input.sourceConfig.dima.projectSpace = ' ';
    input.sourceConfig.taskDiscover.botName = ' ';
    expect(() => createHelpTask('dima', input, draft({ source: 'Dima' }), HELP_MOCK_BOTS, at)).toThrow(
      '请填写 Dima 项目空间',
    );
    expect(() => createHelpTask('td', input, draft({ source: 'Task Discover' }), HELP_MOCK_BOTS, at)).toThrow(
      '请填写 Task Discover Bot',
    );
  });

  test('手动输入无需编号，未勾选同步时不保存残留目标', () => {
    const task = createHelpTask(
      'new',
      board(),
      draft({ dimaProjectSpace: '残留目标', sourceRef: '残留编号' }),
      HELP_MOCK_BOTS,
      at,
    );
    expect(task.sourceDetails).toEqual({ kind: '手动输入' });
    expect(task.sourceRef).toBe('');
    expect(task.syncToDima).toBe(false);
    expect(task.dimaProjectSpace).toBe('');
  });

  test('勾选同步需目标空间；仅手动入站也支持同步，目标保留快照且推进不伪造同步成功', () => {
    const input = board();
    input.sources = ['手动输入'];
    input.sourceConfig.dima.projectSpace = '';
    expect(() =>
      createHelpTask('new', input, draft({ syncToDima: true, dimaProjectSpace: ' ' }), HELP_MOCK_BOTS, at),
    ).toThrow('请填写同步目标 Dima 项目空间');
    let task = createHelpTask(
      'new',
      input,
      draft({ syncToDima: true, dimaProjectSpace: ' 手工目标 ' }),
      HELP_MOCK_BOTS,
      at,
    );
    input.sourceConfig.dima.projectSpace = '另一个空间';
    for (let step = 0; step < 7; step += 1) task = advanceHelpTask(task, at);
    expect(task.dimaProjectSpace).toBe('手工目标');
    expect(task.syncToDima).toBe(true);
    expect(task.sourceRef).toBe('');
    expect(task.status).toBe('done');
    expect(task.activities.some((activity) => /已同步|同步成功/.test(activity.message))).toBe(false);
  });

  test.each(['Dima', 'Task Discover'] as const)('领域层拒绝 %s 来源启用手动同步选项', (source) => {
    expect(() =>
      createHelpTask(
        'new',
        board(),
        draft({ source, syncToDima: true, dimaProjectSpace: 'space' }),
        HELP_MOCK_BOTS,
        at,
      ),
    ).toThrow('仅手动输入任务支持自动同步到 Dima');
  });
});
