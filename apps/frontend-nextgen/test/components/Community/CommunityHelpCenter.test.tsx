/** @jest-environment jsdom */
import { CommunityHelpCenter } from '@/components/Community/HelpCenter';
import { useCommunityHelpMockStore } from '@/stores/communityHelpMockStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

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
  Object.defineProperty(crypto, 'randomUUID', { configurable: true, value: () => `component-${++counter}` });
});

describe('CommunityHelpCenter', () => {
  test('导航、新增、设置及任务说明统一使用模版文案', () => {
    render(<CommunityHelpCenter />);
    expect(screen.getByRole('navigation', { name: '求助模版导航' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '求助模版' })).toBeInTheDocument();
    expect(screen.getByLabelText('模版协作团队')).toBeInTheDocument();
    expect(screen.getByLabelText('模版任务统计')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '求助_new 工作台' })).not.toHaveTextContent('版面');
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
      target: { value: '任务发现 Bot' },
    });
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByRole('heading', { name: '新增模版' })).toBeInTheDocument();
    expect(dialog.getByRole('textbox', { name: /模版名称/ })).toBeInTheDocument();
    expect(dialog.getByRole('textbox', { name: '模版类型' })).toHaveValue('AiCoding');
    expect(dialog.getByRole('textbox', { name: '模版说明' })).toHaveAttribute(
      'placeholder',
      '这个模版主要解决什么问题？',
    );
    expect(dialog.getByRole('button', { name: '创建模版' })).toBeInTheDocument();
    expect(screen.getByRole('dialog')).not.toHaveTextContent('版面');
    fireEvent.click(dialog.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    expect(screen.getByRole('heading', { name: '模版设置' })).toBeInTheDocument();
    expect(screen.getByRole('dialog')).not.toHaveTextContent('版面');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '新建求助任务' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('使用本模版的 6 个 Mock Bot');
    expect(screen.getByRole('dialog')).not.toHaveTextContent('版面');
  });

  test('Mock 初始数据、状态筛选、搜索和清除筛选', () => {
    render(<CommunityHelpCenter />);
    expect(screen.queryByText('Mock 预览')).not.toBeInTheDocument();
    expect(screen.queryByText(/全部内容为示例数据，不连接真实 Bot 或任务来源/)).not.toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(4);
    fireEvent.click(
      within(screen.getByRole('group', { name: '求助任务状态' })).getByRole('button', { name: '已完成' }),
    );
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    fireEvent.change(screen.getByRole('textbox', { name: '搜索求助任务' }), { target: { value: '没有这个任务' } });
    expect(screen.getByText('没有匹配的求助任务')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(screen.getAllByRole('article')).toHaveLength(4);
  });

  test('默认展示两个模版，TC-AiCoding 可独立创建任务且切换后不串版', () => {
    render(<CommunityHelpCenter />);
    const boards = within(screen.getByRole('group', { name: '求助模版' }));
    expect(boards.getAllByRole('button').map((button) => button.textContent)).toEqual([
      '全图风控AiCoding',
      'TC-AiCoding',
    ]);
    expect(boards.getByRole('button', { name: '全图风控AiCoding' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(boards.getByRole('button', { name: 'TC-AiCoding' }));
    expect(screen.getByRole('heading', { name: 'TC-AiCoding' })).toBeInTheDocument();
    expect(screen.getByText('这个模版还没有求助任务')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: '新建求助任务' })[0]);
    fireEvent.change(screen.getByRole('textbox', { name: /任务标题/ }), { target: { value: 'TC 模版任务' } });
    fireEvent.change(screen.getByRole('textbox', { name: /任务说明/ }), { target: { value: '仅在 TC 模版协作' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    expect(within(screen.getByRole('dialog')).getByRole('heading', { name: 'TC 模版任务' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收起任务副屏' }));
    expect(screen.getAllByRole('article')).toHaveLength(1);
    fireEvent.click(boards.getByRole('button', { name: '全图风控AiCoding' }));
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(screen.queryByRole('button', { name: 'TC 模版任务' })).not.toBeInTheDocument();
    fireEvent.click(boards.getByRole('button', { name: 'TC-AiCoding' }));
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'TC 模版任务' })).toBeInTheDocument();
  });

  test('取消模版编辑不写入；多 Bot 多角色配置可以保存', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '不保存的修改' } });
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCommunityHelpMockStore.getState().boards[0].name).toBe('全图风控AiCoding');
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · verify' }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('模版设置已更新（Mock）');
    const members = useCommunityHelpMockStore.getState().boards[0].members;
    expect(members).toHaveLength(6);
    expect(members.find((member) => member.botId === 'mock-coder')?.roles).toEqual(['代码', 'verify']);
  });

  test('新增模版要求每个选中 Bot 有角色，保存后自动切换到空模版', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新增模版' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Dima 项目空间/ }), { target: { value: '示例空间' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Task Discover Bot/ }), {
      target: { value: '任务发现 Bot' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), { target: { value: '前端协作' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Coding Bot/ }));
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(toast.error).toHaveBeenCalledWith('请为每个选中的 Bot 分配至少一个角色');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · 代码' }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · verify' }));
    fireEvent.click(screen.getByRole('button', { name: '创建模版' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('group', { name: '求助模版' })).getByRole('button', { name: '前端协作' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('这个模版还没有求助任务')).toBeInTheDocument();
    fireEvent.click(
      within(screen.getByRole('group', { name: '求助模版' })).getByRole('button', { name: '全图风控AiCoding' }),
    );
    expect(screen.getAllByRole('article')).toHaveLength(4);
  });

  test('任务可选择 Task Discover 来源并打开副屏，推进后列表同步', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新建求助任务' }));
    fireEvent.change(screen.getByRole('textbox', { name: /任务标题/ }), { target: { value: '修复登录页面问题' } });
    fireEvent.change(screen.getByRole('textbox', { name: /任务说明/ }), {
      target: { value: '复现后修复，并补充回归测试' },
    });
    await user.click(screen.getByRole('combobox', { name: /任务来源/ }));
    await user.click(screen.getByRole('option', { name: 'Task Discover' }));
    fireEvent.change(screen.getByRole('textbox', { name: /来源任务编号/ }), { target: { value: 'TD-NEW' } });
    fireEvent.click(screen.getByRole('button', { name: '创建 Mock 任务' }));
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByRole('heading', { name: '修复登录页面问题' })).toBeInTheDocument();
    expect(within(drawer).getByTestId('task-overview-v2')).toBeInTheDocument();
    expect(within(drawer).getByText(/Task Discover.*TD-NEW/)).toBeInTheDocument();
    expect(within(drawer).getByText('第1棒 需求采集')).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟开始协作' }));
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟推进下一阶段' }));
    expect(within(drawer).getByText('第1棒 需求采集')).toHaveStyle({ textDecoration: 'line-through' });
    expect(within(drawer).getByText('1 项')).toBeInTheDocument();
    expect(within(drawer).getByText('需求采集 · 需求文档')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收起任务副屏' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const card = screen.getByRole('button', { name: '修复登录页面问题' }).closest('article')!;
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
  });

  test('只允许模版启用的来源，取消创建不增加任务', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '手动输入' }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    fireEvent.click(screen.getByRole('button', { name: '新建求助任务' }));
    await user.click(screen.getByRole('combobox', { name: /任务来源/ }));
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option', { name: 'Task Discover' })).toBeInTheDocument();
    await user.click(screen.getByRole('option', { name: 'Task Discover' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCommunityHelpMockStore.getState().tasks).toHaveLength(4);
  });

  test('需关注任务可以模拟恢复；完成任务不可推进', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '排查任务进度偶发停滞，补齐异常恢复路径' }));
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByTestId('task-overview-v2')).toBeInTheDocument();
    expect(within(drawer).getByRole('img', { name: '任务挂起' })).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟解除阻塞' }));
    expect(within(drawer).getByRole('button', { name: '模拟推进下一阶段' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '收起任务副屏' }));
    fireEvent.click(screen.getByRole('button', { name: '优化社区移动端任务卡片的响应式布局' }));
    expect(screen.getByRole('button', { name: '任务已完成' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '选中节点 第6棒 pr' }));
    expect(screen.getByRole('button', { name: '选中节点 第6棒 pr' })).toHaveStyle({
      border: '1px solid rgb(37, 99, 235)',
    });

    const artifacts = within(screen.getByRole('dialog')).getByTestId('artifacts-section');
    expect(within(artifacts).getAllByText('文件')).toHaveLength(5);
    expect(within(artifacts).getAllByText('代码')).toHaveLength(1);
    expect(within(artifacts).getByText('pr · PR 摘要').closest('li')).toHaveStyle({
      border: '1px solid rgb(37, 99, 235)',
    });
    expect(within(artifacts).getByText('第1棒产物')).toBeInTheDocument();
    expect(within(artifacts).getByText('最终产物')).toBeInTheDocument();
  });

  test('切走再挂载仍保留本次浏览会话内的 Mock 修改', () => {
    const view = render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '模版设置' }));
    fireEvent.change(screen.getByRole('textbox', { name: /模版名称/ }), {
      target: { value: '全图风控AiCoding · 试验' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    view.unmount();
    render(<CommunityHelpCenter />);
    expect(
      within(screen.getByRole('group', { name: '求助模版' })).getByRole('button', { name: '全图风控AiCoding · 试验' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
});
