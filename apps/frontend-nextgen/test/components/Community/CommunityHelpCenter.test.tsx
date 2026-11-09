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
  test('Mock 初始数据、状态筛选、搜索和清除筛选', () => {
    render(<CommunityHelpCenter />);
    expect(screen.getByText('Mock 预览')).toBeInTheDocument();
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

  test('取消版面编辑不写入；多 Bot 多角色配置可以保存', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '版面设置' }));
    fireEvent.change(screen.getByRole('textbox', { name: /版面名称/ }), { target: { value: '不保存的修改' } });
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCommunityHelpMockStore.getState().boards[0].name).toBe('AiCoding');
    fireEvent.click(screen.getByRole('button', { name: '版面设置' }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · verify' }));
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const members = useCommunityHelpMockStore.getState().boards[0].members;
    expect(members).toHaveLength(6);
    expect(members.find((member) => member.botId === 'mock-coder')?.roles).toEqual(['代码', 'verify']);
  });

  test('新增版面要求每个选中 Bot 有角色，保存后自动切换到空版面', () => {
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '新增版面' }));
    fireEvent.change(screen.getByRole('textbox', { name: /版面名称/ }), { target: { value: '前端协作' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Coding Bot/ }));
    fireEvent.click(screen.getByRole('button', { name: '创建版面' }));
    expect(toast.error).toHaveBeenCalledWith('请为每个选中的 Bot 分配至少一个角色');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · 代码' }));
    fireEvent.click(screen.getByRole('button', { name: 'Coding Bot · verify' }));
    fireEvent.click(screen.getByRole('button', { name: '创建版面' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('group', { name: '求助版面' })).getByRole('button', { name: '前端协作' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('这个版面还没有求助任务')).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('group', { name: '求助版面' })).getByRole('button', { name: 'AiCoding' }));
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
    expect(within(drawer).getByText('Task Discover · TD-NEW')).toBeInTheDocument();
    expect(within(drawer).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟开始协作' }));
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟推进下一阶段' }));
    expect(within(drawer).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
    fireEvent.click(within(drawer).getByRole('button', { name: '执行动态' }));
    expect(within(drawer).getByText(/需求采集.*已模拟完成/)).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('button', { name: '交付产物 (1)' }));
    expect(within(drawer).getByRole('heading', { name: '需求采集 · 需求文档' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const card = screen.getByRole('button', { name: '修复登录页面问题' }).closest('article')!;
    expect(within(card).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
  });

  test('只允许版面启用的来源，取消创建不增加任务', async () => {
    const user = userEvent.setup();
    render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '版面设置' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dima' }));
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
    expect(within(drawer).getByText(/等待确认：失败重试/)).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('button', { name: '模拟解除阻塞' }));
    expect(within(drawer).getByRole('button', { name: '模拟推进下一阶段' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));
    fireEvent.click(screen.getByRole('button', { name: '优化社区移动端任务卡片的响应式布局' }));
    expect(screen.getByRole('button', { name: '任务已完成' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '查看pr阶段' }));
    expect(within(screen.getByRole('region', { name: '阶段详情' })).getByText(/未创建真实 PR/)).toBeInTheDocument();
  });

  test('切走再挂载仍保留本次浏览会话内的 Mock 修改', () => {
    const view = render(<CommunityHelpCenter />);
    fireEvent.click(screen.getByRole('button', { name: '版面设置' }));
    fireEvent.change(screen.getByRole('textbox', { name: /版面名称/ }), { target: { value: 'AiCoding · 试验' } });
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
    view.unmount();
    render(<CommunityHelpCenter />);
    expect(
      within(screen.getByRole('group', { name: '求助版面' })).getByRole('button', { name: 'AiCoding · 试验' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
});
