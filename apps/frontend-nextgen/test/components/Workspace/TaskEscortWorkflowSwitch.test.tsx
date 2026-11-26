/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { TaskEscortDemoPanel } from '@/components/Workspace/TaskEscortDemo';
import type { DemoWorkflow } from '@/components/Workspace/TaskEscortDemo/model';
import { getOverview } from '@/components/Workspace/TaskEscortDemo/model';
import { DEMO_WORKFLOWS, getAccessibleDemoWorkflows } from '@/components/Workspace/TaskEscortDemo/workflowCatalog';
import { serializeWorkflow } from '@/components/Workspace/TaskEscortDemo/yaml';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

function Host({ workflows = DEMO_WORKFLOWS }: { workflows?: readonly DemoWorkflow[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button data-task-escort-trigger onClick={() => setOpen(!open)}>工作流切换演示</Button>
      <TaskEscortDemoPanel open={open} onOpenChange={setOpen} workflows={workflows} />
    </>
  );
}
function fixture(id: string): DemoWorkflow {
  const result = DEMO_WORKFLOWS.find((workflow) => workflow.id === id);
  if (!result) throw new Error(`Missing workflow fixture: ${id}`);
  return result;
}
async function openDemo() {
  const user = userEvent.setup();
  const rendered = render(<Host />);
  await user.click(screen.getByRole('button', { name: '工作流切换演示' }));
  return { user, ...rendered };
}
async function selectWorkflow(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
  await user.click(screen.getByRole('option', { name: new RegExp(name) }));
}
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: jest.fn() });
});

it('only lists simulated workflows that the viewer can access and marks the current workflow', async () => {
  const { user } = await openDemo();
  const allowed = getAccessibleDemoWorkflows(DEMO_WORKFLOWS);
  expect(allowed).toHaveLength(3);
  expect(allowed.every((workflow) => workflow.canView)).toBe(true);
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('OneModel 攻防工作流');
  await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
  expect(screen.getAllByRole('option')).toHaveLength(allowed.length);
  for (const workflow of allowed) {
    expect(screen.getByRole('option', { name: new RegExp(workflow.name) })).toHaveTextContent(workflow.id);
  }
  expect(screen.getByRole('option', { name: /OneModel 攻防工作流/ })).toHaveAttribute('aria-selected', 'true');
  const denied = fixture('restricted-operations');
  expect(screen.queryByRole('option', { name: new RegExp(denied.name) })).not.toBeInTheDocument();
  expect(screen.queryByText(denied.id)).not.toBeInTheDocument();
  expect(screen.getAllByText(/模拟权限/).length).toBeGreaterThan(0);
});

it('switches version, metrics, runs and nodes, rather than changing only the title', async () => {
  const { user } = await openDemo();
  const inspection = fixture('daily-metrics-inspection');
  const before = screen.getByRole('region', { name: '模拟运行指标' }).textContent;
  await selectWorkflow(user, inspection.name);
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent(inspection.id);
  expect(screen.getByRole('table', { name: '模拟运行记录' })).toHaveTextContent(inspection.version);
  const metrics = screen.getByRole('region', { name: '模拟运行指标' });
  expect(metrics).toHaveTextContent(getOverview('24h', inspection.runs).successRate);
  expect(metrics.textContent).not.toBe(before);
  expect(screen.getByRole('table', { name: '模拟运行记录' })).toHaveTextContent(inspection.runs[0].id);
  expect(screen.queryByRole('button', { name: '查看运行 demo-run-1012' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('异常分析');
  await user.click(screen.getByRole('button', { name: 'YAML' }));
  expect(screen.getByRole('textbox', { name: '工作流 YAML · DEMO' })).toHaveValue(
    serializeWorkflow(inspection.nodes, inspection),
  );
});

it('retains independent unsaved and saved editor drafts when switching or reopening', async () => {
  const { user } = await openDemo();
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  fireEvent.change(screen.getByRole('textbox', { name: '节点名称' }), { target: { value: '攻防未保存草稿' } });
  expect(screen.getByText('有未保存的本地修改')).toBeInTheDocument();
  await selectWorkflow(user, '每日指标巡检');
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('异常分析');
  fireEvent.change(screen.getByRole('textbox', { name: '节点名称' }), { target: { value: '巡检独立草稿' } });
  await user.click(screen.getByRole('button', { name: '保存本地草稿' }));
  expect(screen.getByText('草稿已同步')).toBeInTheDocument();
  await selectWorkflow(user, 'OneModel 攻防工作流');
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('攻防未保存草稿');
  expect(screen.getByText('有未保存的本地修改')).toBeInTheDocument();
  await selectWorkflow(user, '每日指标巡检');
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('巡检独立草稿');
  expect(screen.getByText('草稿已同步')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await user.click(screen.getByRole('button', { name: '工作流切换演示' }));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('每日指标巡检');
  expect(screen.getByRole('textbox', { name: '节点名称' })).toHaveValue('巡检独立草稿');
});

it('keeps invalid YAML isolated and rejects YAML belonging to a different workflow', async () => {
  const { user } = await openDemo();
  await user.click(screen.getByRole('button', { name: '编辑器' }));
  await user.click(screen.getByRole('button', { name: 'YAML' }));
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 YAML · DEMO' }), { target: { value: 'nodes: [' } });
  expect(screen.getByRole('alert')).toHaveTextContent('YAML 语法无效');
  await selectWorkflow(user, '每日指标巡检');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'YAML' }));
  const original = fixture('onemodel-risk-defense');
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 YAML · DEMO' }), {
    target: { value: serializeWorkflow(original.nodes, original) },
  });
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存本地草稿' })).toBeDisabled();
  await selectWorkflow(user, 'OneModel 攻防工作流');
  expect(screen.getByRole('textbox', { name: '工作流 YAML · DEMO' })).toHaveValue('nodes: [');
  expect(screen.getByRole('alert')).toHaveTextContent('YAML 语法无效');
});

