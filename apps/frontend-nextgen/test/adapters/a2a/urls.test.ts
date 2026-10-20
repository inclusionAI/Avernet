import { a2aUrlsFromWsUrl } from '@/adapters/a2a/urls';

describe('a2aUrlsFromWsUrl（spec §2 地址映射）', () => {
  it('直连 v3 ws url 派生根级 A2A 面（丢弃 /api/{engine}/ws 路径）', () => {
    expect(a2aUrlsFromWsUrl('ws://localhost:20003/api/claude_code/ws'))
      .toEqual({
        cardUrl: 'http://localhost:20003/.well-known/agent-card.json',
        rpcUrl: 'http://localhost:20003/rpc',
      });
  });

  it('wss 派生 https，保留端口与主机', () => {
    const urls = a2aUrlsFromWsUrl('wss://lab.internal:20003/api/aicoding/ws');
    expect(urls.cardUrl).toBe('https://lab.internal:20003/.well-known/agent-card.json');
    expect(urls.rpcUrl).toBe('https://lab.internal:20003/rpc');
  });

  it('无效 URL 抛错（fail-fast，不静默回退）', () => {
    expect(() => a2aUrlsFromWsUrl('not-a-url')).toThrow();
  });

  it('非 ws scheme 拒绝（先归一后验证 scheme，不再静默产出不可达 cardUrl）', () => {
    expect(() => a2aUrlsFromWsUrl('http://localhost:20003/x')).toThrow();
  });

  it('大写 WS scheme 归一后可派生（new URL 先归一）', () => {
    const urls = a2aUrlsFromWsUrl('WS://localhost:20003/api/claude_code/ws');
    expect(urls.cardUrl).toBe('http://localhost:20003/.well-known/agent-card.json');
  });
});