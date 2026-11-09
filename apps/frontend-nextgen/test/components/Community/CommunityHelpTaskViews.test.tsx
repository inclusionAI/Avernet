/** @jest-environment jsdom */
import { CommunityHelpCenter } from '@/components/Community/HelpCenter';
import { createHelpMockState } from '@/domain/communityHelp/mock';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const seeds = createHelpMockState().tasks;
const views = () => within(screen.getByRole('group', { name: '任务视图' }));
const statuses = () => within(screen.getByRole('group', { name: '求助任务状态' }));
const boards = () => within(screen.getByRole('group', { name: '求助模版' }));
const taskTable = () => screen.getByRole('table', { name: '求助任务列表' });
const taskTitles = () =>
  screen.getAllByRole('progressbar').map((bar) => bar.getAttribute('aria-label')?.replace(/的完成进度$/, ''));

beforeAll(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
});
beforeEach(() => {
  useCommunityHelpMockStore.getState().reset();
  jest.clearAllMocks();
  Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: () => 'task-view-test' });
});

describe('求助任务双视图', () => {
  test('默认卡片视图，切换列表后展示相同任务、顺序和信息，再切回不改变数据', () => {
    render(<CommunityHelpCenter />);
    expect(views().getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(taskTitles()).toEqual(seeds.map((task) => task.title));
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    expect(views().getByRole('button', { name: '列表视图' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    expect(within(taskTable()).getAllByRole('row')).toHaveLength(5);
    expect(taskTitles()).toEqual(seeds.map((task) => task.title));
    expect(
      within(taskTable())
        .getAllByRole('columnheader')
        .map((header) => header.textContent),
    ).toEqual(['任务', '来源', '状态', '阶段进度', '当前阶段 / Bot', '操作']);
    const row = within(taskTable()).getByRole('button', { name: seeds[0].title }).closest('tr')!;
    expect(within(row).getByText(seeds[0].description)).toBeInTheDocument();
    expect(within(row).getByText('Dima')).toBeInTheDocument();
    expect(within(row).getByText(seeds[0].sourceRef)).toBeInTheDocument();
    expect(within(row).getByText('协作中')).toBeInTheDocument();
    expect(within(row).getByText('代码')).toBeInTheDocument();
    expect(within(row).getByText('Coding Bot')).toBeInTheDocument();
    expect(within(row).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(within(taskTable()).getByText('所有协作阶段已完成')).toBeInTheDocument();
    fireEvent.click(views().getByRole('button', { name: '卡片视图' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(taskTitles()).toEqual(seeds.map((task) => task.title));
    expect(useCommunityHelpMockStore.getState().tasks).toEqual(seeds);
  });

  test.each([
    ['全部任务', seeds.map((task) => task.title)],
    ['待处理', [seeds[1].title]],
    ['协作中', [seeds[0].title]],
    ['需关注', [seeds[2].title]],
    ['已完成', [seeds[3].title]],
  ])('%s 筛选在两种视图中一致，来回切换保留筛选', (label, titles) => {
    render(<CommunityHelpCenter />);
    fireEvent.click(statuses().getByRole('button', { name: label }));
    expect(taskTitles()).toEqual(titles);
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    expect(statuses().getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
    expect(taskTitles()).toEqual(titles);
    fireEvent.click(views().getByRole('button', { name: '卡片视图' }));
    expect(taskTitles()).toEqual(titles);
    expect(statuses().getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
  });

  test('在列表直接切换全部五种状态可更新相同列表', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    for (const [label, index] of [
      ['待处理', 1],
      ['协作中', 0],
      ['需关注', 2],
      ['已完成', 3],
    ] as const) {
      fireEvent.click(statuses().getByRole('button', { name: label }));
      expect(taskTitles()).toEqual([seeds[index].title]);
      expect(within(taskTable()).getAllByRole('row')).toHaveLength(2);
    }
    fireEvent.click(statuses().getByRole('button', { name: '全部任务' }));
    expect(within(taskTable()).getAllByRole('row')).toHaveLength(5);
  });

  test('两种视图共用来源和搜索条件，无匹配结果可清除筛选但保留列表视图', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    await user.click(screen.getByRole('combobox', { name: '筛选任务来源' }));
    await user.click(screen.getByRole('option', { name: 'Task Discover' }));
    fireEvent.change(screen.getByRole('textbox', { name: '搜索求助任务' }), { target: { value: 'TD-DEMO-2086' } });
    expect(taskTitles()).toEqual([seeds[1].title]);
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    expect(taskTitles()).toEqual([seeds[1].title]);
    expect(screen.getByRole('combobox', { name: '筛选任务来源' })).toHaveTextContent('Task Discover');
    expect(screen.getByRole('textbox', { name: '搜索求助任务' })).toHaveValue('TD-DEMO-2086');
    fireEvent.change(screen.getByRole('textbox', { name: '搜索求助任务' }), { target: { value: '不存在的任务' } });
    expect(screen.getByText('没有匹配的求助任务')).toBeInTheDocument();
    fireEvent.click(views().getByRole('button', { name: '卡片视图' }));
    expect(screen.getByText('没有匹配的求助任务')).toBeInTheDocument();
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(within(taskTable()).getAllByRole('row')).toHaveLength(5);
    expect(screen.getByRole('combobox', { name: '筛选任务来源' })).toHaveTextContent('全部来源');
    expect(screen.getByRole('textbox', { name: '搜索求助任务' })).toHaveValue('');
  });

  test('从列表标题及操作入口打开同一副屏，模拟推进同步列表和卡片', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    fireEvent.click(within(taskTable()).getByRole('button', { name: seeds[1].title }));
    let drawer = within(screen.getByRole('dialog'));
    expect(drawer.getByRole('heading', { name: seeds[1].title })).toBeInTheDocument();
    fireEvent.click(drawer.getByRole('button', { name: '模拟开始协作' }));
    fireEvent.click(drawer.getByRole('button', { name: '模拟推进下一阶段' }));
    expect(drawer.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    const row = within(taskTable()).getByRole('button', { name: seeds[1].title }).closest('tr')!;
    expect(within(row).getByText('协作中')).toBeInTheDocument();
    expect(within(row).getByText('架构')).toBeInTheDocument();
    expect(within(row).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
    fireEvent.click(within(row).getByRole('button', { name: /查看.*的进度/ }));
    drawer = within(screen.getByRole('dialog'));
    expect(drawer.getByRole('heading', { name: seeds[1].title })).toBeInTheDocument();
    expect(drawer.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    fireEvent.click(views().getByRole('button', { name: '卡片视图' }));
    const card = screen.getByRole('button', { name: seeds[1].title }).closest('article')!;
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
  });

  test('切换空模版及新增模版保留列表视图，新任务不串版', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(views().getByRole('button', { name: '列表视图' }));
    fireEvent.click(boards().getByRole('button', { name: 'TC-AiCoding' }));
    expect(screen.getByText('这个模版还没有求助任务')).toBeInTheDocument();
    expect(views().getByRole('button', { name: '列表视图' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getAllByRole('button', { name: '新建求助任务' })[0]);
    fireEvent.change(screen.getByRole('textbox', { name: /任务标题/ }), { target: { value: 'TC 列表任务' } });
    fireEvent.change(screen.getByRole('textbox', { name: /任务说明/ }), { target: { value: '验证模版隔离' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(within(taskTable()).getAllByRole('row')).toHaveLength(2);
    expect(within(taskTable()).getByRole('button', { name: 'TC 列表任务' })).toBeInTheDocument();
    fireEvent.click(boards().getByRole('button', { name: '全图风控AiCoding' }));
    expect(taskTitles()).toEqual(seeds.map((task) => task.title));
    expect(screen.queryByRole('button', { name: 'TC 列表任务' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
      target: { value: '任务发现 Bot' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '新列表模版' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Coding Bot/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · 代码' }));
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(screen.getByText('这个模版还没有求助任务')).toBeInTheDocument();
    expect(views().getByRole('button', { name: '列表视图' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(boards().getByRole('button', { name: 'TC-AiCoding' }));
    expect(taskTitles()).toEqual(['TC 列表任务']);
  });

  test('视图切换支持键盘，列表任务标题可键盘打开副屏', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    views().getByRole('button', { name: '卡片视图' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(views().getByRole('button', { name: '列表视图' })).toHaveFocus();
    expect(taskTable()).toBeInTheDocument();
    within(taskTable()).getByRole('button', { name: seeds[0].title }).focus();
    await user.keyboard('{Enter}');
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: seeds[0].title })).toBeInTheDocument();
  });
});
