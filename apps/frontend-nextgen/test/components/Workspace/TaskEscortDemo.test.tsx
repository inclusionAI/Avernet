/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { TaskEscortDemoPanel } from '@/components/Workspace/TaskEscortDemo';
import { INITIAL_NODES } from '@/components/Workspace/TaskEscortDemo/model';
import { serializeWorkflow } from '@/components/Workspace/TaskEscortDemo/yaml';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

function Host() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState(0);
  return (
    <>
      <Button
        data-task-escort-trigger
        aria-controls="task-escort-demo-panel"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        任务护航演示
      </Button>
      <Button onClick={() => setMessages((count) => count + 1)}>聊天背景 {messages}</Button>
      <TaskEscortDemoPanel open={open} onOpenChange={setOpen} botName="演示 Bot" />
    </>
  );
}
async function openDemo() {
  const user = userEvent.setup();
  render(<Host />);
  await user.click(screen.getByRole('button', { name: '任务护航演示' }));
  return user;
}

beforeAll(() => {
  // JSDOM lacks these browser APIs used by the real Radix Select implementation.
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: jest.fn() });
});

it('uses the stable drawer API, simulated metrics and keyboard-navigable primary tabs', async () => {
  const user = await openDemo();
  const dialog = screen.getByRole('dialog', { name: /任务护航/ });
  expect(dialog).toHaveAttribute('id', 'task-escort-demo-panel');
  expect(dialog).not.toHaveAttribute('aria-modal', 'true');
  expect(dialog).toHaveAccessibleDescription('演示沙盒 · 所有运行记录、指标与节点状态均为虚构');
  expect(screen.getByRole('button', { name: '关闭任务护航' })).toHaveFocus();
  expect(screen.getByRole('region', { name: '模拟运行指标' })).toHaveTextContent('80.0%');
  const tabs = within(screen.getByRole('group', { name: '任务护航视图' }));
  await user.click(tabs.getByRole('button', { name: '运行概览' }));
  await user.keyboard('{ArrowRight}');
  expect(tabs.getByRole('button', { name: '编辑器' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('region', { name: '演示工作流编辑器' })).toBeInTheDocument();
  expect(tabs.getByRole('button', { name: '编辑器' })).toHaveFocus();
  await user.click(tabs.getByRole('button', { name: '运行概览' }));
  await user.click(screen.getByRole('button', { name: '节点分析' }));
  expect(screen.getByText('2640 ms')).toBeInTheDocument();
});

it('removes only the requested header and selector labels in overview and editor, with valid dialog references', async () => {
  const warn = jest.spyOn(console, 'warn');
  try {
    const user = await openDemo();
    const dialog = screen.getByRole('dialog', { name: '任务护航' });
    const assertChrome = () => {
      const header = within(dialog.querySelector('header')!);
      expect(header.getByRole('heading', { name: '任务护航' })).toBeInTheDocument();
      expect(header.queryByText('DEMO')).not.toBeInTheDocument();
      expect(screen.queryByText('全部为模拟数据 · 不调用后台，不触发真实运行或部署')).not.toBeInTheDocument();
      expect(screen.queryByText('本地交互演示 · 与真实工作流隔离')).not.toBeInTheDocument();
      expect(screen.queryByText('5 节点 · 示意编排')).not.toBeInTheDocument();
      const selector = screen.getByRole('combobox', { name: '切换工作流' });
      expect(selector).toHaveTextContent('OneModel 攻防工作流');
      expect(selector).toHaveTextContent('onemodel-risk-defense');
      expect(selector.parentElement).not.toHaveTextContent('v0.8.2-demo');
      expect(screen.getByText('当前 Bot · 演示 Bot')).toBeInTheDocument();
      expect(dialog).toHaveAccessibleDescription('演示沙盒 · 所有运行记录、指标与节点状态均为虚构');
      for (const attribute of ['aria-labelledby', 'aria-describedby']) {
        const ids = dialog.getAttribute(attribute)?.split(/\s+/);
        expect(ids?.length).toBeGreaterThan(0);
        ids?.forEach((id) => expect(document.getElementById(id)).toBeInTheDocument());
      }
    };
    assertChrome();
    expect(screen.getByText('演示快照 · 2026-11-18 14:30（模拟，非实时）')).toBeInTheDocument();
    expect(screen.getByText('仅模拟数据 · 列表筛选不影响概览')).toBeInTheDocument();
    expect(screen.getByText('仅展示内置 DEMO 记录，不读取真实运行历史。')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: '模拟运行记录' })).toHaveTextContent('v0.8.2-demo');
    await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
    expect(screen.getByRole('option', { name: /OneModel 攻防工作流/ })).toBeInTheDocument();
    expect(screen.getByText('模拟权限 · 仅展示可查看项')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: '编辑器' }));
    assertChrome();
    expect(screen.getByText('示意编排 · 5 节点精简示例，并非线上完整工作流复刻')).toBeInTheDocument();
    expect(screen.getByText(/不会运行、部署或发送任何数据/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    expect(screen.getByRole('textbox', { name: '工作流 YAML · DEMO' })).toBeInTheDocument();
    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/Missing.*Description|aria-describedby/);
  } finally {
    warn.mockRestore();
  }
});

