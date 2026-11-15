/** @jest-environment jsdom */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { TaskPanelHost } from '@/components/Workspace/TaskPanelHost';
import { TaskPanelAssetBoundary } from '@/components/Workspace/TaskPanelAssetBoundary';
import { loadTaskPanelAsset, type TaskPanelAsset } from '@/services/tasks/panel/taskPanelAssetLoader';
import { getTaskPanelController, nextgenTaskPanelHost } from '@/services/tasks/panel/taskPanelRuntime';
import type { TaskPanelController } from '@avernet-assets/task-panel';
import { useWorkspaceStore } from '@/stores/workspaceStore';

let mockViewer: string | null = 'viewer';

jest.mock('@/components/Workspace/TaskPanelAssetBoundary', () => {
  const actual = jest.requireActual('@/components/Workspace/TaskPanelAssetBoundary');
  return { ...actual, TaskPanelAssetBoundary: jest.fn(actual.TaskPanelAssetBoundary) };
});
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: jest.fn() }));
jest.mock('@/services/tasks/panel/taskPanelRuntime', () => ({
  nextgenTaskPanelHost: { getViewer: () => mockViewer ? { userId: mockViewer } : null }, getTaskPanelController: jest.fn(),
  getTaskPanelRuntimeGeneration: () => 0, subscribeTaskPanelRuntime: () => () => {},
}));
const loadAsset = jest.mocked(loadTaskPanelAsset);
const getController = jest.mocked(getTaskPanelController);
const controller = { dispose: jest.fn(), leaveConversation: jest.fn() } as unknown as TaskPanelController;
const createController = jest.fn();
const view = jest.fn(({ taskId }: React.ComponentProps<TaskPanelAsset['TaskLoopView']>) => <div>task {taskId}</div>);
const asset = {
  TaskPanelHostContext: React.createContext(nextgenTaskPanelHost),
  TaskLoopView: view, createTaskPanelController: createController,
} as unknown as TaskPanelAsset;
beforeEach(() => {
  mockViewer = 'viewer';
  jest.clearAllMocks();
  loadAsset.mockReset().mockResolvedValue(asset);
  getController.mockReset().mockResolvedValue(controller);
  view.mockReset().mockImplementation(({ taskId }) => <div>task {taskId}</div>);
});
afterEach(() => { jest.restoreAllMocks(); });

it('uses the common boundary and forwards props, host and the same runtime controller to both panels', async () => {
  const onSelectTask = jest.fn();
  const mounted = render(<><TaskPanelHost apiBaseUrl="" taskId="one" onSelectTask={onSelectTask} /><TaskPanelHost apiBaseUrl="" taskId="two" /></>);
  await screen.findByText('task one');
  await screen.findByText('task two');
  expect(TaskPanelAssetBoundary).toHaveBeenCalled();
  expect(view.mock.calls.map(([props]) => props)).toEqual(expect.arrayContaining([
    expect.objectContaining({ taskId: 'one', host: nextgenTaskPanelHost, controller, onSelectTask }),
    expect.objectContaining({ taskId: 'two', host: nextgenTaskPanelHost, controller }),
  ]));
  mounted.unmount();
  expect(createController).not.toHaveBeenCalled();
  expect(controller.dispose).not.toHaveBeenCalled();
  expect(controller.leaveConversation).not.toHaveBeenCalled();
});

it('uses common load error/retry without acquiring a controller before the asset is ready', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  loadAsset.mockRejectedValueOnce(new Error('asset unavailable'));
  render(<TaskPanelHost apiBaseUrl="" taskId="one" />);
  expect((await screen.findByRole('alert')).textContent).toContain('asset unavailable');
  expect(getController).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('task one')).toBeTruthy();
  expect(getController).toHaveBeenCalledTimes(1);
});

