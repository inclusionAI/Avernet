import { describe, it, expect, vi } from 'vitest';
import { createTaskPanelController } from '../src/controller/createTaskPanelController';
import type { TaskPanelHost, HistoryPage, NavigationOutcome } from '../src/contracts';
const declaration = (id: string) => `<AixUI type="panel" component="taskPanel.TaskLoopView" params='{"taskId":"${id}"}'/>`;
function host(overrides: Partial<TaskPanelHost> = {}): TaskPanelHost {
  return {
    getViewer: () => ({ userId: 'viewer' }), getConversation: () => ({ sessionId: 's1', kind: 'bot' }),
    transport: { requestJson: vi.fn(async () => ({ data: { items: [] } })) },
    history: { readPage: vi.fn(async () => ({ messages: [], nextCursor: null })) },
    artifacts: { readContent: vi.fn(), download: vi.fn() },
    navigate: vi.fn(async () => ({ status: 'ready' as const })), captureOrigin: () => 'origin',
    restoreOrigin: vi.fn(async () => ({ status: 'ready' as const })),
    present: vi.fn(), preservePanel: vi.fn(), recordInteraction: vi.fn(), ...overrides,
  };
}
describe('one controller for declarations and recovery', () => {
  it('finds a declaration on the second history page, without replay', async () => {
    const h = host({ history: { readPage: vi.fn(async (_c, cursor) => cursor === null
      ? { messages: [{ id: 'new', timestamp: 2, content: 'normal' }], nextCursor: 'older' }
      : { messages: [{ id: 'old', timestamp: 1, content: declaration('t1') }], nextCursor: null }) } });
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(h.history.readPage).toHaveBeenCalledTimes(2);
    expect(h.present).toHaveBeenCalledTimes(1);
    expect(c.getSnapshot().activeTaskId).toBe('t1'); c.dispose();
  });
  it('does not let delayed recovery replace an explicit task', async () => {
    let finish!: (value: { messages: []; nextCursor: null }) => void;
    const h = host({ history: { readPage: vi.fn(() => new Promise<HistoryPage>(resolve => { finish = resolve; })) } });
    const c = createTaskPanelController(h);
    const recovery = c.enterConversation({ sessionId: 's1', kind: 'bot' });
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    c.openTask({ id: 'task-explicit', type: 'taskPanel.TaskLoopView', title: 'E', params: { taskId: 'explicit' }, closable: true }, 'explicit');
    finish({ messages: [], nextCursor: null }); await recovery;
    expect(c.getSnapshot().activeTaskId).toBe('explicit');
    expect(h.present).toHaveBeenCalledTimes(1); c.dispose();
  });
  it('pauses after the page budget and continues rather than reporting not found', async () => {
    let page = 0;
    const h = host({ history: { readPage: vi.fn(async () => ({ messages: ++page === 21
      ? [{ id: 'hit', content: declaration('t21'), timestamp: 1 }] : [], nextCursor: String(page) })) } });
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().recovery.status).toBe('incomplete');
    expect(h.history.readPage).toHaveBeenCalledTimes(20);
    await c.continueRecovery(); expect(c.getSnapshot().activeTaskId).toBe('t21'); c.dispose();
  });
  it('rejects nonadvancing cursors', async () => {
    const h = host({ history: { readPage: vi.fn(async () => ({ messages: [], nextCursor: 'same' })) } });
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().recovery.status).toBe('error');
    expect(h.history.readPage).toHaveBeenCalledTimes(2); c.dispose();
  });
  it('deduplicates repeated message declarations', () => {
    const h = host(); const c = createTaskPanelController(h);
    c.ingestMessages([{ id: 'm', timestamp: 1, content: declaration('t1') }]);
    c.ingestMessages([{ id: 'm', timestamp: 1, content: declaration('t1') }]);
    expect(h.present).toHaveBeenCalledTimes(1); c.dispose();
  });

  it('opens every task declaration in a message batch and focuses the newest task', () => {
    const h = host(); const c = createTaskPanelController(h);
    c.ingestMessages([
      { id: 'old', timestamp: 1, content: declaration('t1') },
      { id: 'new', timestamp: 2, content: declaration('t2') },
    ]);
    const present = vi.mocked(h.present);
    expect(present).toHaveBeenCalledTimes(2);
    expect(present.mock.calls.map(([tab]) => tab.params.taskId)).toEqual(['t1', 't2']);
    expect(c.getSnapshot().activeTaskId).toBe('t2');
    c.dispose();
  });

  it('opens every declaration found on the recovery page without collapsing to one task', async () => {
    const h = host({ history: { readPage: vi.fn(async () => ({
      messages: [
        { id: 'new', timestamp: 2, content: declaration('t2') },
        { id: 'old', timestamp: 1, content: declaration('t1') },
      ],
      nextCursor: null,
    })) } });
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    const present = vi.mocked(h.present);
    expect(present.mock.calls.map(([tab]) => tab.params.taskId)).toEqual(['t1', 't2']);
    expect(c.getSnapshot().activeTaskId).toBe('t2');
    c.dispose();
  });
});

