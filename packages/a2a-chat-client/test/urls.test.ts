import { a2aUrlsFromBaseUrl, a2aUrlsFromWsUrl } from '../src/index';

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
describe('a2aUrlsFromBaseUrl（B17 服务端签发 base → A2A 面）', () => {
  it('裸 origin base 派生 card/rpc（尾部斜杠剥除）', () => {
    expect(a2aUrlsFromBaseUrl('http://arca-x-svc.stable.alipay.net:32196'))
      .toEqual({
        cardUrl: 'http://arca-x-svc.stable.alipay.net:32196/.well-known/agent-card.json',
        rpcUrl: 'http://arca-x-svc.stable.alipay.net:32196/rpc',
      });
    expect(a2aUrlsFromBaseUrl('http://host:1/')).toEqual({
      cardUrl: 'http://host:1/.well-known/agent-card.json',
      rpcUrl: 'http://host:1/rpc',
    });
  });

  it('带路径前缀的 base 照单全收（两路径挂其下，不否决未来拓扑）', () => {
    const urls = a2aUrlsFromBaseUrl('http://gw.example/prefix');
    expect(urls.cardUrl).toBe('http://gw.example/prefix/.well-known/agent-card.json');
    expect(urls.rpcUrl).toBe('http://gw.example/prefix/rpc');
  });

  it('https base 派生 https 面', () => {
    const urls = a2aUrlsFromBaseUrl('https://bot.example:20003');
    expect(urls.rpcUrl).toBe('https://bot.example:20003/rpc');
  });

  it('非 http(s) scheme 拒绝（ws/畸形 base fail-fast，不静默产出坏 url）', () => {
    expect(() => a2aUrlsFromBaseUrl('ws://host:1')).toThrow();
    expect(() => a2aUrlsFromBaseUrl('not-a-url')).toThrow();
  });

  it('query/fragment 拒绝（追加路径会拼出结构性坏 url）', () => {
    expect(() => a2aUrlsFromBaseUrl('http://host:1?x=1')).toThrow();
    expect(() => a2aUrlsFromBaseUrl('http://host:1#f')).toThrow();
  });
});
