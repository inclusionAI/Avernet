/** Local failure before POST: safe to display, unlike an unknown upstream outcome. */
export class RsiRequestKeyError extends Error {
  constructor() {
    super('无法生成安全请求标识，本次操作尚未发送。请检查浏览器环境后重试。');
    this.name = 'RsiRequestKeyError';
  }
}

/** 128-bit correlation key, not a credential. Works on HTTP without randomUUID. */
export function createRsiRequestKey(): string {
  try {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return `rsi-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  } catch {
    // Never silently substitute Math.random for a cryptographic random source.
    throw new RsiRequestKeyError();
  }
}