function expectDedicatedRunDetail(runId: string) {
  const main = screen.getByRole('main');
  const detail = within(main).getByRole('region', { name: '模拟运行详情' });
  expect(main.firstElementChild).toBe(detail);
  expect(main.scrollTop).toBe(0);
  const title = within(detail).getByRole('heading', { name: `运行详情 · ${runId}` });
  expect(title).toHaveFocus();
  expect(screen.queryByRole('heading', { name: '工作流运行概览' })).not.toBeInTheDocument();
  for (const name of ['模拟运行指标', '模拟实时状态分布', '模拟运行趋势', '运行风险摘要']) {
    expect(screen.queryByRole('region', { name })).not.toBeInTheDocument();
  }
  expect(screen.queryByRole('group', { name: '运行分析视图' })).not.toBeInTheDocument();
  expect(screen.queryByRole('table', { name: '模拟运行记录' })).not.toBeInTheDocument();
  expect(within(detail).getByText('DEMO')).toBeInTheDocument();
  return detail;
}

it.each([
  {
    target: 'RUN ID',
    buttonName: 'demo-run-1011',
    runId: 'demo-run-1011',
    keyword: '1011',
    status: '等待中',
  },
  {
    target: 'right 查看',
    buttonName: '查看运行 demo-run-1006',
    runId: 'demo-run-1006',
    keyword: '1006',
    status: '失败',
  },
])('opens $target as a focused, top-level detail for the exact run and returns to the filtered list', async ({
  buttonName, runId, keyword, status,
}) => {
  const user = await openDemo();
  await user.click(screen.getByRole('combobox', { name: '概览时间范围' }));
  await user.click(screen.getByRole('option', { name: '最近 7 天' }));
  await user.click(screen.getByRole('combobox', { name: '运行状态筛选' }));
  await user.click(screen.getByRole('option', { name: status }));
  await user.type(screen.getByRole('textbox', { name: '运行关键词' }), keyword);
  const metrics = screen.getByRole('region', { name: '模拟运行指标' }).textContent;
  const table = within(screen.getByRole('table', { name: '模拟运行记录' }));
  expect(table.getByRole('button', { name: runId })).not.toBe(
    table.getByRole('button', { name: `查看运行 ${runId}` }),
  );
  screen.getByRole('main').scrollTop = 480;
  await user.click(table.getByRole('button', { name: buttonName }));
  const detail = expectDedicatedRunDetail(runId);
  await user.click(within(detail).getByRole('button', { name: '返回列表' }));
  expect(screen.queryByRole('region', { name: '模拟运行详情' })).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '概览时间范围' })).toHaveTextContent('最近 7 天');
  expect(screen.getByRole('combobox', { name: '运行状态筛选' })).toHaveTextContent(status);
  expect(screen.getByRole('textbox', { name: '运行关键词' })).toHaveValue(keyword);
  const restored = within(screen.getByRole('table', { name: '模拟运行记录' }));
  expect(restored.getAllByRole('row')).toHaveLength(2);
  expect(restored.getByRole('button', { name: runId })).toBeInTheDocument();
  expect(screen.getByText('1 条演示记录')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '模拟运行指标' }).textContent).toBe(metrics);
});

it('opens the RUN ID button with the keyboard', async () => {
  const user = await openDemo();
  act(() => screen.getByRole('button', { name: 'demo-run-1012' }).focus());
  await user.keyboard('{Enter}');
  const detail = expectDedicatedRunDetail('demo-run-1012');
  expect(detail).toHaveTextContent('此执行路径仅用于展示运行详情，不代表真实执行结果');
});

