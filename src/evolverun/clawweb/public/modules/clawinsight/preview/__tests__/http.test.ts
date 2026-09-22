// @vitest-environment node
import { once } from 'node:events';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { createPreviewApp } from '../server';
let fixture: Awaited<ReturnType<typeof createPreviewApp>>;
let server: ReturnType<typeof fixture.app.listen>;
let base: string;
const request = (path: string, role = 'member', init: RequestInit = {}) => fetch(`${base}${path}`, {
  ...init, headers: { cookie: `monitoring_preview_role=${role}`, ...init.headers },
});
const options = (q = '', role = 'member') => request(`/monitoring/bot-options?start=all&end=all${q}`, role);
beforeAll(async () => {
  fixture = await createPreviewApp(); server = fixture.app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('No bound address');
  base = `http://127.0.0.1:${address.port}/api/insight/v1`;
});
afterAll(async () => { await new Promise<void>(resolve => server.close(() => resolve())); await fixture.close(); });
describe('real local preview HTTP + directory + monitoring SQL', () => {
  it('requires login and lists only owned Bots; rejects scope escalation', async () => {
    expect((await options('', '')).status).toBe(401);
    const page = await (await options()).json();
    expect(page.items).toHaveLength(20); expect(page.items.every((b: {ownerId: string}) => b.ownerId === '001234')).toBe(true);
    const next = await (await options(`&cursor=${page.nextCursor}`)).json();
    expect(next.items).toHaveLength(10); expect(next.nextCursor).toBeNull();
    expect((await options('&scope=all')).status).toBe(403);
    expect((await options('&ownerId=009876')).status).toBe(400);
  });
  it('supports member name/ID search and admin staff search, keeping same default identities separate', async () => {
    expect((await (await options(`&q=${encodeURIComponent('研究助手')}`)).json()).items).toHaveLength(1);
    expect((await (await options('&q=009876')).json()).items).toHaveLength(0);
    const foreign = await (await options('&scope=all&q=009876', 'admin')).json();
    expect(foreign.items).toHaveLength(2);
    const defaults = await (await options('&scope=all&q=default', 'admin')).json();
    expect(new Set(defaults.items.map((b: {botRef: string}) => b.botRef)).size).toBe(3);
    const ref = foreign.items.find((b: {botId: string}) => b.botId === 'default').botRef;
    expect((await request(`/monitoring/targets/${ref}/status`)).status).toBe(404);
    expect((await request(`/monitoring/targets/${ref}/status`, 'admin')).status).toBe(200);
  });
  it('returns real summaries and records past the fifth page', async () => {
    const page = await (await options('&q=default')).json();
    const bot = page.items.find((b: {env: string}) => b.env === 'dev');
    expect(bot.monitoring).toMatchObject({ diagnosisCount: 163, alertCount: 55, diagnosedSessionCount: 82 });
    const response = await request(`/monitoring/targets/${bot.botRef}/diagnoses?start=all&end=all&page=9&pageSize=20`);
    expect(await response.json()).toMatchObject({ page: 9, totalPages: 9, total: 163, items: expect.any(Array) });
  });
  it('returns the user-facing enrollment placeholder without database writes', async () => {
    const page = await (await options('&q=research-new')).json(); const bot = page.items[0];
    expect(bot).toMatchObject({ enrollmentState: 'NOT_ENROLLED', monitoring: null });
    const exec = vi.spyOn(fixture.db, 'exec');
    const response = await request('/monitoring/enrollments', 'member', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ botRef: bot.botRef }) });
    expect(response.status).toBe(501);
    expect(await response.json()).toMatchObject({ error: { code: 'MONITORING_ENROLLMENT_NOT_IMPLEMENTED', message: '加入监控功能正在开发中，敬请期待。' } });
    expect(exec).not.toHaveBeenCalled(); exec.mockRestore();
  });
});