it('reports controller acquisition errors through the common boundary and retries', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  getController.mockRejectedValueOnce(new Error('controller unavailable'));
  render(<TaskPanelHost apiBaseUrl="" taskId="one" />);
  expect((await screen.findByRole('alert')).textContent).toContain('controller unavailable');
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('task one')).toBeTruthy();
  expect(createController).not.toHaveBeenCalled();
});

it('recovers a remote mount failure locally without replacing the shared controller or remounting a sibling', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  let fail = true;
  const siblingUnmount = jest.fn();
  function Descendant({ taskId }: { taskId: string }) {
    React.useEffect(() => taskId === 'two' ? siblingUnmount : undefined, [taskId]);
    if (taskId === 'one' && fail) throw new Error('remote crashed');
    return <div>recovered {taskId}</div>;
  }
  view.mockImplementation(props => <Descendant {...props} />);
  render(<>
    <section aria-label="one"><TaskPanelHost apiBaseUrl="" taskId="one" /></section>
    <TaskPanelHost apiBaseUrl="" taskId="two" />
  </>);
  const first = within(screen.getByRole('region', { name: 'one' }));
  await first.findByRole('alert');
  await screen.findByText('recovered two');
  fail = false;
  fireEvent.click(first.getByRole('button', { name: '重试' }));
  expect(await first.findByText('recovered one')).toBeTruthy();
  expect(siblingUnmount).not.toHaveBeenCalled();
  // React may probe a failed component with no props while building its error stack.
  const renderedProps = view.mock.calls.map(([props]) => props).filter(Boolean);
  expect(renderedProps.length).toBeGreaterThan(0);
  expect(renderedProps.every(props => props.controller === controller)).toBe(true);
  expect(createController).not.toHaveBeenCalled();
  expect(controller.dispose).not.toHaveBeenCalled();
});

it.each(['resolve', 'reject'] as const)('ignores late controller %s after unmount', async outcome => {
  let resolve!: (value: TaskPanelController) => void;
  let reject!: (reason: unknown) => void;
  getController.mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no; }));
  const mounted = render(<TaskPanelHost apiBaseUrl="" taskId="one" />);
  await act(async () => {});
  expect(getController).toHaveBeenCalledTimes(1);
  mounted.unmount();
  await act(async () => {
    if (outcome === 'resolve') resolve(controller);
    else reject(new Error('late controller error'));
  });
  expect(view).not.toHaveBeenCalled();
  expect(controller.dispose).not.toHaveBeenCalled();
});


it('drops the mounted controller when the viewer changes and acquires the new runtime controller', async () => {
  const fresh = { dispose: jest.fn() } as unknown as TaskPanelController;
  render(<TaskPanelHost apiBaseUrl="" taskId="one" />);
  await screen.findByText('task one');
  const previousCalls = view.mock.calls.length;
  getController.mockResolvedValue(fresh);
  act(() => {
    mockViewer = 'next-viewer';
    useWorkspaceStore.setState({});
  });
  expect(screen.queryByText('task one')).toBeNull();
  await screen.findByText('task one');
  expect(getController).toHaveBeenCalledTimes(2);
  expect(view.mock.calls.slice(previousCalls).every(([props]) =>
    props.controller === fresh,
  )).toBe(true);
  // Only runtime disposes controllers; the mount must not dispose either one.
  expect(controller.dispose).not.toHaveBeenCalled();
  expect(fresh.dispose).not.toHaveBeenCalled();
});

it('ignores an old viewer AbortError after switching to a new viewer', async () => {
  let reject!: (reason: unknown) => void;
  getController.mockReturnValueOnce(new Promise((_, no) => { reject = no; }));
  render(<TaskPanelHost apiBaseUrl="" taskId="one" />);
  await act(async () => {});
  expect(getController).toHaveBeenCalledTimes(1);
  act(() => {
    mockViewer = 'next-viewer';
    useWorkspaceStore.setState({});
  });
  await screen.findByText('task one');
  await act(async () => {
    reject(Object.assign(new Error('identity invalidated'), { name: 'AbortError' }));
  });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(getController).toHaveBeenCalledTimes(2);
});
