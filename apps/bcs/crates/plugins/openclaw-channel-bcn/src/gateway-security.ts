function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function parseGatewayPort(value: unknown): number | undefined {
  if (typeof value === 'number') {
    return Number.isInteger(value) && value > 0 && value <= 65535 ? value : undefined;
  }

  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return undefined;

  const port = Number(trimmed);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : undefined;
}

export function resolveGatewayPort(cfg: Record<string, unknown>): number {
  return parseGatewayPort(process.env.OPENCLAW_GATEWAY_PORT)
    ?? parseGatewayPort(asRecord(cfg.gateway)?.port)
    ?? 18789;
}

export function scopesForGatewayMethod(method: string): string[] {
  if (method === 'chat.history' || method === 'sessions.list') return [ 'operator.read' ];
  if (method === 'chat.inject' || method === 'sessions.delete') return [ 'operator.admin' ];
  return [];
}
