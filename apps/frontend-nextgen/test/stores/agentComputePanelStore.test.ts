import { isPanelOpenFor, useAgentComputePanelStore } from '@/stores/agentComputePanelStore';

beforeEach(() => useAgentComputePanelStore.getState().reset());

test('the panel starts closed', () => {
  const state = useAgentComputePanelStore.getState();
  expect(state.sessionId).toBeUndefined();
  expect(isPanelOpenFor(state.sessionId, 's1')).toBe(false);
});

test('opening targets the draft view for one session', () => {
  useAgentComputePanelStore.getState().open('s1');
  const state = useAgentComputePanelStore.getState();
  expect(state.sessionId).toBe('s1');
  expect(state.view).toBe('draft');
  expect(state.runId).toBeUndefined();
});

test('查看计算任务详情 opens straight onto that run', () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-9');
  const state = useAgentComputePanelStore.getState();
  expect(state.view).toBe('detail');
  expect(state.runId).toBe('run-9');
});

test('a panel opened in one session does not show in another', () => {
  useAgentComputePanelStore.getState().open('s1');
  expect(isPanelOpenFor(useAgentComputePanelStore.getState().sessionId, 's1')).toBe(true);
  expect(isPanelOpenFor(useAgentComputePanelStore.getState().sessionId, 's2')).toBe(false);
});

test('selecting a run switches to detail without changing the session', () => {
  useAgentComputePanelStore.getState().open('s1');
  useAgentComputePanelStore.getState().selectRun('run-2');
  const state = useAgentComputePanelStore.getState();
  expect(state.sessionId).toBe('s1');
  expect(state.view).toBe('detail');
  expect(state.runId).toBe('run-2');
});

test('going back to the draft clears the run selection', () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-9');
  useAgentComputePanelStore.getState().showDraft('s1');
  const state = useAgentComputePanelStore.getState();
  expect(state.view).toBe('draft');
  expect(state.runId).toBeUndefined();
});

test('closing clears the session so nothing renders', () => {
  useAgentComputePanelStore.getState().openDetail('s1', 'run-9');
  useAgentComputePanelStore.getState().close();
  expect(useAgentComputePanelStore.getState().sessionId).toBeUndefined();
  expect(isPanelOpenFor(useAgentComputePanelStore.getState().sessionId, 's1')).toBe(false);
});
test('the panel width is clamped to its bounds', () => {
  useAgentComputePanelStore.getState().setWidth(9999);
  expect(useAgentComputePanelStore.getState().width).toBe(720);
  useAgentComputePanelStore.getState().setWidth(10);
  expect(useAgentComputePanelStore.getState().width).toBe(380);
});

test('a chosen width survives closing and reopening the panel', () => {
  useAgentComputePanelStore.getState().open('s1');
  useAgentComputePanelStore.getState().setWidth(600);
  useAgentComputePanelStore.getState().close();
  useAgentComputePanelStore.getState().open('s2');
  expect(useAgentComputePanelStore.getState().width).toBe(600);
});
