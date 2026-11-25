import { describe, expect, it, vi } from 'vitest';
import { createTaskClient, responseItems } from '../../src/data/taskClient';
import { createTaskPanelController } from '../../src/controller/createTaskPanelController';
import type { TaskPanelHost } from '../../src/contracts';

function host(response: unknown): TaskPanelHost {
  return {
    getViewer: () => ({ userId: 'viewer' }), getConversation: () => null,
    transport: { requestJson: vi.fn(async path => path.includes('/by_session') ? [] : response) },
    history: { readPage: vi.fn(async () => ({ messages: [], nextCursor: null })) },
    artifacts: { readContent: vi.fn(), download: vi.fn() },
    navigate: vi.fn(), captureOrigin: vi.fn(), restoreOrigin: vi.fn(),
    present: vi.fn(), preservePanel: vi.fn(), recordInteraction: vi.fn(),
  };
}

describe('task list response boundary', () => {
  it.each([null, undefined, {}, { items: null }, { items: {} }, 'private-response'])('rejects malformed DTO %# instead of returning an empty list', value => {
    expect(() => responseItems(value)).toThrow(/Invalid task response.*items/);
  });

  it.each([[], { items: [] }, { data: [] }, { code: 200000, data: { items: [] } }, { code: 0, success: true, data: [] }])('accepts valid empty response %#', value => {
    expect(responseItems(value)).toEqual([]);
  });

  it.each(['list', 'bySession'] as const)('rejects the entire %s response when one task_id is invalid', async method => {
    for (const row of [null, [], 'secret-row', {}, { task_id: 7 }, { task_id: '' }, { task_id: ' \n ' }]) {
      const h = host(null);
      h.transport.requestJson = vi.fn(async () => ({ data: [{ task_id: 'valid' }, row] }));
      await expect(createTaskClient(h)[method]('owner', new AbortController().signal)).rejects.toThrow(/Invalid task response.*items\[1\].task_id/);
    }
  });

  it('rejects malformed consumed metadata with a field path but no response data', async () => {
    const h = host({ data: { items: [{ task_id: 'secret-task', task_spec: { context: { title: { secret: 'private' } } } }] } });
    await expect(createTaskClient(h).list('owner', new AbortController().signal)).rejects.toThrow(/items\[0\].task_spec.context.title/);
    try { await createTaskClient(h).list('owner', new AbortController().signal); }
    catch (error) { expect(String(error)).not.toMatch(/secret-task|private/); }
  });

  it('preserves records, encoded query parameters and successful envelopes', async () => {
    const record = { task_id: 't1', status: 'DONE', task_spec: { context: { title: 'Title' } } };
    const h = host(null);
    h.transport.requestJson = vi.fn(async () => ({ code: 200000, data: { items: [record], total: 1 } }));
    const client = createTaskClient(h); const signal = new AbortController().signal;
    await expect(client.list('owner & 1', signal)).resolves.toEqual([record]);
    await expect(client.bySession('session:1', signal)).resolves.toEqual(['t1']);
    expect(h.transport.requestJson).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/list?user_id=owner%20%26%201', signal);
    expect(h.transport.requestJson).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/by_session?session_id=session%3A1', signal);
  });

  it('accepts nullable backend metadata and preserves unknown fields', async () => {
    const record = { task_id: 't1', status: 'DONE', owner_bot_name: null, gmt_create: null,
      execution_config: null, task_spec: { context: { title: null } }, extra: { future: true } };
    await expect(createTaskClient(host({ data: [record] })).list('owner', new AbortController().signal)).resolves.toEqual([record]);
  });

  it.each([{ status: 1 }, { execution_config: [] }, { task_spec: 'private' },
    { execution_config: { main_session_id: 7 } }, { task_spec: { goal: { objective: [] } } }])('rejects malformed metadata %#', async metadata => {
    await expect(createTaskClient(host([{ task_id: 't1', ...metadata }])).list('owner', new AbortController().signal)).rejects.toThrow(/Invalid task response at items\[0\]/);
  });

  it('controller treats a valid empty fallback list as not-found', async () => {
    const controller = createTaskPanelController(host({ data: { items: [] } }));
    try {
      await controller.enterConversation({ sessionId: 'session', kind: 'bot' });
      expect(controller.getSnapshot().recovery).toEqual({ status: 'not-found' });
    } finally { controller.dispose(); }
  });

  it('preserves business and HTTP failures', async () => {
    const h = host({ code: 403000, message: 'Forbidden' });
    await expect(createTaskClient(h).list('owner', new AbortController().signal)).rejects.toMatchObject({ status: 403, code: 403000, message: 'Forbidden' });
    const failure = Object.assign(new Error('HTTP failure'), { status: 401 });
    h.transport.requestJson = vi.fn(async () => { throw failure; });
    await expect(createTaskClient(h).bySession('s', new AbortController().signal)).rejects.toBe(failure);
  });

  it.each([{ items: [{ task_id: '' }] }, { items: null }, { unexpected: 'private-response' }])('controller reports invalid fallback lists as error, never not-found %#', async data => {
    const h = host({ data });
    const controller = createTaskPanelController(h);
    try {
      await controller.enterConversation({ sessionId: 'session', kind: 'bot' });
      expect(controller.getSnapshot().recovery).toMatchObject({ status: 'error', reason: expect.stringContaining('Invalid task response at items') });
      expect(h.present).not.toHaveBeenCalled();
    } finally { controller.dispose(); }
  });
});
