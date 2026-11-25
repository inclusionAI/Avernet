/** 金样对齐 engine/adapter/.../a2a/card.py 的输出形状（P0 落地后含 resolve）。 */
export const EXT_BASE = 'https://avernet.local/a2a/ext';

export function makeEngineCard(opts: {
  binding?: string; streaming?: boolean; resolve?: boolean;
} = {}): any {
  const base = {
    streaming: opts.streaming ?? true,
    pushNotifications: false,
    extensions: ['inject', 'context', 'replay', 'retry', 'cancel'].map(
      (k) => ({ url: `${EXT_BASE}/${k}/v1`, required: false }),
    ),
  };
  if (opts.resolve !== false) {
    base.extensions.push({ url: `${EXT_BASE}/resolve/v1`, required: false });
  }
  return {
    supportedInterfaces: [{
      url: 'http://127.0.0.1:20003/rpc',
      protocolBinding: opts.binding ?? 'JSONRPC',
      protocolVersion: '1.0',
    }],
    capabilities: base,
  };
}