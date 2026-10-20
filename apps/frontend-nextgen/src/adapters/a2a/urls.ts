/**
 * A2A 面地址派生（spec stage2 §2）。
 *
 * 直连形态：v3 `ws://host:port/api/{engine}/ws` → A2A 根级路径
 * （引擎 a2a router 无前缀挂载，@app.py:293）。
 * 企业代理链前缀规则 M4 定稿——本模块不含代理链翻译。
 * 前端永不消费 card 自报 url（引擎本机视角，非浏览器可达承诺）。
 */
export interface A2aUrls { cardUrl: string; rpcUrl: string }

const CARD_PATH = '/.well-known/agent-card.json';
const RPC_PATH = '/rpc';

export function a2aUrlsFromWsUrl(wsUrl: string): A2aUrls {
  const parsed = new URL(wsUrl);                       // 先归一（大小写 scheme 网络 URL 都接受）
  if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
    throw new TypeError(`not a ws url: ${wsUrl}`);
  }
  const base = (parsed.protocol === 'wss:' ? 'https://' : 'http://') + parsed.host;
  return { cardUrl: `${base}${CARD_PATH}`, rpcUrl: `${base}${RPC_PATH}` };
}