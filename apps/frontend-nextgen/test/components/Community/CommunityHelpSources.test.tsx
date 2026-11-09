/** @jest-environment jsdom */
import { CommunityHelpCenter } from '@/components/Community/HelpCenter';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const field = (name: string | RegExp) => screen.getByRole('textbox', { name });
const fill = (name: string | RegExp, value: string) => fireEvent.change(field(name), { target: { value } });
const source = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
  await user.click(screen.getByRole('combobox', { name: /任务来源/ }));
  await user.click(screen.getByRole('option', { name }));
};
const openTask = () => {
  fireEvent.click(screen.getAllByRole('button', { name: '新建求助任务' })[0]);
  fill(/任务标题/, '用户录入的任务');
  fill(/任务说明/, '输入问题和验收要求');
};
const boards = () => useCommunityHelpMockStore.getState().boards;
const newestTask = () => useCommunityHelpMockStore.getState().tasks[0];

beforeAll(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
});
beforeEach(() => {
  useCommunityHelpMockStore.getState().reset();
  jest.clearAllMocks();
});

describe('求助来源配置与手动录入', () => {
  test('模版详情支持配置、启停草稿、取消、保存回显且不改变其他模版', () => {
    render(<CommunityHelpCenter />);
    const original = boards()[0];
    const other = boards()[1];
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(screen.getAllByRole('textbox', { name: /Task Discover Bot/ })).toHaveLength(1);
    expect(screen.queryByRole('textbox', { name: /Task Discover Bot (ID|名称)/ })).not.toBeInTheDocument();
    expect(field(/Task Discover Bot/)).toBeRequired();
    fill(/Dima 项目空间/, ' 新项目空间 ');
    fill(/Task Discover Bot/, ' 自定义发现 Bot ');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Task Discover' }));
    expect(screen.queryByRole('textbox', { name: /Task Discover Bot/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Task Discover' }));
    expect(field(/Task Discover Bot/)).toHaveValue(' 自定义发现 Bot ');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    expect(screen.queryByRole('textbox', { name: /Dima 项目空间/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    expect(field(/Dima 项目空间/)).toHaveValue(' 新项目空间 ');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(boards()[0]).toEqual(original);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(field(/Dima 项目空间/)).toHaveValue(original.sourceConfig.dima.projectSpace);
    expect(field(/Task Discover Bot/)).toHaveValue(original.sourceConfig.taskDiscover.botName);
    fill(/Dima 项目空间/, ' 新项目空间 ');
    fill(/Task Discover Bot/, ' 自定义发现 Bot ');
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(boards()[1]).toEqual(other);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(field(/Dima 项目空间/)).toHaveValue('新项目空间');
    expect(field(/Task Discover Bot/)).toHaveValue('自定义发现 Bot');
  });

  test('新增开放模版仍要求启用来源的具体信息，支持仅手动输入', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fill(/模版名称/, '新来源模版');
    fireEvent.click(screen.getByRole('button', { name: '开放' }));
    fill(/Task Discover Bot/, '任务发现 Bot');
    fill(/Dima 项目空间/, ' ');
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(toast.error).toHaveBeenLastCalledWith('请填写 Dima 项目空间');
    fill(/Dima 项目空间/, '有效空间');
    fill(/Task Discover Bot/, ' ');
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(toast.error).toHaveBeenLastCalledWith('请填写 Task Discover Bot');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Task Discover' }));
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(boards()[2].sources).toEqual(['手动输入']);
  });

  test('发现 Bot 在新建预览、卡片、列表与副屏仅展示名称，并保留创建时快照', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    openTask();
    expect(screen.getByRole('dialog')).toHaveTextContent(boards()[0].sourceConfig.dima.projectSpace);
    await source(user, 'Task Discover');
    const config = { ...boards()[0].sourceConfig.taskDiscover };
    const sourceText = `Task Discover Bot：${config.botName}`;
    expect(within(screen.getByRole('dialog')).getByText(sourceText, { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    const info = screen.getByRole('region', { name: '任务信息' });
    expect(within(info).getByText(sourceText, { exact: true })).toBeInTheDocument();
    expect(newestTask().sourceDetails).toEqual({ kind: 'Task Discover', botName: config.botName });
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    const card = screen.getByRole('button', { name: '用户录入的任务' }).closest('article')!;
    expect(within(card).getByText(sourceText, { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '列表视图' }));
    const row = screen.getByRole('button', { name: '用户录入的任务' }).closest('tr')!;
    expect(within(row).getByText(sourceText, { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fill(/Task Discover Bot/, '新发现 Bot');
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    fireEvent.click(screen.getByRole('button', { name: '用户录入的任务' }));
    expect(
      within(screen.getByRole('region', { name: '任务信息' })).getByText(sourceText, { exact: true }),
    ).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '任务信息' })).not.toHaveTextContent('新发现 Bot');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    openTask();
    await source(user, 'Task Discover');
    expect(screen.getByRole('dialog')).toHaveTextContent('Task Discover Bot：新发现 Bot');
  });

  test('手动输入可不勾选同步直接创建，不显示来源编号或同步目标', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    openTask();
    await source(user, '手动输入');
    expect(screen.queryByRole('textbox', { name: /来源任务编号/ })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' })).not.toBeChecked();
    expect(screen.queryByRole('textbox', { name: /同步目标 Dima 项目空间/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    expect(newestTask().source).toBe('手动输入');
    expect(newestTask().syncToDima).toBe(false);
    expect(newestTask().dimaProjectSpace).toBe('');
    expect(screen.getByRole('region', { name: '任务信息' })).toHaveTextContent('未开启自动同步');
  });

  test('手动输入勾选同步默认带入空间，可修改；两个视图和副屏均展示模拟待同步', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    openTask();
    await source(user, '手动输入');
    fireEvent.click(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' }));
    expect(field(/同步目标 Dima 项目空间/)).toHaveValue(boards()[0].sourceConfig.dima.projectSpace);
    fill(/同步目标 Dima 项目空间/, ' ');
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    expect(toast.error).toHaveBeenLastCalledWith('请填写同步目标 Dima 项目空间');
    fill(/同步目标 Dima 项目空间/, ' 新同步目标 ');
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    expect(newestTask().dimaProjectSpace).toBe('新同步目标');
    expect(screen.getByRole('region', { name: '任务信息' })).toHaveTextContent('模拟待同步（未实际同步）');
    expect(screen.getByRole('region', { name: '任务信息' })).toHaveTextContent('新同步目标');
    fireEvent.click(screen.getByRole('button', { name: '模拟开始协作' }));
    for (let i = 0; i < 6; i += 1) fireEvent.click(screen.getByRole('button', { name: '模拟推进下一阶段' }));
    expect(screen.getByRole('region', { name: '任务信息' })).toHaveTextContent('模拟待同步（未实际同步）');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    let container = screen.getByRole('button', { name: '用户录入的任务' }).closest('article')!;
    expect(container).toHaveTextContent('新同步目标');
    expect(container).toHaveTextContent('模拟待同步（未实际同步）');
    fireEvent.click(screen.getByRole('button', { name: '列表视图' }));
    container = screen.getByRole('button', { name: '用户录入的任务' }).closest('tr')!;
    expect(container).toHaveTextContent('新同步目标');
    expect(container).toHaveTextContent('模拟待同步（未实际同步）');
    await user.click(screen.getByRole('combobox', { name: '筛选任务来源' }));
    await user.click(screen.getByRole('option', { name: '手动输入' }));
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: '卡片视图' }));
    expect(screen.getAllByRole('article')).toHaveLength(1);
  });

  test('取消勾选及切换来源清除同步意愿，不丢失标题说明；取消创建不写入', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    openTask();
    await source(user, '手动输入');
    fireEvent.click(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' }));
    fill(/同步目标 Dima 项目空间/, '不应保存的目标');
    fireEvent.click(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' }));
    expect(screen.queryByRole('textbox', { name: /同步目标 Dima 项目空间/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' }));
    expect(field(/同步目标 Dima 项目空间/)).not.toHaveValue('不应保存的目标');
    await source(user, 'Dima');
    expect(screen.queryByRole('checkbox', { name: '自动同步到 Dima 项目' })).not.toBeInTheDocument();
    await source(user, '手动输入');
    expect(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' })).not.toBeChecked();
    expect(field(/任务标题/)).toHaveValue('用户录入的任务');
    expect(field(/任务说明/)).toHaveValue('输入问题和验收要求');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCommunityHelpMockStore.getState().tasks).toHaveLength(4);
  });

  test('仅手动输入模版也能配置同步目标，不依赖 Dima 入站启用', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Task Discover' }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    openTask();
    expect(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: '自动同步到 Dima 项目' }));
    fill(/同步目标 Dima 项目空间/, '手动独立空间');
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    expect(newestTask().dimaProjectSpace).toBe('手动独立空间');
    expect(newestTask().source).toBe('手动输入');
  });
});
