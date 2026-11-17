import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { FlowEventRepository } from '../../../repositories/event-repository.js';
import { createInternalEventsRouter } from '../events.js';

const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup();
  vi.restoreAllMocks();
});
const event = {
  event_id: 'event-1', flow_id: 'flow-1', workflow_id: 'wf-1', node_id: 'train',
  event_type: 'node.manual_poll_requested', attempt: 1, time: 1794820000000,
  data_json: JSON.stringify({ source: 'manual' }),
};
async function fixture() {
  const raw = new Database(':memory:');
  raw.exec(`CREATE TABLE flow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
    flow_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT,
    event_type TEXT NOT NULL, attempt INTEGER, time INTEGER NOT NULL,
    data_json TEXT, error_text TEXT, gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
  )`);
  const repo = new FlowEventRepository(new SqliteDatabase(raw));
  const app = express(); app.use(express.json());
  app.use('/api/internal/events', createInternalEventsRouter(repo));
  const server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  cleanups.push(async () => { await new Promise<void>(resolve => server.close(() => resolve())); raw.close(); });
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/internal/events`;
  const post = (body = event) => fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return { raw, repo, post };
}
describe('internal event persistence acknowledgement', () => {
  it('preserves the successful 201 envelope only for a confirmed insert and stores epoch seconds', async () => {
    const f = await fixture(); const response = await f.post();
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ success: true, data: { inserted: true } });
    expect(f.raw.prepare('SELECT event_id, time, data_json FROM flow_events').get()).toEqual({
      event_id: event.event_id, time: 1794820000, data_json: event.data_json,
    });
  });
  it('returns non-2xx failure when a real SQLite insert fails and the repository returns false', async () => {
    const f = await fixture(); vi.spyOn(console, 'warn').mockImplementation(() => {});
    f.raw.exec("CREATE TRIGGER fail_event_write BEFORE INSERT ON flow_events BEGIN SELECT RAISE(FAIL, 'private SQL persistence detail'); END");
    const response = await f.post();
    expect(response.status).toBe(503); expect(response.ok).toBe(false);
    const body = await response.json(); expect(body.success).toBe(false);
    expect(JSON.stringify(body)).not.toContain('private SQL');
    expect(f.raw.prepare('SELECT COUNT(*) AS count FROM flow_events').get()).toEqual({ count: 0 });
  });
  it('does not turn an unconfirmed duplicate insert into a positive audit ACK or duplicate the row', async () => {
    const f = await fixture(); vi.spyOn(console, 'warn').mockImplementation(() => {});
    const first = await f.post(); expect(first.status).toBe(201); await first.json();
    const duplicate = await f.post(); expect(duplicate.status).toBe(503); expect(duplicate.ok).toBe(false);
    expect((await duplicate.json()).success).toBe(false);
    expect(f.raw.prepare('SELECT COUNT(*) AS count FROM flow_events').get()).toEqual({ count: 1 });
    const next = await f.post({ ...event, event_id: 'event-2' });
    expect(next.status).toBe(201); expect(await next.json()).toEqual({ success: true, data: { inserted: true } });
  });
});
