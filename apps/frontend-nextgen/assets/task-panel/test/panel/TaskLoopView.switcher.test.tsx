import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TaskLoopView } from '../../src/panel/TaskLoopView';
import { createTaskPanelController, type TaskPanelController } from '../../src/controller/createTaskPanelController';
import { dashboard, makeHost, row, tab } from './taskSwitcherFixtures';

const controllers: TaskPanelController[] = [];
afterEach(() => { act(() => { controllers.forEach(c => c.dispose()); }); controllers.length = 0; vi.restoreAllMocks(); });
async function mount(rows = [row('t1'), row('t2')], sessionId = 'shared') {
  const host = makeHost(rows);
  const controller = createTaskPanelController(host); controllers.push(controller);
  await controller.enterConversation({ sessionId, kind: 'bot' }, tab('t1'));
  vi.mocked(host.present).mockClear(); // Assertions below observe browsing, not initial sibling-tab recovery.
  const view = render(<TaskLoopView host={host} controller={controller} taskId="t1" apiBaseUrl="" />);
  await act(async () => {});
  return { ...view, host, controller };
}
function expand() {
  fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));
  fireEvent.click(screen.getByTitle('展开任务切换'));
}
function select(id = 't2') {
  expand(); fireEvent.click(within(screen.getByTestId('task-switcher-dropdown')).getByText(`Title ${id}`));
}
const listCalls = (host: ReturnType<typeof makeHost>) => vi.mocked(host.transport.requestJson).mock.calls.filter(([path]) => path.includes('/list?') && !path.includes('&page='));

describe('TaskLoopView owns switcher UI, data and explicit selection', () => {
  it('queries the conversation owner and includes by_session relay associations, not unrelated tasks', async () => {
    const { host } = await mount([row('t1'), row('t2', 'relay'), row('foreign', 'elsewhere')], 'agent:main:session:sess:user:440718');
    expect(listCalls(host).every(([path]) => path.endsWith('user_id=440718'))).toBe(true);
    expect(listCalls(host).length).toBeGreaterThan(0);
    expand(); const dropdown = screen.getByTestId('task-switcher-dropdown');
    expect(within(dropdown).getByText('Title t2')).toBeTruthy();
    expect(within(dropdown).queryByText('Title foreign')).toBeNull();
  });
  it('starts collapsed, toggles the bar, uses this panel task rather than global active focus', async () => {
    const { controller } = await mount();
    act(() => controller.adoptPresentedTask(tab('t2')));
    expect(screen.queryByTitle('展开任务切换')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));
    expect(screen.getByTitle('展开任务切换').textContent).toContain('Title t1');
    expect(screen.queryByTestId('task-switcher-dropdown')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '隐藏任务切换' }));
    expect(screen.queryByTitle('展开任务切换')).toBeNull();
  });
  it('retains the single-task switcher (not a silent removal of the old user behavior)', async () => {
    await mount([row('t1')]);
    fireEvent.click(screen.getByRole('button', { name: '显示任务切换' }));
    expect(screen.getByTitle('展开任务切换').textContent).toContain('Title t1');
  });
  it('selects explicitly, opens the target title/id, and locates its first step with time bounds', async () => {
    const { host, controller } = await mount();
    select(); await act(async () => {});
    expect(controller.getSnapshot().activeTaskId).toBe('t2');
    expect(host.present).toHaveBeenCalledWith(expect.objectContaining({ id: 'task-t2', title: 'Title t2',
      type: 'taskPanel.TaskLoopView', closable: true, params: expect.objectContaining({ taskId: 't2' }) }), 'explicit');
    expect(host.navigate).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'session-first-step', kind: 'bot',
      botId: 'bot-first', startTime: 1760000000000, endTime: 1760000060000 }), expect.any(AbortSignal));
    expect(controller.nav('t2').selectedNodeId).toBe('n-first');
    expect(host.recordInteraction).not.toHaveBeenCalled();
  });
  it('resets the control on task params changes and on panel remount', async () => {
    const { host, controller, rerender, unmount } = await mount();
    select(); await act(async () => {});
    rerender(<TaskLoopView host={host} controller={controller} taskId="t2" apiBaseUrl="" />);
    await act(async () => {});
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
    expect(screen.queryByTestId('task-switcher-dropdown')).toBeNull();
    expand(); unmount();
    render(<TaskLoopView host={host} controller={controller} taskId="t2" apiBaseUrl="" />);
    await act(async () => {});
    expect(screen.queryByTestId('task-switcher-dropdown')).toBeNull();
    expect(screen.getByRole('button', { name: '显示任务切换' })).toBeTruthy();
  });
  it('drawer selection adopts without opening a second host tab', async () => {
    const { host, controller, rerender } = await mount(); const onSelectTask = vi.fn();
    rerender(<TaskLoopView host={host} controller={controller} taskId="t1" apiBaseUrl="" onSelectTask={onSelectTask} />);
    select(); await act(async () => {});
    expect(onSelectTask).toHaveBeenCalledWith('t2'); expect(host.present).not.toHaveBeenCalled();
    expect(controller.getSnapshot().activeTaskId).toBe('t2');
  });
  it('dashboard failure never falls back to whole-task blind navigation', async () => {
    const { host, controller } = await mount();
    const request = vi.mocked(host.transport.requestJson).getMockImplementation()!;
    vi.mocked(host.transport.requestJson).mockImplementation(async (path, signal) => {
      if (path.includes('task_id=t2')) throw new Error('dashboard offline');
      return request(path, signal);
    });
    select(); await act(async () => {});
    expect(controller.getSnapshot().activeTaskId).toBe('t2'); expect(host.navigate).not.toHaveBeenCalled();
  });
  it('ignores a late first-node response after focus changes', async () => {
    const { host, controller } = await mount(); let finish!: (value: unknown) => void;
    vi.mocked(host.transport.requestJson).mockImplementation(async path => path.includes('/dashboard?')
      ? new Promise(resolve => { finish = resolve; }) : { data: [] });
    select(); act(() => controller.adoptPresentedTask(tab('t3')));
    await act(async () => { finish({ data: dashboard('t2') }); });
    expect(host.navigate).not.toHaveBeenCalled(); expect(controller.getSnapshot().activeTaskId).toBe('t3');
  });
});

