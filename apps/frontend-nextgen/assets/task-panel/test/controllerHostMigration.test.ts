import { describe, expect, it, vi } from 'vitest';
import type { NavigationOutcome } from '../src/contracts';
import { createTaskPanelController } from '../src/controller/createTaskPanelController';
import { makeHost, tab } from './panel/taskSwitcherFixtures';

/** Direct domain assertions migrated from the retired nextgen store and observer. */
describe('controller owns former host task navigation policy', () => {
  it('starts without active task or nav slices; null target still selects without navigation', () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    expect(c.getSnapshot()).toMatchObject({ activeTaskId: null, navByTask: {} });
    c.nav('t1').selectNode('n2', null);
    expect(c.nav('t1')).toMatchObject({ selectedNodeId: 'n2', drillStack: [] });
    expect(h.navigate).not.toHaveBeenCalled(); c.dispose();
  });
  it('selects a node and forwards every host route/time-window field exactly once', async () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    const target = { sessionId: 'bcs_grp_902:round:2', kind: 'group' as const, groupId: 'g902', botId: null,
      startTime: 1700000000000, endTime: 1700000005000 };
    c.nav('t1').selectNode('n3', target); await Promise.resolve();
    expect(c.nav('t1').selectedNodeId).toBe('n3');
    expect(h.navigate).toHaveBeenCalledTimes(1); expect(h.navigate).toHaveBeenCalledWith(target, expect.any(AbortSignal));
    expect(h.recordInteraction).not.toHaveBeenCalled(); c.dispose();
  });
  it('deduplicates drill entries and bounds the current shared stack to two, without selecting another node', () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    c.nav('t1').selectDefaultNode('chosen');
    c.nav('t1').drillInto('n2', null); c.nav('t1').drillInto('n2', null);
    expect(c.nav('t1').drillStack).toEqual(['n2']);
    c.nav('t1').drillInto('n2v', null); expect(c.nav('t1').drillStack).toEqual(['n2', 'n2v']);
    c.nav('t1').drillInto('n3', null);
    // Preserve the original depth cap: a defensive third drill cannot evict an existing level.
    expect(c.nav('t1').drillStack).toEqual(['n2', 'n2v']);
    expect(c.nav('t1').selectedNodeId).toBe('chosen'); expect(h.navigate).not.toHaveBeenCalled(); c.dispose();
  });
  it('drilling navigates; returning to overview preserves selection and isolates sibling tasks', async () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    c.nav('tA').selectNode('selected', null);
    c.nav('tA').drillInto('child', { sessionId: 'child-session', kind: 'bot', botId: 'bot9' });
    c.nav('tB').drillInto('other', null); c.nav('tA').backToOverview(); await Promise.resolve();
    expect(c.nav('tA')).toMatchObject({ selectedNodeId: 'selected', drillStack: [] });
    expect(c.nav('tB')).toMatchObject({ selectedNodeId: null, drillStack: ['other'] });
    expect(h.navigate).toHaveBeenCalledTimes(1); c.dispose();
  });
  it('root without an origin selects without navigation or synthetic scroll', () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    c.nav('t1').selectRootNode('root');
    expect(c.nav('t1').selectedNodeId).toBe('root');
    expect(h.navigate).not.toHaveBeenCalled(); expect(h.restoreOrigin).not.toHaveBeenCalled(); c.dispose();
  });
  it('captures origin once and restores it after sibling switching, cancelling previous node navigation', async () => {
    let oldSignal!: AbortSignal;
    const h = makeHost(); h.navigate = vi.fn(async (_target, signal) => { oldSignal = signal; return new Promise<NavigationOutcome>(() => {}); });
    const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
    c.nav('t1').selectNode('linked', { sessionId: 'linked', kind: 'group' });
    c.openTask(tab('t2'), 'explicit'); c.nav('t2').selectRootNode('root'); await Promise.resolve();
    expect(oldSignal.aborted).toBe(true);
    expect(h.captureOrigin).toHaveBeenCalledTimes(1);
    expect(h.restoreOrigin).toHaveBeenCalledWith({ opaque: 'origin' }, expect.any(AbortSignal)); c.dispose();
  });
  it('new task gets an empty slice; reopening a displayed task preserves its real shared selection', () => {
    const h = makeHost(); const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
    c.nav('t1').selectDefaultNode('chosen'); c.nav('t1').drillInto('child', null);
    c.openTask(tab('t2'), 'explicit'); expect(c.nav('t2')).toMatchObject({ selectedNodeId: null, drillStack: [] });
    c.openTask(tab('t1'), 'explicit');
    expect(c.nav('t1')).toMatchObject({ selectedNodeId: 'chosen', drillStack: ['child'] }); c.dispose();
  });
  it('cancel/close aborts navigation instead of keeping a host pending-navigation queue', async () => {
    let signal!: AbortSignal; const h = makeHost();
    h.navigate = vi.fn(async (_target, pending) => { signal = pending; return new Promise<NavigationOutcome>(() => {}); });
    const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
    c.nav('t1').selectNode('n', { sessionId: 'child', kind: 'bot' });
    c.cancelNavigation(); expect(signal.aborted).toBe(true); expect(c.getSnapshot().activeTaskId).toBe('t1');
    c.closeTask(); expect(c.getSnapshot().activeTaskId).toBeNull();
    expect(h.preservePanel).toHaveBeenLastCalledWith(false); c.dispose();
  });
  it('leaving a conversation clears focus/associations; viewer change clears the previous viewer nav slices', async () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 'shared', kind: 'bot' }, tab('t1'), { recover: false });
    c.nav('t1').selectDefaultNode('selected'); c.leaveConversation();
    expect(c.getSnapshot()).toMatchObject({ activeTaskId: null, associatedTaskIds: [], conversation: null });
    h.getViewer = () => ({ userId: 'next-viewer' });
    await c.enterConversation({ sessionId: 'shared', kind: 'bot' }, null, { recover: false });
    expect(c.getSnapshot().navByTask).toEqual({}); c.dispose();
  });
});


it('all task browsing commands are silent for both host adapters', () => {
  const h = makeHost(); const c = createTaskPanelController(h); c.adoptPresentedTask(tab('t1'));
  c.nav('t1').selectDefaultNode('default');
  c.nav('t1').selectNode('node', null);
  c.nav('t1').selectRootNode('root');
  c.nav('t1').drillInto('child', null);
  c.nav('t1').backToOverview();
  expect(c.nav('t1')).toMatchObject({ selectedNodeId: 'root', drillStack: [] });
  expect(h.recordInteraction).not.toHaveBeenCalled();
  c.dispose();
});
