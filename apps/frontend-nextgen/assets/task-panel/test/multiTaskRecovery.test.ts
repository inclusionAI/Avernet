import { describe, expect, it, vi } from 'vitest';
import { createTaskPanelController } from '../src/controller/createTaskPanelController';
import { makeHost, tab } from './panel/taskSwitcherFixtures';

describe('conversation multi-task recovery', () => {
  it('presents every associated task, with the newest as active', async () => {
    const h = makeHost();
    h.transport.requestJson = vi.fn(async path => ({ data: { items: path.includes('by_session')
      ? [{ task_id: 'new' }, { task_id: 'old' }] : [] } }));
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(vi.mocked(h.present).mock.calls.map(([value]) => value.params.taskId).sort()).toEqual(['new', 'old']);
    expect(c.getSnapshot().activeTaskId).toBe('new');
    c.dispose();
  });

  it('card adoption does not cancel association discovery or discard sibling tasks', async () => {
    let finish!: (value: unknown) => void;
    let signal!: AbortSignal;
    const h = makeHost();
    h.transport.requestJson = vi.fn((path, pending) => {
      if (!path.includes('by_session')) return Promise.resolve({ data: { items: [] } });
      signal = pending;
      return new Promise(resolve => { finish = resolve; });
    });
    const c = createTaskPanelController(h);
    const recovery = c.enterConversation({ sessionId: 's1', kind: 'bot' });
    c.adoptPresentedTask(tab('new'));
    expect(signal.aborted).toBe(false);
    finish({ data: { items: [{ task_id: 'new' }, { task_id: 'old' }] } });
    await recovery;
    expect(c.getSnapshot().associatedTaskIds).toEqual(['new', 'old']);
    expect(c.getSnapshot().activeTaskId).toBe('new');
    expect(vi.mocked(h.present).mock.calls.map(([value]) => value.params.taskId)).toContain('old');
    c.dispose();
  });
  it('orders tabs by creation time independently of update order and default focus', async () => {
    const h = makeHost();
    h.transport.requestJson = vi.fn(async path => ({ data: { items: path.includes('by_session')
      ? [{ task_id: 'old' }, { task_id: 'new' }]
      : [{ task_id: 'new', gmt_create: '2026-11-13 11:00:00' },
        { task_id: 'old', gmt_create: '2026-11-13 10:00:00' }] } }));
    const c = createTaskPanelController(h);
    await c.enterConversation({ sessionId: 's1', kind: 'bot' });
    expect(vi.mocked(h.present).mock.calls.map(([value]) => value.params.taskId)).toEqual(['old', 'new']);
    expect(c.getSnapshot().activeTaskId).toBe('new');
    c.dispose();
  });

});
