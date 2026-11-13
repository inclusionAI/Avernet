/** @jest-environment jsdom */
import { beforeEach, afterEach, describe, expect, it, jest } from '@jest/globals';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { TaskPanelDeclaration } from '@avernet-assets/task-panel';

let mockViewer: string | null = 'viewer';
let mockOrigin: { sessionId: string; kind: 'bot' } | null = { sessionId: 's1', kind: 'bot' };
const mockLoad = jest.fn<() => Promise<any>>();
jest.mock('@/capabilities', () => ({ getCapabilities: () => ({ getHumanIdentity: () => ({ status: 'available', value: mockViewer ? { userId: mockViewer } : null }) }) }));
jest.mock('@/services/tasks/panel/taskPanelAssetLoader', () => ({ loadTaskPanelAsset: () => mockLoad() }));
jest.mock('@/services/tasks/panel/taskPanelArtifactAdapter', () => ({ taskPanelArtifacts: {} }));
jest.mock('@/services/workspace/chatBridge', () => ({ chatBridge: { openPanelTab: jest.fn() } }));
jest.mock('@/services/tasks/panel/taskPanelConversationAdapter', () => ({ captureTaskPanelOrigin: () => mockOrigin, restoreTaskPanelOrigin: async () => ({ status: 'ready' }), navigateTaskPanelConversation: async () => ({ status: 'ready' }) }));
import * as runtime from '@/services/tasks/panel/taskPanelRuntime';
import { useSharedTaskPanelRecovery } from '@/hooks/useSharedTaskPanelRecovery';
import React from 'react';
import { TaskPanelHost } from '@/components/Workspace/TaskPanelHost';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';

const asset = require('../../../../assets/task-panel/src/controller/createTaskPanelController');
const tab = (id: string): TaskPanelDeclaration => ({ id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: id, closable: true, params: { taskId: id } });
const history = { readPage: async () => ({ messages: [], nextCursor: null }) };
let originalFetch: typeof fetch;
beforeEach(() => {
  mockViewer = 'viewer'; mockOrigin = { sessionId: 's1', kind: 'bot' };
  mockLoad.mockReset().mockResolvedValue(asset);
  runtime.resetTaskPanelRuntime();
  useTaskSidePanelStore.getState().resetAll();
  originalFetch = global.fetch;
  global.fetch = jest.fn(async (path: any) => ({ ok: true, json: async () => ({ data: { items: String(path).includes('by_session') ? [{ task_id: 'index-latest' }] : [] } }) })) as unknown as typeof fetch;
});
afterEach(() => { runtime.resetTaskPanelRuntime(); global.fetch = originalFetch; });

describe('one controller owns presented-card and recovery focus', () => {
  it('keeps a mounted card when history-ready enables recovery later', async () => {
    const c = await runtime.getTaskPanelController();
    const unregister = runtime.registerTaskPanelPresentation(tab('shown'));
    c.adoptPresentedTask(tab('shown'));
    const openTab = jest.fn();
    const panelRef = { current: { openTab } as any };
    const { rerender } = renderHook(({ ready }) => useSharedTaskPanelRecovery({
      enabled: ready, conversation: { sessionId: 's1', kind: 'bot' }, userId: 'viewer', history,
      panelRef,
    }), { initialProps: { ready: false } });
    await act(async () => { rerender({ ready: true }); });
    await waitFor(() => expect(c.getSnapshot().conversation?.sessionId).toBe('s1'));
    await act(async () => Promise.resolve());
    expect(c.getSnapshot().activeTaskId).toBe('shown');
    expect(openTab).not.toHaveBeenCalled();
    expect(useTaskSidePanelStore.getState().activeTaskId).toBe('shown');
    unregister();
  });

  it('does not carry a stale mounted-card hint into a different conversation or viewer', async () => {
    const unregister = runtime.registerTaskPanelPresentation(tab('old'));
    expect(runtime.getPresentedTaskForConversation({ sessionId: 's2', kind: 'bot' })).toBeNull();
    mockViewer = 'another';
    expect(runtime.getPresentedTaskForConversation({ sessionId: 's1', kind: 'bot' })).toBeNull();
    unregister();
  });

  it('unmount removes only its own presentation, not the newer one', () => {
    const old = runtime.registerTaskPanelPresentation(tab('old'));
    const next = runtime.registerTaskPanelPresentation(tab('next'));
    old();
    expect(runtime.getPresentedTaskForConversation({ sessionId: 's1', kind: 'bot' })?.params.taskId).toBe('next');
    next();
    expect(runtime.getPresentedTaskForConversation({ sessionId: 's1', kind: 'bot' })).toBeNull();
  });
});

