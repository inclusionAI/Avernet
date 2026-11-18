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
    expect(next.items).toHaveLength(13); expect(next.nextCursor).toBeNull();
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
  it('marks service Bots in options and excludes them from owner writes', async () => {
    const { items: [bot] } = await (await options('&q=weekly-report')).json();
    expect(bot).toMatchObject({ botId: 'weekly-report', isServiceBot: true, configuration: { version: 0 } });
    const configure = (body: unknown) => request('/monitoring/configurations', 'member', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
    expect((await configure({ botRef: bot.botRef, enabled: true, version: 0 })).status).toBe(403);
    expect(await (await configure({ all: true, enabled: true })).json()).toEqual({ updated: 1, failed: [] });
    const { items: [after] } = await (await options('&q=weekly-report')).json();
    expect(after.configuration).toEqual(bot.configuration);
  });
  it('keeps desktop, TE and other engines visible but rejects owner writes over HTTP', async () => {
    const { items } = await (await options('&scope=mine&env=prod')).json();
    const configure = (input: unknown) => request('/monitoring/configurations', 'member', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input),
    });
    for (const [botId, reason] of [
      ['desktop-assistant', '桌面 Bot 暂不支持'], ['te-assistant', 'TE 引擎暂不支持'],
      ['claude-assistant', '非 OpenClaw 引擎暂不支持'],
    ]) {
      const bot = items.find((item: { botId: string }) => item.botId === botId);
      expect(bot).toMatchObject({ configurationDisabledReason: reason, configuration: { version: 0 } });
      for (const enabled of [true, false]) {
        const response = await configure({ botRef: bot.botRef, enabled, version: 0 });
        expect(response.status).toBe(403);
        expect(await response.json()).toMatchObject({ error: { message: reason } });
      }
    }
    const supported = items.find((item: { botId: string }) => item.botId === 'default');
    expect(supported.configurationDisabledReason).toBeUndefined();
    for (const enabled of [true, false]) {
      expect(await (await configure({ all: true, enabled })).json()).toEqual({ updated: 1, failed: [] });
      const { items: after } = await (await options('&scope=mine&env=prod')).json();
      expect(after.filter((item: { configurationDisabledReason?: string }) => item.configurationDisabledReason)
        .every((item: { configuration: { version: number } }) => item.configuration.version === 0)).toBe(true);
    }
  });
  it('shows only production Bot options and preserves pre configuration during bulk changes', async () => {
    await fixture.db.exec("INSERT INTO ac_bots (bot_id,entity_id,env,owner_id,avernet_tenant,bot_type) VALUES ('default','001234','pre','001234','teamclaw','personal')");
    await fixture.db.exec("INSERT INTO ce_bot_diagnosis_recovery_config (bot_id,entity_id,env,diagnosis_enabled,recovery_enabled,config_json,version) VALUES ('default','001234','pre',1,1,'{}',1)");
    const { items } = await (await options('&scope=mine&env=prod')).json();
    expect(items).toHaveLength(5);
    expect(items.every((bot: { env: string }) => bot.env === 'prod')).toBe(true);
    const preRows = () => fixture.db.query("SELECT diagnosis_enabled, recovery_enabled, config_json, version FROM ce_bot_diagnosis_recovery_config WHERE bot_id='default' AND env='pre'");
    const original = await preRows();
    const { items: [pre] } = await (await options('&q=default&env=pre')).json();
    const configure = (input: unknown) => request('/monitoring/configurations', 'member', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input),
    });
    expect((await configure({ botRef: pre.botRef, version: 1, enabled: false })).status).toBe(403);
    for (const enabled of [true, false]) {
      expect(await (await configure({ all: true, enabled })).json()).toEqual({ updated: 1, failed: [] });
      expect(await preRows()).toEqual(original);
    }
  });
});