it('opens the risk summary run in the same dedicated detail, independently of list filters', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('combobox', { name: '运行状态筛选' }));
  await user.click(screen.getByRole('option', { name: '失败' }));
  await user.type(screen.getByRole('textbox', { name: '运行关键词' }), '1006');
  await user.click(screen.getByRole('button', { name: '节点分析' }));
  screen.getByRole('main').scrollTop = 360;
  await user.click(
    within(screen.getByRole('region', { name: '运行风险摘要' })).getByRole('button', { name: '查看运行' }),
  );
  const detail = expectDedicatedRunDetail('demo-run-1011');
  expect(detail).toHaveTextContent('不会通知真实审批人');
  await user.click(within(detail).getByRole('button', { name: '返回列表' }));
  expect(screen.getByRole('combobox', { name: '运行状态筛选' })).toHaveTextContent('失败');
  expect(screen.getByRole('textbox', { name: '运行关键词' })).toHaveValue('1006');
  expect(screen.getByRole('table', { name: '模拟运行记录' })).toHaveTextContent('demo-run-1006');
  expect(screen.getByRole('button', { name: '运行列表' })).toHaveAttribute('aria-pressed', 'true');
});

it('filters status/keywords without changing overview metrics and handles an empty result', async () => {
  const user = await openDemo();
  const metrics = screen.getByRole('region', { name: '模拟运行指标' });
  const snapshot = metrics.textContent;
  await user.click(screen.getByRole('combobox', { name: '运行状态筛选' }));
  await user.click(screen.getByRole('option', { name: '失败' }));
  expect(screen.getByText('1 条演示记录')).toBeInTheDocument();
  expect(screen.getByRole('table', { name: '模拟运行记录' })).toHaveTextContent('demo-run-1006');
  expect(metrics.textContent).toBe(snapshot);
  await user.type(screen.getByRole('textbox', { name: '运行关键词' }), 'not-a-demo-run');
  expect(screen.getByText('没有匹配的演示记录')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '重置筛选' }));
  expect(screen.getByText('9 条演示记录')).toBeInTheDocument();
  await user.type(screen.getByRole('textbox', { name: '运行关键词' }), '1011');
  expect(screen.getByText('1 条演示记录')).toBeInTheDocument();
  expect(metrics.textContent).toBe(snapshot);
});

it('uses terminal-only success rates and includes failed, terminated and cancelled in abnormal totals', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('combobox', { name: '概览时间范围' }));
  await user.click(screen.getByRole('option', { name: '最近 7 天' }));
  const metrics = screen.getByRole('region', { name: '模拟运行指标' });
  expect(metrics).toHaveTextContent('62.5%');
  expect(screen.getByText('异常结束').parentElement?.parentElement).toHaveTextContent('3');
  expect(screen.getByText('12 条演示记录')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '查看11/16成功率趋势' }));
  expect(screen.getByRole('img', { name: /11\/16：0%/ })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /11\/16：1 个实例/ })).toBeInTheDocument();
});

it('opens a run detail then locates its node in the editor', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('button', { name: '查看运行 demo-run-1011' }));
  const detail = expectDedicatedRunDetail('demo-run-1011');
  expect(detail).toHaveTextContent('不会通知真实审批人');
  await user.click(within(detail).getByRole('button', { name: '在编辑器定位人工审批' }));
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('人工审批');
  expect(screen.getByRole('button', { name: '选择节点 人工审批' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: '选择节点 人工审批' })).toHaveFocus();
  expect(screen.getByRole('main').scrollTop).toBe(0);
  expect(screen.queryByRole('region', { name: '模拟运行详情' })).not.toBeInTheDocument();
});

