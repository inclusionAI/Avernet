import { createBotA2aFetch, type A2aProxyConnection } from '@/services/workspace/botA2aFetch';
import { buildRpcRequest, fetchCard, resolveA2aUrls, streamRpc } from '@avernet/a2a-chat-client';
import { makeEngineCard } from '../../adapters/a2a/helpers';
import * as capabilities from '@/capabilities';

const BASE = 'https://proxy.example/proxypass/ARCA_test@0:20003';
const CARD = `${BASE}/.well-known/agent-card.json`;
const RPC = `${BASE}/rpc`;
const connection = (overrides: Partial<A2aProxyConnection> = {}): A2aProxyConnection => ({
  a2aBaseUrl: BASE, a2aWithheld: false,
  a2aHeaders: { 'X-PROXYPASS-TOKEN': 'proxy-token' },
  expiresAt: new Date(Date.now() + 120_000).toISOString(), ...overrides,
});

function harness(initial = connection()) {
  const response = new Response('data: event\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
  const network = jest.fn().mockResolvedValue(response);
  const refresh = jest.fn().mockImplementation(async () => connection({
    a2aHeaders: { 'X-PROXYPASS-TOKEN': 'new-token' },
  }));
  return { network, refresh, response, run: createBotA2aFetch(initial, refresh, network)! };
}

describe('browser-direct A2A proxy transport', () => {
  afterEach(() => jest.restoreAllMocks());

  it('uses deployment login policy for validated card and RPC URLs only', async () => {
    const policy = jest.fn().mockReturnValue('include');
    jest.spyOn(capabilities, 'getCapabilities').mockReturnValue({
      ...capabilities.getCapabilities(), getA2aProxyCredentials: policy,
    });
    const h = harness();
    for (const url of [CARD, RPC]) await h.run(url, { credentials: 'omit' });
    expect(policy.mock.calls).toEqual([[CARD], [RPC]]);
    for (const [, opts] of h.network.mock.calls) {
      expect(opts).toMatchObject({ credentials: 'include', redirect: 'error' });
    }
    await expect(h.run('https://other.example/rpc')).rejects.toThrow('outside');
    expect(policy).toHaveBeenCalledTimes(2);
  });
  it('leaves existing direct A2A and WS credentials alone', () => {
    expect(createBotA2aFetch(connection({ a2aHeaders: undefined }), jest.fn())).toBeUndefined();
    expect(createBotA2aFetch(connection({ a2aHeaders: {} }), jest.fn())).toBeUndefined();
  });

  it('authenticates card GET without login cookies or redirects', async () => {
    const h = harness();
    await h.run(CARD, { headers: { Accept: 'application/json' } });
    const [url, opts] = h.network.mock.calls[0];
    expect(url).toBe(CARD);
    expect(opts.headers.get('X-PROXYPASS-TOKEN')).toBe('proxy-token');
    expect(opts.headers.get('Accept')).toBe('application/json');
    expect(opts).toMatchObject({ credentials: 'omit', redirect: 'error' });
    expect(h.refresh).not.toHaveBeenCalled();
  });

  it('feeds the real SDK card and SSE consumers through the signed proxy, never the card internal URL', async () => {
    const initial = connection();
    const h = harness(initial);
    const urls = resolveA2aUrls({ url: 'wss://ws.example/ws', a2aBaseUrl: BASE });
    const rpc = buildRpcRequest('SendStreamingMessage', {
      message: { messageId: 'fixture', contextId: 'fixture-session', parts: [{ text: 'fixture' }] },
    });
    const events = [
      { task: { id: 'fixture-task', contextId: 'fixture-session', metadata: {} } },
      { statusUpdate: { taskId: 'fixture-task', status: { state: 'completed' },
        metadata: { seq: 1, final: 'true' } } },
    ];
    const sse = events.map((result) => `data: ${JSON.stringify({ jsonrpc: '2.0', id: rpc.id, result })}\n\n`).join('');
    h.network.mockResolvedValueOnce(new Response(JSON.stringify(makeEngineCard())));
    h.network.mockResolvedValueOnce(new Response(new ReadableStream({
      start(controller) {
        const bytes = new TextEncoder().encode(sse);
        controller.enqueue(bytes.slice(0, 27));
        controller.enqueue(bytes.slice(27));
        controller.close();
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } }));

    expect((await fetchCard(urls.cardUrl, h.run)).card).toEqual(makeEngineCard());
    initial.expiresAt = '2000-01-01T00:00:00Z';
    const received = [];
    for await (const event of streamRpc(urls.rpcUrl, rpc, { iamToken: 'iam-token', fetchImpl: h.run })) {
      received.push(event);
    }
    expect(received.map((event) => event.type)).toEqual(['task', 'statusUpdate']);
    expect(received[1].metadata.final).toBe('true');
    expect(h.network.mock.calls.map(([url]) => url)).toEqual([CARD, RPC]);
    const [, options] = h.network.mock.calls[1];
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual(rpc);
    expect(options.headers.get('x-iam-token')).toBe('iam-token');
    expect(options.headers.get('X-PROXYPASS-TOKEN')).toBe('new-token');
    expect(h.refresh).toHaveBeenCalledTimes(1);
  });

  it('preserves IAM, extensions, body, signal and the unbuffered SSE response', async () => {
    const h = harness();
    const controller = new AbortController();
    const body = JSON.stringify({ method: 'SendStreamingMessage', params: { message: 'fixture only' } });
    const response = await h.run(RPC, { method: 'POST', body, signal: controller.signal,
      headers: { 'x-iam-token': 'iam-token', 'A2A-Extensions': 'extension', 'Content-Type': 'application/json' } });
    expect(response).toBe(h.response);
    const opts = h.network.mock.calls[0][1];
    expect(opts).toMatchObject({ method: 'POST', body, signal: controller.signal });
    expect(opts.headers.get('x-iam-token')).toBe('iam-token');
    expect(opts.headers.get('A2A-Extensions')).toBe('extension');
    expect(opts.headers.get('Content-Type')).toBe('application/json');
  });

  it('supports Request input, retaining its headers with init overrides', async () => {
    const h = harness();
    const request = new Request(RPC, { method: 'POST', headers: { 'x-iam-token': 'old' }, body: '{}' });
    await h.run(request, { headers: { 'x-iam-token': 'new' } });
    expect(h.network.mock.calls[0][0]).toBe(request);
    expect(h.network.mock.calls[0][1].headers.get('x-iam-token')).toBe('new');
  });

  it.each([
    'https://other.example/rpc', `${BASE}-other/rpc`, `${BASE}/rpc?redirect=other`,
    `${BASE}/../rpc`, `${BASE}/rpc#fragment`, 'http://127.0.0.1:20003/rpc',
  ])('never forwards credentials outside the issued paths: %s', async (url) => {
    const h = harness();
    await expect(h.run(url)).rejects.toThrow('outside');
    expect(h.network).not.toHaveBeenCalled();
    expect(h.refresh).not.toHaveBeenCalled();
  });

  it.each(['http://proxy.example', 'https://user:pass@proxy.example', 'https://proxy.example?q=x', 'https://proxy.example#x'])
  ('rejects unsafe signed bases: %s', async (base) => {
    const h = harness(connection({ a2aBaseUrl: base }));
    await expect(h.run(`${base}/rpc`)).rejects.toThrow();
    expect(h.network).not.toHaveBeenCalled();
  });

  it.each<Record<string, string>>([
    { Authorization: 'anything' }, { 'X-PROXYPASS-TOKEN': '' },
    { 'X-PROXYPASS-TOKEN': 'token\r\nCookie: secret' },
    { 'X-PROXYPASS-TOKEN': 'token', Cookie: 'secret' },
  ])('rejects invalid header material', async (a2aHeaders) => {
    const h = harness(connection({ a2aHeaders }));
    await expect(h.run(CARD)).rejects.toThrow('credential');
    expect(h.network).not.toHaveBeenCalled();
  });

  it('refreshes before expiry, deduplicates concurrent refresh and retains new token', async () => {
    const h = harness(connection({ expiresAt: new Date(Date.now() + 10_000).toISOString() }));
    await Promise.all([h.run(CARD), h.run(RPC, { method: 'POST' })]);
    await h.run(CARD);
    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(h.network).toHaveBeenCalledTimes(3);
    for (const [, opts] of h.network.mock.calls) expect(opts.headers.get('X-PROXYPASS-TOKEN')).toBe('new-token');
  });

  it.each([
    { a2aWithheld: true }, { a2aBaseUrl: `${BASE}-replacement` }, { a2aBaseUrl: null },
    { expiresAt: 'invalid' }, { expiresAt: '2000-01-01T00:00:00Z' }, { a2aHeaders: {} },
  ])('does not reuse credentials when refresh revokes or replaces the endpoint: %j', async (change) => {
    const h = harness(connection({ expiresAt: '' }));
    h.refresh.mockResolvedValue(connection(change));
    await expect(h.run(RPC, { method: 'POST' })).rejects.toThrow();
    expect(h.network).not.toHaveBeenCalled();
  });

  it('failed refresh never sends stale credentials; a later explicit request can refresh', async () => {
    const h = harness(connection({ expiresAt: '' }));
    h.refresh.mockRejectedValueOnce(new Error('connection unavailable'));
    await expect(h.run(RPC, { method: 'POST' })).rejects.toThrow('connection unavailable');
    expect(h.network).not.toHaveBeenCalled();
    await h.run(CARD);
    expect(h.refresh).toHaveBeenCalledTimes(2);
  });

  it('honors cancellation during refresh without later sending the aborted request', async () => {
    const h = harness(connection({ expiresAt: '' }));
    let resolve!: (value: A2aProxyConnection) => void;
    h.refresh.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const controller = new AbortController();
    const pending = h.run(RPC, { signal: controller.signal, method: 'POST' });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    resolve(connection());
    await Promise.resolve();
    expect(h.network).not.toHaveBeenCalled();
  });

  it('honors an already aborted request', async () => {
    const h = harness();
    const controller = new AbortController();
    controller.abort();
    await expect(h.run(CARD, { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(h.network).not.toHaveBeenCalled();
  });

  it('does not retry a POST on auth failure, redirect rejection or network failure', async () => {
    const h = harness();
    h.network.mockResolvedValueOnce(new Response('', { status: 401 }));
    expect((await h.run(RPC, { method: 'POST' })).status).toBe(401);
    h.network.mockRejectedValueOnce(new TypeError('redirect/network failure'));
    await expect(h.run(RPC, { method: 'POST' })).rejects.toThrow('redirect/network failure');
    expect(h.network).toHaveBeenCalledTimes(2);
    expect(h.refresh).not.toHaveBeenCalled();
  });
});
