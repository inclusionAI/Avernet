import { describe, expect, it, vi } from 'vitest';
const fetchJson = vi.hoisted(() => vi.fn());
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson }));
import { monitoringApi, monitoringErrorText } from '../monitoring';

describe('monitoring read API', () => {
  it('encodes bot and query using the shared same-origin GET client, without reporting credentials', () => {
    const signal = new AbortController().signal;
    monitoringApi.diagnoses('bot/a+b', { startDate: '2026-09-09', endDate: '', keyword: 'x & y', decision: 'ALL', page: 2, pageSize: 10 }, signal);
    const [url, init] = fetchJson.mock.lastCall!;
    expect(url).toContain('/bots/bot%2Fa%2Bb/diagnoses?');
    const params = new URL(url, 'http://localhost').searchParams;
    expect(params.get('keyword')).toBe('x & y');
    expect(params.get('page')).toBe('2');
    expect(params.has('endDate')).toBe(false);
    expect(init).toEqual({ signal });
    monitoringApi.status('bot/a', signal);
    expect(fetchJson.mock.lastCall).toEqual(['/api/insight/v1/monitoring/bots/bot%2Fa/status', { signal }]);
    monitoringApi.bots(signal);
    expect(fetchJson.mock.lastCall).toEqual(['/api/insight/v1/monitoring/bots', { signal }]);
  });
  it('does not display server details or credentials in error text', () => {
    expect(monitoringErrorText({ status: 503, message: 'private-secret' })).not.toContain('private-secret');
    expect(monitoringErrorText({ status: 401 })).toContain('登录');
    expect(monitoringErrorText({ name: 'TimeoutError' })).toContain('超时');
    expect(monitoringErrorText(new Error('private-secret'))).toContain('加载失败');
  });
  it('uses opaque target references and explicit Beijing windows for the new APIs', () => {
    const dates = { startDate: '2026-09-09', endDate: '2026-09-10' };
    const signal = new AbortController().signal;
    monitoringApi.options('mine', 'default & name', dates, 'signed-cursor', signal);
    let params = new URL(fetchJson.mock.lastCall![0], 'http://localhost').searchParams;
    expect(params.get('start')).toBe(String(Date.parse('2026-09-09T00:00:00+08:00')));
    expect(params.get('end')).toBe(String(Date.parse('2026-09-11T00:00:00+08:00')));
    expect(params.get('q')).toBe('default & name'); expect(params.get('cursor')).toBe('signed-cursor');
    expect(params.has('ownerId')).toBe(false);
    monitoringApi.targetDiagnoses('opaque/ref', { ...dates, decision: 'ALL', keyword: '', page: 18, pageSize: 50 }, signal);
    expect(fetchJson.mock.lastCall![0]).toContain('/targets/opaque%2Fref/diagnoses?');
    params = new URL(fetchJson.mock.lastCall![0], 'http://localhost').searchParams;
    expect(params.has('startDate')).toBe(false); expect(params.get('page')).toBe('18');
    monitoringApi.targetStatus('opaque', { startDate: '', endDate: '' }, signal);
    expect(fetchJson.mock.lastCall![0]).toContain('start=all&end=all');
    monitoringApi.enroll('opaque', signal);
    expect(fetchJson.mock.lastCall![1]).toMatchObject({ method: 'POST', body: '{"botRef":"opaque"}', signal });
  });

});
