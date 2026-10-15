/** @jest-environment jsdom */
import { TaskLoopCard } from '@/components/TaskCards/TaskLoopCard';
import { submitTaskCardAction } from '@/services/tasks/taskCardBridge';
import { clearEvaluationAutoExecuteClaimsForTest } from '@/services/tasks/taskEvaluationAutoExecute';
import { fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/services/tasks/taskCardBridge', () => ({
  submitTaskCardAction: jest.fn(),
}));

const mockedSubmit = submitTaskCardAction as jest.MockedFunction<typeof submitTaskCardAction>;

function cardProps(content: unknown) {
  return {
    params: { content },
    payload: {},
    onAction: jest.fn(),
    onInteraction: jest.fn(),
    tab: { id: 'task-card', type: 'taskCard.TaskLoopCard', title: 'task' },
  };
}

describe('TaskLoopCard', () => {
  beforeEach(() => {
    mockedSubmit.mockClear();
    clearEvaluationAutoExecuteClaimsForTest();
  });

  it('renders a task_ready payload without cardId or global data', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_ready',
          task: {
            task_type: 'dynamic',
            goal: '修复 PR #1243 的命名问题',
            deliverables: ['代码 PR（命名修正）'],
            acceptance_criteria: [],
            constraints: [],
            resources: [],
          },
        })}
      />,
    );

    expect(screen.getByText('任务已就绪')).toBeTruthy();
    expect(screen.getByText('修复 PR #1243 的命名问题')).toBeTruthy();
    expect(screen.getByText('代码 PR（命名修正）')).toBeTruthy();
    expect(document.body.innerHTML).not.toContain('cardId');
  });

  it('renders a JSON-string AixUI body for a task_ready confirmation card', () => {
    const payload = {
      type: 'task_ready',
      task: {
        task_type: 'dynamic',
        goal: '修复 PR #1243 的命名问题',
        deliverables: ['代码 PR（命名修正）'],
        acceptance_criteria: ['CI 通过'],
        constraints: ['不动接口逻辑'],
        resources: [],
      },
    };

    render(<TaskLoopCard {...cardProps(JSON.stringify(payload))} />);

    expect(screen.getByText('任务已就绪')).toBeTruthy();
    expect(screen.getByText('修复 PR #1243 的命名问题')).toBeTruthy();
    expect(screen.queryByText('暂无数据')).toBeNull();
  });

  it('falls back to top-level task fields when a malformed confirmation payload has an empty task object', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_ready',
          task: {},
          goal: '修复 PR #1243 的命名问题',
          deliverables: ['代码 PR（命名修正）'],
          acceptance_criteria: ['CI 通过'],
          constraints: ['不动接口逻辑'],
          resources: [],
        })}
      />,
    );

    expect(screen.getByText('修复 PR #1243 的命名问题')).toBeTruthy();
    expect(screen.getByText('代码 PR（命名修正）')).toBeTruthy();
    expect(screen.queryByText('未设定')).toBeNull();
  });

  it('fills missing nested confirmation fields from the legacy top-level payload', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_ready',
          task: { task_type: 'dynamic', goal: '修复 PR #1243 的命名问题' },
          deliverables: ['代码 PR（命名修正）'],
          acceptance_criteria: ['CI 通过'],
          constraints: ['不动接口逻辑'],
          resources: [],
        })}
      />,
    );

    expect(screen.getByText('修复 PR #1243 的命名问题')).toBeTruthy();
    expect(screen.getByText('代码 PR（命名修正）')).toBeTruthy();
    expect(screen.getByText('CI 通过')).toBeTruthy();
    expect(screen.getByText('不动接口逻辑')).toBeTruthy();
  });

  it('dispatches the task action through the public bridge service', () => {
    const task = {
      task_type: 'dynamic',
      goal: '执行一个任务',
      deliverables: [],
      acceptance_criteria: [],
      constraints: [],
      resources: [],
    };
    render(<TaskLoopCard {...cardProps({ type: 'task_ready', task })} />);

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(mockedSubmit).toHaveBeenCalledWith('执行任务', {
      __taskAction: 'execute',
      task,
    });
  });

  it('auto-executes a pre-confirmed evaluation card exactly once and hides manual actions', () => {
    const task = {
      task_type: 'dynamic' as const,
      goal: '完成存储行业尽调',
      deliverables: ['尽调报告'],
      acceptance_criteria: ['明确投资价值'],
      constraints: [],
      resources: [],
    };
    const hidden_flags = {
      evaluation_auto_execute: true as const,
      evaluation_auto_execute_id: 'evaluation-delivery-1',
    };

    const first = render(<TaskLoopCard {...cardProps({ type: 'task_ready', task, hidden_flags })} />);

    expect(mockedSubmit).toHaveBeenCalledTimes(1);
    expect(mockedSubmit).toHaveBeenCalledWith('执行任务', {
      __taskAction: 'execute',
      __taskAutoExecute: true,
      task: expect.objectContaining({
        goal: '完成存储行业尽调',
        hidden_flags,
      }),
    });
    expect(screen.getByText('评测任务已确认，正在自动提交')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '确认' })).toBeNull();
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull();

    first.unmount();
    render(<TaskLoopCard {...cardProps({ type: 'task_ready', task, hidden_flags })} />);
    expect(mockedSubmit).toHaveBeenCalledTimes(1);
  });

  it('does not auto-execute an evaluation card without a unique delivery id', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_ready',
          task: {
            task_type: 'dynamic',
            goal: '完成存储行业尽调',
            deliverables: ['尽调报告'],
            acceptance_criteria: ['明确投资价值'],
            constraints: [],
            resources: [],
          },
          hidden_flags: { evaluation_auto_execute: true },
        })}
      />,
    );

    expect(mockedSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '确认' })).toBeTruthy();
  });

  it('auto-executes an evaluation card with smoke flags and keeps the action single-shot', () => {
    const task = {
      task_type: 'dynamic' as const,
      goal: '完成存储行业尽调',
      deliverables: ['尽调报告'],
      acceptance_criteria: ['明确投资价值'],
      constraints: [],
      resources: [],
    };
    const hidden_flags = {
      evaluation_auto_execute: true as const,
      evaluation_auto_execute_id: 'evaluation-delivery-smoke-1',
      relay_mode_coverage_smoke: {
        version: 3 as const,
        current_phase: 'plan_single_bot' as const,
        planned_modes: [] as Array<'single_bot' | 'coop_group' | 'bbs'>,
      },
    };

    render(<TaskLoopCard {...cardProps({ type: 'task_ready', task, hidden_flags })} />);

    expect(mockedSubmit).toHaveBeenCalledTimes(1);
    expect(mockedSubmit).toHaveBeenCalledWith('执行任务', {
      __taskAction: 'execute',
      __taskAutoExecute: true,
      task: expect.objectContaining({ hidden_flags }),
    });
  });

  it('forwards the hidden relay smoke flag when dispatching a confirmed task', () => {
    const hidden_flags = {
      relay_mode_coverage_smoke: {
        version: 3,
        current_phase: 'plan_single_bot',
        planned_modes: [],
      },
    };
    const task = {
      task_type: 'dynamic',
      goal: '完成存储行业尽调',
      deliverables: ['尽调报告'],
      acceptance_criteria: ['明确投资价值'],
      constraints: ['全球市场'],
      resources: [],
    };
    render(<TaskLoopCard {...cardProps({ type: 'task_ready', task, hidden_flags })} />);

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(mockedSubmit).toHaveBeenCalledWith('执行任务', {
      __taskAction: 'execute',
      task: expect.objectContaining({
        goal: '完成存储行业尽调',
        hidden_flags,
      }),
    });
  });

  it('normalizes legacy scalar list fields before dispatching execute action', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_ready',
          task: {
            task_type: 'dynamic',
            goal: '完成存储行业尽调',
            deliverables: '一份存储行业尽调报告' as unknown as string[],
            acceptance_criteria: '明确中短期投资价值' as unknown as string[],
            constraints: '至少30%判断来自最近三个月信息' as unknown as string[],
            resources: '',
          },
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(mockedSubmit).toHaveBeenCalledWith(
      '执行任务',
      expect.objectContaining({
        __taskAction: 'execute',
        task: expect.objectContaining({
          goal: '完成存储行业尽调',
          deliverables: ['一份存储行业尽调报告'],
          acceptance_criteria: ['明确中短期投资价值'],
          constraints: ['至少30%判断来自最近三个月信息'],
          resources: [],
        }),
      }),
    );
    const extra = mockedSubmit.mock.calls[0]?.[1] as { task?: { deliverables?: string[] } } | undefined;
    // Keeps the downstream useTaskExecuteFromCard path safe even when a legacy card emits scalar strings.
    expect(extra?.task?.deliverables?.join('；')).toBe('一份存储行业尽调报告');
  });

  it('dispatches ordinary card actions without exposing internal card identifiers', () => {
    render(
      <TaskLoopCard
        {...cardProps({
          type: 'task_multi_select',
          prompt: '请选择一个任务',
          tasks: [{ index: 1, summary: '任务一' }],
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /任务一/ }));
    expect(mockedSubmit).toHaveBeenCalledWith('我选择任务 1：任务一');
  });
});
