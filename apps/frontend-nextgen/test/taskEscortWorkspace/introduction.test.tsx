/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { WorkspaceIntroduction } from '@/pages/TaskEscort/WorkspaceIntroduction';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
  workflowAuthoringService: { createBlank: jest.fn() },
}));
const create = jest.mocked(workflowAuthoringService.createBlank);
const spec = { id: 'first-flow', title: '首个工作流', version: '1.0.0', nodes: [] };
const makeContext = (overrides: Partial<TaskEscortParityContext> = {}): TaskEscortParityContext => ({
  scopeKey: 'user:space',
  userId: 'user',
  ready: false,
  creationReady: true,
  workflowId: '',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
  openCreatedWorkflow: jest.fn(),
  ...overrides,
});
const open = () => fireEvent.click(screen.getByRole('button', { name: '创建第一个工作流' }));
const fill = () => {
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: 'first-flow' } });
  fireEvent.change(screen.getByRole('textbox', { name: '工作流名称' }), { target: { value: '首个工作流' } });
};
beforeEach(() => {
  jest.clearAllMocks();
  create.mockResolvedValue(spec);
});

it('presents the r2 capability loop and getting-started guide without fabricated data or sample actions', () => {
  const { container } = render(<WorkspaceIntroduction context={makeContext()} />);
  expect(screen.getByRole('heading', { name: '任务护航', level: 1 })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: /让任务执行有迹可循/ })).toBeInTheDocument();
  expect(screen.getByText(/工作流编排/)).toBeInTheDocument();
  const loop = screen.getByRole('list', { name: '护航闭环' });
  expect(within(loop).getAllByRole('listitem')).toHaveLength(4);
  for (const label of ['观测运行', '诊断问题', '审阅优化', '验证与复用']) {
    expect(within(loop).getByText(label)).toBeInTheDocument();
  }
  expect(screen.getByText('能力示意 · 非实时数据')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '先从一个工作流开始' })).toBeInTheDocument();
  expect(screen.getAllByRole('button')).toHaveLength(1);
  expect(container.textContent).not.toMatch(/示例|演示模式|成功率|Token|\d+\s*次运行/);
  expect(create).not.toHaveBeenCalled();
});

it('opens the real dialog, does not write on open/cancel, and submits explicit identity only after confirmation', async () => {
  const context = makeContext();
  render(<WorkspaceIntroduction context={context} />);
  open();
  expect(screen.getByRole('dialog', { name: '新建工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '基于已有工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'AI 生成' })).toBeInTheDocument();
  expect(create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(create).not.toHaveBeenCalled();
  open();
  fill();
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  await waitFor(() => expect(context.openCreatedWorkflow).toHaveBeenCalledWith('first-flow'));
  expect(create).toHaveBeenCalledWith(
    { workflowId: 'first-flow', title: '首个工作流' },
    { user_id: 'user' },
    expect.any(AbortSignal),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(context.navigate).not.toHaveBeenCalled();
});

it.each([{ creationReady: false }, { userId: undefined }, { userId: '   ' }])(
  'disables creation when identity is not explicitly ready: %j',
  (overrides) => {
    render(<WorkspaceIntroduction context={makeContext(overrides)} />);
    expect(screen.getByRole('button', { name: '创建第一个工作流' })).toBeDisabled();
    expect(screen.getByText(/请先加载当前用户身份/)).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  },
);

it('keeps permission failures visible without pretending to create or navigating', async () => {
  create.mockRejectedValue(Object.assign(new Error('forbidden'), { status: 403 }));
  const context = makeContext();
  render(<WorkspaceIntroduction context={context} />);
  open();
  fill();
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('403');
  expect(screen.getByRole('textbox', { name: '工作流名称' })).toHaveValue('首个工作流');
  expect(context.openCreatedWorkflow).not.toHaveBeenCalled();
});

it('closes and aborts on identity/scope changes, ignores late replies, and does not reopen an old dialog', async () => {
  let resolve!: (value: typeof spec) => void;
  create.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const context = makeContext();
  const { rerender } = render(<WorkspaceIntroduction context={context} />);
  open();
  fill();
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  const signal = create.mock.calls[0][2]!;
  const next = makeContext({ scopeKey: 'other:space', userId: 'other' });
  rerender(<WorkspaceIntroduction context={next} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(spec));
  expect(context.openCreatedWorkflow).not.toHaveBeenCalled();
  expect(next.openCreatedWorkflow).not.toHaveBeenCalled();
  rerender(<WorkspaceIntroduction context={context} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  open();
  rerender(<WorkspaceIntroduction context={makeContext({ creationReady: false })} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