it('supports risk-to-editor navigation, node search, canvas zoom and synchronized draft editing', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('button', { name: '定位节点' }));
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('策略评估');
  expect(screen.getByRole('button', { name: '选择节点 策略评估' })).toHaveFocus();
  await user.type(screen.getByRole('textbox', { name: '搜索示例节点' }), '风险');
  expect(screen.queryByRole('button', { name: '从节点库选择 开始' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '从节点库选择 风险识别' }));
  await user.click(screen.getByRole('button', { name: '放大画布' }));
  expect(screen.getByText('125%')).toBeInTheDocument();
  await user.clear(screen.getByRole('textbox', { name: '节点名称' }));
  await user.type(screen.getByRole('textbox', { name: '节点名称' }), '演示风险检查');
  await user.click(screen.getByRole('button', { name: 'YAML' }));
  expect((screen.getByRole('textbox', { name: '工作流 YAML · DEMO' }) as HTMLTextAreaElement).value).toContain(
    '演示风险检查',
  );
  await user.click(screen.getByRole('button', { name: '保存本地草稿' }));
  expect(screen.getByRole('status')).toHaveTextContent('仅当前页面内存');
  expect(screen.getByText('草稿已同步')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '重置演示' }));
  expect((screen.getByRole('textbox', { name: '工作流 YAML · DEMO' }) as HTMLTextAreaElement).value).not.toContain(
    '演示风险检查',
  );
});

it('validates YAML, blocks invalid saves, and applies valid YAML back to node properties', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  await user.click(screen.getByRole('button', { name: 'YAML' }));
  const input = screen.getByRole('textbox', { name: '工作流 YAML · DEMO' });
  fireEvent.change(input, { target: { value: 'nodes: [' } });
  expect(screen.getByRole('alert')).toHaveTextContent('YAML 语法无效');
  expect(screen.getByRole('button', { name: '保存本地草稿' })).toBeDisabled();
  const next = INITIAL_NODES.map((node) =>
    node.id === 'strategy' ? { ...node, name: '本地策略演示', timeoutSeconds: 45 } : node,
  );
  fireEvent.change(input, { target: { value: serializeWorkflow(next) } });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '可视化' }));
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('本地策略演示');
  expect(screen.getByRole('combobox', { name: '节点超时配置' })).toHaveTextContent('45 秒');
});

it('preserves outside interaction and in-memory draft, expands/restores, closes via button and Escape, and restores trigger focus', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('button', { name: '聊天背景 0' }));
  expect(screen.getByRole('button', { name: '聊天背景 1' })).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '展开抽屉' }));
  expect(screen.getByRole('dialog')).toHaveAttribute('data-expanded', 'true');
  await user.click(screen.getByRole('button', { name: '还原抽屉' }));
  expect(screen.getByRole('dialog')).toHaveAttribute('data-expanded', 'false');
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  fireEvent.change(screen.getByRole('textbox', { name: '节点名称' }), { target: { value: '保留的草稿' } });
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole('button', { name: '任务护航演示' })).toHaveFocus());
  await user.click(screen.getByRole('button', { name: '任务护航演示' }));
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('保留的草稿');
  // The autofocus target is an IconButton: its passive Tooltip must not eat Escape.
  expect(screen.getByRole('button', { name: '关闭任务护航' })).toHaveFocus();
  await screen.findByRole('tooltip', { name: '关闭任务护航' });
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole('button', { name: '任务护航演示' })).toHaveFocus());
});

it('closes a portaled Select with Escape before dismissing the drawer, while outside/chat Escape closes the drawer', async () => {
  const user = await openDemo();
  await user.click(screen.getByRole('combobox', { name: '运行状态筛选' }));
  expect(screen.getByRole('listbox')).toBeInTheDocument();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '聊天背景 0' }));
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole('button', { name: '任务护航演示' })).toHaveFocus());
});

it('resets the main scroll on risk navigation and direct primary-view changes without losing the editor draft', async () => {
  const user = await openDemo();
  const overviewMain = screen.getByRole('main');
  overviewMain.scrollTop = 480;
  await user.click(screen.getByRole('button', { name: '定位节点' }));
  const editorMain = screen.getByRole('main');
  expect(editorMain).not.toBe(overviewMain);
  expect(editorMain.scrollTop).toBe(0);
  fireEvent.change(screen.getByRole('textbox', { name: '节点名称' }), { target: { value: '滚动切换保留草稿' } });
  editorMain.scrollTop = 360;
  await user.click(
    within(screen.getByRole('group', { name: '任务护航视图' })).getByRole('button', { name: '运行概览' }),
  );
  expect(screen.getByRole('main')).not.toBe(editorMain);
  expect(screen.getByRole('main').scrollTop).toBe(0);
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  expect(screen.getByRole('main').scrollTop).toBe(0);
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('滚动切换保留草稿');
});