describe('shared switcher refresh and attention', () => {
  it('pauses while hidden, resumes immediately on visibility, and removes timers/listener on unmount', async () => {
    vi.useFakeTimers(); const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const { host, unmount } = await mount(); const baseline = listCalls(host).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(listCalls(host)).toHaveLength(baseline);
    visibility.mockReturnValue('visible');
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(listCalls(host)).toHaveLength(baseline + 1);
    unmount();
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(60_000); });
    expect(listCalls(host)).toHaveLength(baseline + 1);
  });
  it('first load sets a baseline; background updates mark attention without stealing focus; selection clears it', async () => {
    vi.useFakeTimers(); const { host, controller } = await mount();
    expect(screen.queryByTestId('task-switcher-attention-badge')).toBeNull();
    vi.mocked(host.transport.requestJson).mockImplementation(async path => path.includes('/dashboard?') ? { data: dashboard('t1') }
      : { data: [row('t1', 'shared', 'DONE'), { ...row('t2', 'shared', 'DONE'), gmt_modified: '2026-10-20 10:01:00' }] });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByTestId('task-switcher-attention-badge')).toBeTruthy();
    expect(controller.getSnapshot().activeTaskId).toBe('t1'); expect(host.present).not.toHaveBeenCalled();
    const beforeSelect = listCalls(host).length;
    select(); await act(async () => {});
    expect(screen.queryAllByTestId('task-switcher-attention-badge')).toHaveLength(0);
    expect(listCalls(host)).toHaveLength(beforeSelect);
  });
  it('list failure hides supplemental switcher without breaking the displayed task', async () => {
    vi.useFakeTimers(); const { host, controller } = await mount();
    vi.mocked(host.transport.requestJson).mockImplementation(async path => {
      if (path.includes('/list?') && !path.includes('&page=')) throw new Error('offline'); return { data: dashboard('t1') };
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.queryByRole('button', { name: '显示任务切换' })).toBeNull();
    expect(controller.getSnapshot().activeTaskId).toBe('t1'); expect(screen.queryByRole('alert')).toBeNull();
  });
  it('does not query supplemental lists without viewer/task/controller or with a custom switcher', async () => {
    const host = makeHost(); host.getViewer = () => null;
    const c = createTaskPanelController(host); controllers.push(c);
    const { rerender } = render(<TaskLoopView host={host} controller={c} taskId="t1" apiBaseUrl="" />);
    await act(async () => {}); expect(listCalls(host)).toHaveLength(0);
    host.getViewer = () => ({ userId: 'viewer' });
    rerender(<TaskLoopView host={host} controller={c} taskId="" apiBaseUrl="" />);
    await act(async () => {}); expect(listCalls(host)).toHaveLength(0);
    rerender(<TaskLoopView host={host} controller={c} taskId="t1" apiBaseUrl="" switcher={<div>custom</div>} />);
    await act(async () => {}); expect(listCalls(host)).toHaveLength(0);
    rerender(<TaskLoopView host={host} taskId="t1" apiBaseUrl="" />);
    await act(async () => {}); expect(listCalls(host)).toHaveLength(0);
  });
});


it('clicking a displayed task node selects it without emitting a Bot interaction', async () => {
  const { host, controller } = await mount();
  fireEvent.click(await screen.findByRole('button', { name: /^选中节点 / }));
  await act(async () => {});
  expect(controller.nav('t1').selectedNodeId).toBe('n-first');
  expect(host.recordInteraction).not.toHaveBeenCalled();
});