describe('viewer-owned runtime lifecycle', () => {
  it('disposes even a preserved controller when the viewer logs out', async () => {
    const c = await runtime.getTaskPanelController();
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    c.adoptPresentedTask(tab('t1'));
    runtime.nextgenTaskPanelHost.preservePanel(true);
    mockViewer = null;
    act(() => { useWorkspaceStore.setState({ identities: [] }); });
    expect(c.getSnapshot().conversation).toBeNull();
    expect(c.getSnapshot().activeTaskId).toBeNull();
    expect(runtime.peekTaskPanelController()).toBeNull();
    await expect(runtime.getTaskPanelController()).rejects.toThrow('登录');
  });

  it('rejects a late asset load after logout and creates a fresh controller on next login', async () => {
    let resolve!: (value: any) => void;
    mockLoad.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const pending = runtime.getTaskPanelController();
    const failed = pending.catch(error => error);
    mockViewer = null;
    act(() => { useWorkspaceStore.setState({ identities: [] }); });
    resolve(asset);
    expect((await failed).name).toBe('AbortError');
    expect(runtime.peekTaskPanelController()).toBeNull();
    mockViewer = 'next-viewer';
    const fresh = await runtime.getTaskPanelController();
    expect(fresh.host.getViewer()?.userId).toBe('next-viewer');
    expect(fresh.getSnapshot().activeTaskId).toBeNull();
  });
});


describe('same-viewer relogin before React effects flush', () => {
  it('remounts a host using the lifecycle generation, even when viewerId ends unchanged', async () => {
    const view = jest.fn(({ taskId }: any) => <div>ready {taskId}</div>);
    mockLoad.mockResolvedValue({ ...asset, TaskPanelHostContext: React.createContext(null), TaskLoopView: view });
    render(<TaskPanelHost taskId="same-user" apiBaseUrl="" />);
    await screen.findByText('ready same-user');
    const previous = runtime.peekTaskPanelController();
    act(() => {
      mockViewer = null; useWorkspaceStore.setState({ identities: [] });
      mockViewer = 'viewer'; useWorkspaceStore.setState({ identities: [] });
    });
    await waitFor(() => {
      expect(runtime.peekTaskPanelController()).not.toBeNull();
      expect(runtime.peekTaskPanelController()).not.toBe(previous);
      expect(view.mock.calls.at(-1)?.[0].controller).toBe(runtime.peekTaskPanelController());
    });
  });

  it('rebinds recovery to a fresh controller without a userId prop change', async () => {
    const panelRef = { current: { openTab: jest.fn() } as any };
    renderHook(() => useSharedTaskPanelRecovery({ enabled: true, conversation: { sessionId: 's1', kind: 'bot' }, userId: 'viewer', history, panelRef }));
    await waitFor(() => expect(runtime.peekTaskPanelController()?.getSnapshot().activeTaskId).toBe('index-latest'));
    const previous = runtime.peekTaskPanelController();
    act(() => {
      mockViewer = null; useWorkspaceStore.setState({ identities: [] });
      mockViewer = 'viewer'; useWorkspaceStore.setState({ identities: [] });
    });
    await waitFor(() => {
      expect(runtime.peekTaskPanelController()).not.toBe(previous);
      expect(runtime.peekTaskPanelController()?.getSnapshot().activeTaskId).toBe('index-latest');
    });
  });
});

it('retries session initialization when the first asset/controller load failed', async () => {
  mockLoad.mockRejectedValueOnce(new Error('offline asset'));
  const panelRef = { current: { openTab: jest.fn() } as any };
  const { result } = renderHook(() => useSharedTaskPanelRecovery({ enabled: true, conversation: { sessionId: 's1', kind: 'bot' }, userId: 'viewer', history, panelRef }));
  await waitFor(() => expect(result.current.status).toBe('error'));
  await act(async () => { await result.current.continueRecovery(); });
  await waitFor(() => expect(runtime.peekTaskPanelController()?.getSnapshot().activeTaskId).toBe('index-latest'));
  expect(panelRef.current.openTab).toHaveBeenCalledTimes(1);
  expect(result.current.status).toBe('opened');
});

it('historyReady false-to-true does not release a target handoff already accepted by the controller', async () => {
  const c = await runtime.getTaskPanelController();
  await c.enterConversation({ sessionId: 's1', kind: 'bot' });
  c.adoptPresentedTask(tab('root'));
  let finish!: (value: { status: 'ready' }) => void;
  const navigation = jest.spyOn(runtime.nextgenTaskPanelHost, 'navigate').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  c.nav('root').selectNode('node', { sessionId: 'child', kind: 'bot' });
  const panelRef = { current: { openTab: jest.fn() } as any };
  const { rerender } = renderHook(({ ready }) => useSharedTaskPanelRecovery({ enabled: ready, conversation: { sessionId: 'child', kind: 'bot' }, userId: 'viewer', history, panelRef }), { initialProps: { ready: false } });
  try {
    await waitFor(() => expect(c.getSnapshot().conversation?.sessionId).toBe('child'));
    expect(c.getSnapshot().activeTaskId).toBe('root');
    await act(async () => { finish({ status: 'ready' }); });
    await act(async () => { rerender({ ready: true }); });
    await waitFor(() => expect(c.getSnapshot().activeTaskId).toBe('root'));
    expect(c.getSnapshot().conversation?.sessionId).toBe('child');
    expect(panelRef.current.openTab).not.toHaveBeenCalled();
  } finally { navigation.mockRestore(); }
});

it('a cold SDK card binds its authenticated route without waiting for visible history or fetching recovery', async () => {
  const unregister = runtime.registerTaskPanelPresentation(tab('cold-shown'));
  await waitFor(() => expect(runtime.peekTaskPanelController()?.getSnapshot().conversation?.sessionId).toBe('s1'));
  expect(runtime.peekTaskPanelController()?.getSnapshot().activeTaskId).toBe('cold-shown');
  expect(global.fetch).not.toHaveBeenCalled();
  unregister();
});