it('clears the previous workflow filters and run details on a workflow change', async () => {
  const { user } = await openDemo();
  await user.type(screen.getByRole('textbox', { name: '运行关键词' }), '1006');
  await user.click(screen.getByRole('button', { name: '查看运行 demo-run-1006' }));
  expect(screen.getByRole('region', { name: '模拟运行详情' })).toHaveTextContent('demo-run-1006');
  await selectWorkflow(user, '每日指标巡检');
  expect(screen.queryByRole('region', { name: '模拟运行详情' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '运行关键词' })).toHaveValue('');
  expect(screen.getByRole('table', { name: '模拟运行记录' })).not.toHaveTextContent('demo-run-1006');
  await selectWorkflow(user, 'OneModel 攻防工作流');
  expect(screen.getByRole('textbox', { name: '运行关键词' })).toHaveValue('');
  expect(screen.getByText('9 条演示记录')).toBeInTheDocument();
});

it('falls back immediately after access is revoked and renders an empty permission state safely', async () => {
  const { user, rerender } = await openDemo();
  await selectWorkflow(user, '每日指标巡检');
  const revoked = DEMO_WORKFLOWS.map((workflow) =>
    workflow.id === 'daily-metrics-inspection' ? { ...workflow, canView: false } : workflow,
  );
  rerender(<Host workflows={revoked} />);
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('OneModel 攻防工作流');
  expect(screen.queryByRole('button', { name: /查看运行 demo-inspection-/ })).not.toBeInTheDocument();
  rerender(<Host workflows={revoked.map((workflow) => ({ ...workflow, canView: false }))} />);
  expect(screen.getByText('无可访问的工作流')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toBeDisabled();
  expect(screen.queryByRole('region', { name: '模拟运行指标' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '演示工作流编辑器' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('supports keyboard selection and dismisses the workflow menu before dismissing the drawer', async () => {
  const { user } = await openDemo();
  const trigger = screen.getByRole('combobox', { name: '切换工作流' });
  await user.click(trigger);
  await user.keyboard('{Home}{ArrowDown}{Enter}');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(trigger).toHaveTextContent('每日指标巡检');
  expect(trigger).toHaveFocus();
  await user.click(trigger);
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('handles a permitted workflow with no runs without stale metrics, risks or NaN', async () => {
  const { user } = await openDemo();
  await selectWorkflow(user, '巡检报告汇总');
  expect(screen.getByText('0 条演示记录')).toBeInTheDocument();
  const metrics = screen.getByRole('region', { name: '模拟运行指标' });
  expect(metrics.textContent).not.toMatch(/NaN|Infinity/);
  expect(metrics).toHaveTextContent('—');
  expect(screen.queryByText('策略评估存在阻塞')).not.toBeInTheDocument();
  const trend = screen.getByRole('region', { name: '模拟运行趋势' });
  expect(within(trend).getByRole('img', { name: /实例.*0 个实例/ })).toBeInTheDocument();
});
