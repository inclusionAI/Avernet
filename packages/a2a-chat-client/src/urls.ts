/**
 * A2A 面地址派生（spec stage2 §2）。
 *
 * 直连形态：v3 `ws://host:port/api/{engine}/ws` → A2A 根级路径
 * （引擎 a2a router 无前缀挂载，@app.py:293）。
 * 企业代理链前缀规则 M4 定稿——本模块不含代理链翻译。
 * 前端永不消费 card 自报 url（引擎本机视角，非浏览器可达承诺）。
 *
 * B17：直连推导是推导不出时的兜底；连接响应 sockets 里 kind='a2a' 的
 * 服务端签发 base（`a2aUrlsFromBaseUrl`）才是权威来源——它由知道部署拓扑
 * 的层（BaaS local_k8s 沙箱插件）拼出浏览器真正可达的地址形态。
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

/**
 * 服务端签发的 A2A base → card/rpc 地址。
 *
 * base 是裸 HTTP(S) origin（无路径尾巴、无凭据）；仅校验协议形态并剥掉
 * 尾部斜杠，不做其他改写——「浏览器可达」是签发方的承诺，改写 url 即否决
 * 它。非法形态（非 http/https、不可解析）抛 TypeError，与
 * `a2aUrlsFromWsUrl` 的失败口径一致：形状不是本模块能修的，上浮给调用方
 * 当「本面不可用」处理。
 */
export function a2aUrlsFromBaseUrl(baseUrl: string): A2aUrls {
  const parsed = new URL(baseUrl);
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new TypeError(`not an a2a base url: ${baseUrl}`);
  }
  // query/fragment 会让「直接追加路径」变成坏 url（签发方违约）；路径尾巴
  // 则照单全收——未来某拓扑的带前缀 base 依然按「两路径挂其下」拼出。
  if (parsed.search || parsed.hash) {
    throw new TypeError(`a2a base url must carry no query or fragment: ${baseUrl}`);
  }
  const base = baseUrl.replace(/\/+$/, '');
  return { cardUrl: `${base}${CARD_PATH}`, rpcUrl: `${base}${RPC_PATH}` };
}