describe('review regressions', () => {
  it('keeps message focus and opened status when history returns late', async () => {
    let finish!: (page: { messages: []; nextCursor: null }) => void;
    const h = host({ history: { readPage: vi.fn(() => new Promise<HistoryPage>(resolve => { finish = resolve; })) } });
    const c = createTaskPanelController(h); const pending = c.enterConversation({ sessionId: 's1', kind: 'bot' });
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'));
    c.ingestMessages([{ id: 'm', timestamp: 1, content: declaration('t1') }]);
    finish({ messages: [], nextCursor: null }); await pending;
    expect(c.getSnapshot()).toMatchObject({ activeTaskId: 't1', recovery: { status: 'opened' } }); c.dispose();
  });
  it('shows permission failure rather than not-found', async () => {
    const h = host({ transport: { requestJson: vi.fn(async () => { throw Object.assign(new Error('forbidden'), { status: 403 }); }) } });
    const c = createTaskPanelController(h); await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().recovery.status).toBe('error'); expect(h.history.readPage).not.toHaveBeenCalled(); c.dispose();
  });
  it('does not convert a failed fallback list into a successful empty result', async () => {
    const h = host({ transport: { requestJson: vi.fn(async () => { throw new Error('offline'); }) } });
    const c = createTaskPanelController(h); await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().recovery.status).toBe('error'); c.dispose();
  });
  it('cancels the previous navigation on an ordinary session change', async () => {
    let navigationSignal!: AbortSignal; let finish!: (result: { status: 'denied'; reason: string }) => void;
    const h = host({ navigate: vi.fn((_target, signal) => { navigationSignal = signal; return new Promise<NavigationOutcome>(resolve => { finish = resolve; }); }) });
    const c = createTaskPanelController(h); await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    c.nav('t1').selectNode('n', { sessionId: 'target', kind: 'bot' });
    await c.enterConversation({ sessionId: 's2', kind: 'bot' });
    expect(navigationSignal.aborted).toBe(true); finish({ status: 'denied', reason: 'old navigation' });
    await Promise.resolve(); expect(c.getSnapshot().navigationError).toBeNull(); c.dispose();
  });
});

describe('review: real envelope and origin handoff', () => {
  it('recognizes a business envelope permission failure', async () => {
    const h = host({ transport: { requestJson: vi.fn(async () => ({ code: 403000, message: 'Forbidden' })) } });
    const c = createTaskPanelController(h); await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().recovery.status).toBe('error'); expect(h.history.readPage).not.toHaveBeenCalled(); c.dispose();
  });
  it('keeps the task when restoring the opaque origin through a real session handoff', async () => {
    const h = host(); const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    c.openTask({ id: 'task-t1', type: 'taskPanel.TaskLoopView', title: 'T', params: { taskId: 't1' }, closable: true }, 'explicit');
    h.navigate = vi.fn(async (_target, signal) => {
      await c.enterConversation({ sessionId: 'child', kind: 'group' });
      expect(signal.aborted).toBe(false); return { status: 'ready' as const };
    });
    c.nav('t1').selectNode('n', { sessionId: 'child', kind: 'group' }); await new Promise(r => setTimeout(r, 0));
    h.restoreOrigin = vi.fn(async (origin, signal) => {
      expect(origin).toBe('origin'); await c.enterConversation({ sessionId: 's1', kind: 'bot' });
      expect(signal.aborted).toBe(false); return { status: 'ready' as const };
    });
    c.nav('t1').selectRootNode('root'); await new Promise(r => setTimeout(r, 0));
    expect(c.getSnapshot().activeTaskId).toBe('t1'); c.dispose();
  });
});

