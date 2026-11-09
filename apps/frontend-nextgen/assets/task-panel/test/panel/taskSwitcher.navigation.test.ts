import { describe, expect, it } from 'vitest';
import { createTaskPanelController } from '../../src/controller/createTaskPanelController';
import { makeHost, tab } from './taskSwitcherFixtures';

describe('shared controller nav policy (former taskPanelNavBinding)', () => {
  it('defaults to an empty task slice and keeps instance callbacks isolated', () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    const a = c.nav('tA'); const b = c.nav('tB');
    expect(a.selectedNodeId).toBeNull(); expect(a.drillStack).toEqual([]);
    a.selectNode('nA', null); b.selectNode('nB', null);
    a.drillInto('childA', null); b.drillInto('childB', null);
    expect(c.getSnapshot().navByTask).toMatchObject({
      tA: { selectedNodeId: 'nA', drillStack: ['childA'] }, tB: { selectedNodeId: 'nB', drillStack: ['childB'] },
    });
    a.backToOverview(); expect(c.nav('tA').drillStack).toEqual([]); expect(c.nav('tB').drillStack).toEqual(['childB']);
    c.dispose();
  });
  it('select/drill invokes the host navigator with the exact target; back only clears the task stack', async () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    c.nav('t1').selectNode('n2', { sessionId: 'sess-main', kind: 'bot' });
    c.nav('t1').drillInto('n2', { sessionId: 'sess-grp', kind: 'group', groupId: 'g902' });
    expect(c.nav('t1')).toMatchObject({ selectedNodeId: 'n2', drillStack: ['n2'] });
    expect(h.navigate).toHaveBeenLastCalledWith({ sessionId: 'sess-grp', kind: 'group', groupId: 'g902' }, expect.any(AbortSignal));
    c.nav('t1').backToOverview(); expect(c.nav('t1').drillStack).toEqual([]);
    expect(h.navigate).toHaveBeenCalledTimes(2); await Promise.resolve(); c.dispose();
  });
  it('default node selection changes only that task, with no navigation, preservation or user interaction', () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    c.nav('t1').selectDefaultNode('first');
    expect(c.nav('t1').selectedNodeId).toBe('first');
    expect(h.navigate).not.toHaveBeenCalled(); expect(h.restoreOrigin).not.toHaveBeenCalled();
    expect(h.preservePanel).not.toHaveBeenCalled(); expect(h.recordInteraction).not.toHaveBeenCalled(); c.dispose();
  });
  it('root selection restores the opaque original host, including after switching sibling tasks', async () => {
    const h = makeHost(); const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 'shared', kind: 'bot' }, tab('t1'));
    const origin = h.captureOrigin();
    c.nav('t1').selectNode('linked', { sessionId: 'child', kind: 'group' }); await Promise.resolve();
    c.openTask(tab('t2'), 'explicit');
    c.nav('t2').selectRootNode('root'); await Promise.resolve();
    expect(c.nav('t2').selectedNodeId).toBe('root'); expect(c.nav('t1').selectedNodeId).toBe('linked');
    expect(h.restoreOrigin).toHaveBeenCalledWith(origin, expect.any(AbortSignal));
    expect(h.navigate).toHaveBeenCalledTimes(1); c.dispose();
  });
  it('root without a captured origin never invents navigation', () => {
    const h = makeHost(); const c = createTaskPanelController(h); c.nav('unopened').selectRootNode('root');
    expect(c.nav('unopened').selectedNodeId).toBe('root');
    expect(h.navigate).not.toHaveBeenCalled(); expect(h.restoreOrigin).not.toHaveBeenCalled(); c.dispose();
  });
});
