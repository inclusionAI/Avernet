/** @jest-environment jsdom */
import { TaskLoopCard } from '@/components/TaskCards/TaskLoopCard';
import { submitTaskCardAction } from '@/services/tasks/taskCardBridge';
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
  beforeEach(() => mockedSubmit.mockClear());

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

    fireEvent.click(screen.getByRole('button', { name: '执行' }));

    expect(mockedSubmit).toHaveBeenCalledWith('执行任务', {
      __taskAction: 'execute',
      task,
    });
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
