// Minimal debug logger gated by the CLAUDE_CODE_GATEWAY_DEBUG env var.
//
//   CLAUDE_CODE_GATEWAY_DEBUG=1          -> enable all namespaces
//   CLAUDE_CODE_GATEWAY_DEBUG=server,sdk -> enable only listed namespaces
//   CLAUDE_CODE_GATEWAY_DEBUG=           -> disabled (default)
//
// Falls back to TEAMCLAW_AICODING_RELAY_DEBUG and AIX_DEBUG for compatibility
// with existing local tooling.
//
// Output format: `[claude-code-gateway:<namespace>] <message> <json-context?>`
// Kept dependency-free so it works in both ESM and CJS builds.

const raw = String(
  process.env.CLAUDE_CODE_GATEWAY_DEBUG
    ?? process.env.TEAMCLAW_AICODING_RELAY_DEBUG
    ?? process.env.AIX_DEBUG
    ?? '',
).trim();
const enabledAll = raw === '1' || raw.toLowerCase() === 'true' || raw === '*';
const enabledSet = new Set(
  raw && !enabledAll ? raw.split(',').map(s => s.trim()).filter(Boolean) : [],
);

export function isDebugEnabled(ns: string): boolean {
  if (enabledAll) return true;
  return enabledSet.has(ns);
}

export function createLogger(ns: string) {
  const prefix = `[claude-code-gateway:${ns}]`;
  const log = (level: 'log' | 'warn' | 'error', msg: string, ctx?: unknown) => {
    // if (!isDebugEnabled(ns) && level === 'log') return;
    const line = ctx === undefined ? `${prefix} ${msg}` : `${prefix} ${msg} ${safeJson(ctx)}`;
    // warn/error always print; log is gated by namespace
    // eslint-disable-next-line no-console
    (level === 'log' ? console.log : level === 'warn' ? console.warn : console.error)(line);
  };
  return {
    enabled: () => isDebugEnabled(ns),
    debug: (msg: string, ctx?: unknown) => log('log', msg, ctx),
    warn: (msg: string, ctx?: unknown) => log('warn', msg, ctx),
    error: (msg: string, ctx?: unknown) => log('error', msg, ctx),
  };
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, (_, v) => {
      if (typeof v === 'string' && v.length > 500) return `${v.slice(0, 500)}…<+${v.length - 500}>`;
      if (v instanceof Error) return { name: v.name, message: v.message };
      return v;
    });
  } catch {
    return '[unserializable]';
  }
}