describe('SDK binding and identity lifecycle', () => {
  const tab = (id: string) => ({ id: `task-${id}`, type: 'taskPanel.TaskLoopView', title: '任务', params: { taskId: id }, closable: true });
  it('adopts a mounted SDK task without opening a duplicate tab or losing focus to recovery', async () => {
    let resolve!: (value: unknown) => void;
    const h = host({ transport: { requestJson: path => path.includes('by_session')
      ? new Promise(done => { resolve = done; }) : Promise.resolve({ data: { items: [] } }) } });
    const c = createTaskPanelController(h);
    const pending = c.enterConversation({ sessionId: 's1', kind: 'bot' });
    c.adoptPresentedTask(tab('mounted')); resolve({ data: { items: [{ task_id: 'other' }] } }); await pending;
    expect(c.getSnapshot().activeTaskId).toBe('mounted');
    expect(vi.mocked(h.present).mock.calls.map(([value]) => value.params.taskId)).toEqual(['other']); c.dispose();
  });
  it('keeps user navigation and default selection out of the SDK interaction channel', () => {
    const h = host(); const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
    c.nav('t1').selectDefaultNode('first'); expect(h.recordInteraction).not.toHaveBeenCalled();
    c.nav('t1').selectNode('second', null); c.nav('t1').drillInto('second', null); c.nav('t1').backToOverview();
    expect(c.nav('t1')).toMatchObject({ selectedNodeId: 'second', drillStack: [] });
    expect(h.recordInteraction).not.toHaveBeenCalled(); c.dispose();
  });
  it('clears per-user task navigation and origins when the viewer changes', async () => {
    let userId = 'alice'; const h = host({ getViewer: () => ({ userId }) }); const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' }); c.adoptPresentedTask(tab('t1')); c.nav('t1').selectDefaultNode('secret');
    userId = 'bob'; await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(c.getSnapshot().navByTask).toEqual({}); expect(c.getSnapshot().activeTaskId).toBeNull(); c.dispose();
  });
  it('closing a panel aborts its pending navigation and releases preservation', async () => {
    let signal!: AbortSignal;
    const h = host({ navigate: async (_target, value) => { signal = value; return new Promise(() => {}); } });
    const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
    c.nav('t1').selectNode('n', { sessionId: 'child', kind: 'group' }); c.closeTask();
    expect(signal.aborted).toBe(true); expect(h.preservePanel).toHaveBeenLastCalledWith(false); c.dispose();
  });
});

describe('conversation-scoped SDK presentation handshake', () => {
  it('adopts an already visible card before recovery while retaining all associated ids', async () => {
    const h = host({ transport: { requestJson: vi.fn(async path => ({ data: { items: path.includes('by_session')
      ? [{ task_id: 'latest' }, { task_id: 'relay' }] : [] } })) } });
    const c = createTaskPanelController(h);
    const tab = { id: 'task-shown', type: 'taskPanel.TaskLoopView', title: 'Shown', closable: true, params: { taskId: 'shown' } };
    c.adoptPresentedTask(tab);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' }, tab);
    expect(c.getSnapshot().activeTaskId).toBe('shown');
    expect(c.getSnapshot().associatedTaskIds).toEqual(['latest', 'relay', 'shown']);
    expect(vi.mocked(h.present).mock.calls.map(([value]) => value.params.taskId)).toEqual(['relay', 'latest']);
    expect(h.history.readPage).not.toHaveBeenCalled();
    c.dispose();
  });

  it('continues normal recovery in another conversation without a scoped presented declaration', async () => {
    const h = host({ transport: { requestJson: vi.fn(async path => ({ data: { items: path.includes('by_session') ? [{ task_id: 'new' }] : [] } })) } });
    const c = createTaskPanelController(h);
    c.adoptPresentedTask({ id: 'task-old', type: 'taskPanel.TaskLoopView', title: 'Old', closable: true, params: { taskId: 'old' } });
    await c.enterConversation({ sessionId: 's2', kind: 'bot' });
    expect(c.getSnapshot().activeTaskId).toBe('new');
    expect(h.present).toHaveBeenCalledTimes(1);
    c.dispose();
  });

  it('cancels a collapsed panel navigation without destroying its selected task or node', async () => {
    let signal!: AbortSignal;
    const h = host({ navigate: vi.fn((_target, pending) => { signal = pending; return new Promise<NavigationOutcome>(resolve => {
      pending.addEventListener('abort', () => resolve({ status: 'cancelled', reason: 'closed' }), { once: true });
    }); }) });
    const c = createTaskPanelController(h);
    c.adoptPresentedTask({ id: 'task-t1', type: 'taskPanel.TaskLoopView', title: 'T', closable: true, params: { taskId: 't1' } });
    c.nav('t1').selectNode('node', { sessionId: 's2', kind: 'bot' });
    c.cancelNavigation();
    expect(signal.aborted).toBe(true);
    expect(c.getSnapshot().activeTaskId).toBe('t1');
    expect(c.getSnapshot().navByTask.t1.selectedNodeId).toBe('node');
    await Promise.resolve();
    expect(c.getSnapshot().navigationError).toBeNull();
    c.dispose();
  });
});

