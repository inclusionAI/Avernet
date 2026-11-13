/** @jest-environment jsdom */
import React from 'react';
import { backendRequest } from '@/services/backendApi/httpClient';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { TaskPanelAssetBoundary } from '@/components/Workspace/TaskPanelAssetBoundary';
import { loadTaskPanelAsset, type TaskPanelAsset } from '@/services/tasks/panel/taskPanelAssetLoader';
import { nextgenTaskPanelHost } from '@/services/tasks/panel/taskPanelRuntime';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: jest.fn() }));
jest.mock('@/services/tasks/panel/taskPanelRuntime', () => ({ nextgenTaskPanelHost: { getViewer: () => ({ userId: 'viewer' }) }, getTaskPanelRuntimeGeneration: () => 0, subscribeTaskPanelRuntime: () => () => {} }));

const loadAsset = jest.mocked(loadTaskPanelAsset);
const hostContext = React.createContext<typeof nextgenTaskPanelHost | null>(null);
const asset = { TaskPanelHostContext: hostContext } as unknown as TaskPanelAsset;
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
beforeEach(() => { loadAsset.mockReset(); loadAsset.mockResolvedValue(asset); });
afterEach(() => { jest.restoreAllMocks(); });

it('shows loading, reports a failed load and retries through the shared loader', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  const pending = deferred<TaskPanelAsset>();
  loadAsset.mockReturnValueOnce(pending.promise);
  render(<TaskPanelAssetBoundary>{() => <div>asset ready</div>}</TaskPanelAssetBoundary>);
  expect(screen.getByRole('status')).toBeTruthy();
  await act(async () => { pending.reject(new Error('load failed')); });
  expect(screen.getByRole('alert').textContent).toContain('load failed');
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('asset ready')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(loadAsset).toHaveBeenCalledTimes(2);
});

it('catches the render-prop call itself and resets it on retry', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  let fail = true;
  render(<TaskPanelAssetBoundary>{() => {
    if (fail) throw new Error('callback failed');
    return <div>callback recovered</div>;
  }}</TaskPanelAssetBoundary>);
  expect((await screen.findByRole('alert')).textContent).toContain('callback failed');
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('callback recovered')).toBeTruthy();
});

it('catches descendant updates and remounts only the failed panel on retry', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  const unmount = jest.fn();
  let instances = 0;
  function Remote() {
    const [id] = React.useState(() => ++instances);
    const [fail, setFail] = React.useState(false);
    React.useEffect(() => () => { unmount(id); }, [id]);
    if (fail) throw new Error('descendant failed');
    return <button type="button" onClick={() => setFail(true)}>remote {id}</button>;
  }
  render(<>
    <section aria-label="first"><TaskPanelAssetBoundary>{() => <Remote />}</TaskPanelAssetBoundary></section>
    <section aria-label="second"><TaskPanelAssetBoundary>{() => <Remote />}</TaskPanelAssetBoundary></section>
  </>);
  const first = within(screen.getByRole('region', { name: 'first' }));
  const second = within(screen.getByRole('region', { name: 'second' }));
  await first.findByRole('button', { name: 'remote 1' });
  await second.findByRole('button', { name: 'remote 2' });
  fireEvent.click(first.getByRole('button'));
  expect(first.getByRole('alert').textContent).toContain('descendant failed');
  expect(unmount).toHaveBeenCalledWith(1);
  fireEvent.click(first.getByRole('button', { name: '重试' }));
  expect(await first.findByRole('button', { name: 'remote 3' })).toBeTruthy();
  expect(second.getByRole('button', { name: 'remote 2' })).toBeTruthy();
  expect(unmount).not.toHaveBeenCalledWith(2);
});

it('injects the host context for auxiliary asset descendants', async () => {
  function Consumer() {
    expect(React.useContext(hostContext)).toBe(nextgenTaskPanelHost);
    return <div>context ready</div>;
  }
  render(<TaskPanelAssetBoundary>{() => <Consumer />}</TaskPanelAssetBoundary>);
  expect(await screen.findByText('context ready')).toBeTruthy();
});

it.each(['resolve', 'reject'] as const)('ignores a late asset %s after unmount without poisoning a new mount', async outcome => {
  const pending = deferred<TaskPanelAsset>();
  loadAsset.mockReturnValueOnce(pending.promise);
  const children = jest.fn(() => <div>stale mount</div>);
  const view = render(<TaskPanelAssetBoundary>{children}</TaskPanelAssetBoundary>);
  await act(async () => {});
  view.unmount();
  await act(async () => {
    if (outcome === 'resolve') pending.resolve(asset);
    else pending.reject(new Error('late rejection'));
  });
  expect(children).not.toHaveBeenCalled();
  render(<TaskPanelAssetBoundary>{() => <div>new mount</div>}</TaskPanelAssetBoundary>);
  expect(await screen.findByText('new mount')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('also reports synchronous loader exceptions', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  loadAsset.mockImplementationOnce(() => { throw new Error('sync load failed'); });
  render(<TaskPanelAssetBoundary>{() => null}</TaskPanelAssetBoundary>);
  expect((await screen.findByRole('alert')).textContent).toContain('sync load failed');
});

it('shares one real JS/CSS load across mounts and reuses the valid asset on render retry', async () => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
  const global = window as unknown as {
    taskPanel?: TaskPanelAsset;
  };
  const previousAsset = global.taskPanel;
  const realLoader = jest.requireActual<typeof import('@/services/tasks/panel/taskPanelAssetLoader')>(
    '@/services/tasks/panel/taskPanelAssetLoader',
  );
  loadAsset.mockImplementation(realLoader.loadTaskPanelAsset);
  jest.mocked(backendRequest).mockResolvedValue({ code: 200000, data: { schema_version: 1, env: 'pre', bundles: [{ name: 'taskPanel', url: 'https://assets.example.test/task-panel/0.2.0/index.umd.js' }] } });
  delete global.taskPanel;
  let fail = true;
  const mounted = render(<>
    <TaskPanelAssetBoundary>{() => {
      if (fail) throw new Error('local render failure');
      return <div>retried real asset</div>;
    }}</TaskPanelAssetBoundary>
    <TaskPanelAssetBoundary>{() => <div>sibling real asset</div>}</TaskPanelAssetBoundary>
  </>);
  try {
    await act(async () => {});
    expect(document.querySelectorAll('script')).toHaveLength(1);
    expect(document.querySelectorAll('link[data-task-panel-source]')).toHaveLength(1);
    const script = document.querySelector('script')!;
    const style = document.querySelector('link[data-task-panel-source]')!;
    await act(async () => {
      global.taskPanel = {
        ...asset, hostApiVersion: 1, assetVersion: '0.2.0',
        TaskLoopView: () => null, createTaskPanelController: jest.fn(),
      } as unknown as TaskPanelAsset;
      script.dispatchEvent(new Event('load'));
      style.dispatchEvent(new Event('load'));
    });
    expect(await screen.findByText('sibling real asset')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('local render failure');
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('retried real asset')).toBeTruthy();
    expect(document.querySelector('script')).toBe(script);
    expect(document.querySelector('link[data-task-panel-source]')).toBe(style);
    script.remove();
    style.remove();
  } finally {
    mounted.unmount();
    global.taskPanel = previousAsset;
  }
});
