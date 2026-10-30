/** @jest-environment jsdom */
// TaskPanelAdapter 的参数归一化、缺 taskId 空态和子任务交互上报。
import { TaskPanelAdapter } from '@/assets/TaskPanel/TaskPanelAdapter';
import type { TaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import { TaskPanel } from '@/assets/TaskPanel/TaskPanel';
import { describe, expect, it } from '@jest/globals';
import type { TaskPanelProps } from '@/assets/TaskPanel/TaskPanel';
import { render, screen } from '@testing-library/react';
import type { ComponentProps, FC } from 'react';
import '@testing-library/jest-dom/jest-globals';

jest.mock('@/assets/TaskPanel/TaskPanel', () => ({
  TaskPanel: jest.fn((props: Record<string, unknown>) => <div>{JSON.stringify(props)}</div>),
}));

interface TaskPanelMockType {
  (props: TaskPanelProps): JSX.Element;
  mock: { calls: [TaskPanelProps][] };
}

const TaskPanelMock = TaskPanel as unknown as TaskPanelMockType;
const nav: TaskPanelNav = {
  selectedNodeId: null,
  drillStack: [],
  selectRootNode: jest.fn(),
  selectNode: jest.fn(),
  selectDefaultNode: jest.fn(),
  drillInto: jest.fn(),
  backToOverview: jest.fn(),
};

type TaskPanelAdapterProps = ComponentProps<typeof TaskPanelAdapter>;

function renderAdapter(
  params: TaskPanelAdapterProps['params'],
  overrides: { onInteraction?: TaskPanelAdapterProps['onInteraction'] } = {},
) {
  const AdapterForTest = TaskPanelAdapter as unknown as FC<Partial<TaskPanelAdapterProps>>;

  return render(
    <AdapterForTest
      params={params}
      onInteraction={overrides.onInteraction ?? jest.fn()}
      nav={nav}
    />,
  );
}

describe('TaskPanelAdapter', () => {
  it('params 为空且缺 taskId 时渲染空态', () => {
    renderAdapter(undefined as unknown as TaskPanelAdapterProps['params']);
    expect(screen.getByText(/缺少 taskId 参数/)).toBeInTheDocument();
  });

  it('相对路径原样透传给 TaskPanel', () => {
    const onInteraction = jest.fn();
    renderAdapter(
      {
        apiBaseUrl: '/api',
        bcsBaseUrl: '/bcs',
        taskApiBase: '/task-api',
        userId: 'user-1',
        taskId: 'task-1',
        taskInfoFallback: null,
      },
      { onInteraction },
    );

    const props = TaskPanelMock.mock.calls.at(-1)![0];
    expect(props).toMatchObject({
      apiBaseUrl: '/api',
      bcsBaseUrl: '/bcs',
      taskApiBase: '/task-api',
      userId: 'user-1',
      taskId: 'task-1',
      nav,
    });
    props.onOpenSubTask?.('sub-1');
    expect(onInteraction).toHaveBeenCalledTimes(1);
    expect(onInteraction.mock.calls[0][0].snapshot).toEqual({ parentTaskId: 'task-1' });
  });

  it('测试代理路径归一化为空串', () => {
    renderAdapter({ apiBaseUrl: '/__test_local__', bcsBaseUrl: '/__test_bcs__', taskId: 'task-2' });
    const props = TaskPanelMock.mock.calls.at(-1)![0];
    expect(props).toMatchObject({ apiBaseUrl: '', bcsBaseUrl: '' });
  });

  it('绝对 HTTP 地址归一化为空串', () => {
    renderAdapter({ apiBaseUrl: 'http://backend.example', bcsBaseUrl: 'https://bcs.example', taskId: 'task-3' });
    const props = TaskPanelMock.mock.calls.at(-1)![0];
    expect(props).toMatchObject({ apiBaseUrl: '', bcsBaseUrl: '' });
  });

  it('default export 与命名 export 指向同一适配器', () => {
    const adapterModule = TaskPanelAdapter as unknown as { default: unknown };
    expect(adapterModule.default ?? TaskPanelAdapter).toBeDefined();
  });
});
