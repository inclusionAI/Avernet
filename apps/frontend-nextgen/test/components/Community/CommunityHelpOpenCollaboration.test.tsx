/** @jest-environment jsdom */
import { CommunityHelpCenter } from '@/components/Community/HelpCenter';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const modes = () => within(screen.getByRole('group', { name: '协作 Bot 范围' }));
const selectMode = (name: string) => fireEvent.click(modes().getByRole('button', { name }));
const createOpenBoard = () => {
  fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
  fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
  fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
    target: { value: '任务发现 Bot' },
  });
  fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '开放协作模版' } });
  selectMode('开放');
  fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
};
const createOpenTask = () => {
  fireEvent.click(screen.getAllByRole('button', { name: '新建求助任务' })[0]);
  expect(screen.getByRole('dialog')).toHaveTextContent('开放协作，不限定特定 Bot');
  expect(screen.getByRole('dialog')).not.toHaveTextContent('0 个');
  fireEvent.change(screen.getByRole('textbox', { name: /任务标题/ }), { target: { value: '开放任务' } });
  fireEvent.change(screen.getByRole('textbox', { name: /任务说明/ }), { target: { value: '按角色协作处理问题' } });
  fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
};

beforeAll(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
});
beforeEach(() => {
  useCommunityHelpMockStore.getState().reset();
  jest.clearAllMocks();
  let counter = 0;
  Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: () => `open-${++counter}` });
});

describe('模版开放协作', () => {
  test('新增默认指定 Bot，开放无需圈选即可保存且再次编辑保持开放', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
      target: { value: '任务发现 Bot' },
    });
    expect(modes().getByRole('button', { name: '指定 Bot' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '开放协作模版' } });
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(toast.error).toHaveBeenCalledWith('请至少选择一个 Bot');
    selectMode('开放');
    expect(screen.queryByRole('checkbox', { name: /Coding Bot/ })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveTextContent('不限定特定 Bot');
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('模版协作团队')).toHaveTextContent('开放协作 · 不限定特定 Bot');
    const board = useCommunityHelpMockStore.getState().boards[2];
    expect(board.collaborationMode).toBe('open');
    expect(board.members).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(modes().getByRole('button', { name: '开放' })).toHaveAttribute('aria-pressed', 'true');
    selectMode('指定 Bot');
    expect(screen.getAllByRole('checkbox', { checked: true })).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    expect(toast.error).toHaveBeenLastCalledWith('请至少选择一个 Bot');
  });

  test('范围支持键盘切换并保留未保存的成员角色，取消不修改已保存数据', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    const original = useCommunityHelpMockStore.getState().boards[0];
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · verify' }));
    modes().getByRole('button', { name: '指定 Bot' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(modes().getByRole('button', { name: '开放' })).toHaveFocus();
    expect(screen.queryByRole('button', { name: 'Coding Bot · verify' })).not.toBeInTheDocument();
    selectMode('指定 Bot');
    expect(screen.getByRole('button', { name: 'Coding Bot · verify' })).toHaveAttribute('aria-pressed', 'true');
    selectMode('开放');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCommunityHelpMockStore.getState().boards[0]).toEqual(original);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(modes().getByRole('button', { name: '指定 Bot' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Coding Bot · verify' })).toHaveAttribute('aria-pressed', 'false');
    selectMode('开放');
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    expect(useCommunityHelpMockStore.getState().boards[0].members).toEqual([]);
    expect(screen.getByLabelText('模版协作团队')).not.toHaveTextContent('Coding Bot');
  });

  test('开放不绕过任务来源校验', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
      target: { value: '任务发现 Bot' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '开放协作模版' } });
    selectMode('开放');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Task Discover' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '手动输入' }));
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(toast.error).toHaveBeenCalledWith('请至少启用一个有效任务来源');
    expect(useCommunityHelpMockStore.getState().boards).toHaveLength(2);
  });

  test('开放任务在副屏、产物、卡片和列表诚实展示范围，完成后仍保留开放标识', () => {
    render(<CommunityHelpCenter />);
    createOpenBoard();
    createOpenTask();
    const drawer = within(screen.getByRole('dialog'));
    expect(drawer.getByRole('region', { name: '阶段详情' })).toHaveTextContent('开放协作 · 不限定特定 Bot');
    expect(drawer.getAllByRole('button', { name: /查看.*阶段/ })).toHaveLength(6);
    fireEvent.click(drawer.getByRole('button', { name: '模拟开始协作' }));
    fireEvent.click(drawer.getByRole('button', { name: '模拟推进下一阶段' }));
    expect(drawer.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
    fireEvent.click(drawer.getByRole('button', { name: '交付产物 (1)' }));
    expect(drawer.getByText(/负责 Bot：开放协作 · 不限定特定 Bot/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(screen.getByRole('article')).toHaveTextContent('开放协作');
    expect(screen.getByRole('article')).not.toHaveTextContent('0 个 Bot');
    const views = within(screen.getByRole('group', { name: '任务视图' }));
    fireEvent.click(views.getByRole('button', { name: '列表视图' }));
    expect(screen.getByRole('table')).toHaveTextContent('开放协作 · 不限定特定 Bot');
    expect(screen.getByRole('table')).not.toHaveTextContent('Coding Bot');
    fireEvent.click(screen.getByRole('button', { name: '开放任务' }));
    for (let step = 0; step < 5; step += 1) {
      fireEvent.click(screen.getByRole('button', { name: '模拟推进下一阶段' }));
    }
    expect(screen.getByRole('button', { name: '任务已完成' })).toBeDisabled();
    expect(within(screen.getByRole('dialog')).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(screen.getByRole('table')).toHaveTextContent('开放协作');
  });
});
