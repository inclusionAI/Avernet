/** @jest-environment jsdom */
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// TaskPanelAdapter 是引擎 PanelContentProps ↔ TaskPanel 业务 props 的适配胶水:
// mock 掉 TaskPanel 本体,验证 params 透传 / apiBaseUrl 归一化 / 缺 taskId 降级文案 / onOpenSubTask 交互上报。
jest.mock('@/assets/TaskPanel/TaskPanel', () => ({ TaskPanel: jest.fn(() => null) }));

import { TaskPanel } from '@/assets/TaskPanel/TaskPanel';
import { TaskPanelAdapter } from '@/assets/TaskPanel/TaskPanelAdapter';
import { render, screen } from '@testing-library/react';

const mockedTaskPanel = TaskPanel as unknown as jest.Mock;

// PanelContentProps 的其余字段(payload/onAction/tab 等)不被适配器消费,造一个最小合法壳。
function adapterProps(params: Record<string, unknown>, overrides: Record<string, unknown> = {}) {
  return {
    params,
    payload: params,
    onAction: jest.fn(),
    onInteraction: jest.fn(),
    ...overrides,
  } as Parameters<typeof TaskPanelAdapter>[0];
}

/** 构造 <TaskPanelAdapter {...props} /> 元素(壳 props 已 cast 成适配器入参类型)。 */
function renderAdapter(props: ReturnType<typeof adapterProps>) {
  return render(<TaskPanelAdapter {...(props as Parameters<typeof TaskPanelAdapter>[0])} />);
}

describe('TaskPanelAdapter', () => {
  beforeEach(() => {
    mockedTaskPanel.mockClear();
  });

  it('缺 taskId 参数时渲染降级文案(根节点 data-task-panel-root)并透传样式', () => {
    renderAdapter(adapterProps({ apiBaseUrl: 'https://internal.example.com' }, { className: 'panel-empty', style: { padding: 40 } }));

    const root = screen.getByText(/任务副屏缺少 taskId 参数/);
    expect(root.getAttribute('data-task-panel-root')).toBe('true');
    expect(root.className).toBe('panel-empty');
    // 空态样式:基线 padding 24 与宿主 style 合并
    expect(root.style.padding).toBe('40px');
    // 缺 taskId 时不应触碰 TaskPanel 本体
    expect(mockedTaskPanel).not.toHaveBeenCalled();
  });

  it('params 各字段透传给 TaskPanel,apiBaseUrl 绝对地址归一化为相对', () => {
    const taskInfoFallback = { ownerBotName: '演示Bot' };
    const switcher = <div>switcher</div>;
    const nav = { push: jest.fn() };
    renderAdapter(
      adapterProps(
        {
          apiBaseUrl: 'https://internal.example.com', // 绝对回调地址 → '' (走同源代理)
          bcsBaseUrl: '/__test_bcs__', // 本地测试代理 → ''
          taskApiBase: '/openapi/v1/collaboration/tasks',
          userId: 'user-1',
          taskId: 'task-9',
          onTogglePanelName: '面板',
          taskInfoFallback,
        },
        { className: 'panel-root', style: { height: 320 }, switcher, nav },
      ),
    );

    expect(mockedTaskPanel).toHaveBeenCalledTimes(1);
    const props = mockedTaskPanel.mock.calls[0][0];
    expect(props.apiBaseUrl).toBe('');
    expect(props.bcsBaseUrl).toBe('');
    expect(props.taskApiBase).toBe('/openapi/v1/collaboration/tasks');
    expect(props.userId).toBe('user-1');
    expect(props.taskId).toBe('task-9');
    expect(props.taskInfoFallback).toBe(taskInfoFallback);
    expect(props.className).toBe('panel-root');
    expect(props.style).toEqual({ height: 320 });
    expect(props.nav).toBe(nav);
    expect(props.switcher).toBe(switcher);
  });

  it('apiBaseUrl 缺省/本地测试代理/相对路径 三类归一化分支', () => {
    renderAdapter(adapterProps({ taskId: 'task-1' })); // apiBaseUrl/bcsBaseUrl 缺省 → ''
    expect(mockedTaskPanel).toHaveBeenCalledTimes(1);
    let props = mockedTaskPanel.mock.calls[0][0];
    expect(props.apiBaseUrl).toBe('');
    expect(props.bcsBaseUrl).toBe('');

    renderAdapter(
      adapterProps({ apiBaseUrl: '/__test_local__', bcsBaseUrl: '/teamclaw-bcs', taskId: 'task-2' }),
    );
    props = mockedTaskPanel.mock.calls[1][0];
    // /__test_local__ → '' (相对),而自有相对前缀 /teamclaw-bcs 原样保留
    expect(props.apiBaseUrl).toBe('');
    expect(props.bcsBaseUrl).toBe('/teamclaw-bcs');
  });

  it('params 缺省对象整体为空 → taskId 缺失降级', () => {
    renderAdapter(adapterProps(undefined as unknown as Record<string, unknown>));
    expect(screen.getByText(/任务副屏缺少 taskId 参数/)).toBeTruthy();
    expect(mockedTaskPanel).not.toHaveBeenCalled();
  });

  it('onOpenSubTask 转译为副屏 InteractionRecord 上报', () => {
    const onInteraction = jest.fn();
    renderAdapter(adapterProps({ taskId: 'task-9' }, { onInteraction }));

    expect(mockedTaskPanel).toHaveBeenCalledTimes(1);
    const props = mockedTaskPanel.mock.calls[0][0];
    props.onOpenSubTask('sub-7');

    expect(onInteraction).toHaveBeenCalledWith({
      source: { type: 'panel', target: 'taskPanel.TaskLoopView' },
      description: '打开子任务 sub-7',
      action: { verb: 'open', subject: 'subtask', params: { subTaskId: 'sub-7' } },
      snapshot: { parentTaskId: 'task-9' },
    });
  });
});