it('a disposed controller and captured callbacks cannot affect the next viewer runtime', async () => {
  const h = host(); const c = createTaskPanelController(h);
  c.adoptPresentedTask({ id: 'task-t1', type: 'taskPanel.TaskLoopView', title: 'T', closable: true, params: { taskId: 't1' } });
  const oldNav = c.nav('t1');
  c.dispose(); const disposed = c.getSnapshot();
  vi.clearAllMocks();
  oldNav.selectDefaultNode('stale');
  oldNav.selectNode('stale', { sessionId: 'other', kind: 'bot' });
  oldNav.selectRootNode('stale'); oldNav.drillInto('stale', { sessionId: 'other', kind: 'bot' }); oldNav.backToOverview();
  c.closeTask(); c.cancelNavigation(); c.dispose();
  await Promise.resolve();
  expect(c.getSnapshot()).toBe(disposed);
  expect(h.navigate).not.toHaveBeenCalled();
  expect(h.restoreOrigin).not.toHaveBeenCalled();
  expect(h.recordInteraction).not.toHaveBeenCalled();
  expect(h.preservePanel).not.toHaveBeenCalled();
});

it('preserves the root task if route readiness precedes target visible-history readiness', async () => {
  const h = host({ transport: { requestJson: vi.fn(async path => ({ data: { items: path.includes('by_session') ? [{ task_id: 'target-latest' }] : [] } })) } });
  const c = createTaskPanelController(h);
  await c.enterConversation({ sessionId: 's1', kind: 'bot' });
  c.openTask({ id: 'task-root', type: 'taskPanel.TaskLoopView', title: 'Root', closable: true, params: { taskId: 'root' } }, 'explicit');
  c.nav('root').selectNode('node', { sessionId: 'child', kind: 'bot' });
  await Promise.resolve(); await Promise.resolve();
  await c.enterConversation({ sessionId: 'child', kind: 'bot' }, null, { recover: false });
  expect(c.getSnapshot().activeTaskId).toBe('root');
  await c.enterConversation({ sessionId: 'child', kind: 'bot' });
  expect(c.getSnapshot().activeTaskId).toBe('root');
  expect(c.getSnapshot().conversation?.sessionId).toBe('child');
  c.dispose();
});

it('defers ordinary recovery until visible history is ready, without deferring the session boundary', async () => {
  const h = host(); const c = createTaskPanelController(h);
  await c.enterConversation({ sessionId: 'slow-history', kind: 'bot' }, null, { recover: false });
  expect(c.getSnapshot().conversation?.sessionId).toBe('slow-history');
  expect(h.transport.requestJson).not.toHaveBeenCalled();
  expect(h.history.readPage).not.toHaveBeenCalled();
  await c.enterConversation({ sessionId: 'slow-history', kind: 'bot' });
  expect(h.transport.requestJson).toHaveBeenCalled();
  expect(h.history.readPage).toHaveBeenCalledTimes(1);
  c.dispose();
});

it('retries an association permission error at the same boundary, never as empty history fallback', async () => {
  const h = host({ transport: { requestJson: vi.fn(async () => { throw Object.assign(new Error('denied'), { status: 403 }); }) } });
  const c = createTaskPanelController(h);
  await c.enterConversation({ sessionId: 'private', kind: 'bot' });
  expect(c.getSnapshot().recovery.status).toBe('error');
  await c.continueRecovery();
  expect(h.transport.requestJson).toHaveBeenCalledTimes(2);
  expect(h.history.readPage).not.toHaveBeenCalled();
  expect(c.getSnapshot().recovery.status).toBe('error');
  c.dispose();
});
