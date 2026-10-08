/** Versioned, untrusted locators. These are never credentials: directory ACL is checked on every use. */
import { createHash } from 'node:crypto';
import { MonitoringError, type MonitoringTarget } from './contracts.js';
import type { MonitoringReferences } from './directory-contracts.js';
import { target } from './target.js';

export function createMonitoringReferences(now: () => number = Date.now): MonitoringReferences {
  const contextHash = (context: string) => createHash('sha256').update(context).digest('base64url');
  const pack = (payload: unknown) => Buffer.from(JSON.stringify(payload)).toString('base64url');
  const unpack = (token: string): Record<string, unknown> => {
    if (typeof token !== 'string' || token.length > 4096 || !/^[\w-]+$/.test(token)) throw new Error('Invalid locator');
    const bytes = Buffer.from(token, 'base64url');
    if (bytes.toString('base64url') !== token) throw new Error('Non-canonical locator');
    const payload: unknown = JSON.parse(bytes.toString('utf8'));
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Invalid payload');
    return payload as Record<string, unknown>;
  };
  return {
    encode: (t, tenant) => pack({ v: 1, kind: 'target', tenant, ...target(t) }),
    decode(ref, tenant) {
      try {
        const data = unpack(ref);
        if (data.v !== 1 || data.kind !== 'target' || data.tenant !== tenant) throw new Error('Invalid scope');
        return target(data as MonitoringTarget);
      } catch { throw new MonitoringError('BOT_NOT_FOUND', 'Bot 不存在或无权访问。'); }
    },
    cursor: (after, context) => pack({ v: 1, kind: 'cursor', after, context: contextHash(context), expires: now() + 15 * 60_000 }),
    readCursor(cursor, context) {
      try {
        const data = unpack(cursor);
        if (data.v !== 1 || data.kind !== 'cursor' || data.context !== contextHash(context) || typeof data.expires !== 'number'
          || !Number.isSafeInteger(data.expires) || data.expires <= now() || data.expires > now() + 15 * 60_000 || typeof data.after !== 'string' || !/^\d+$/.test(data.after)) throw new Error('Invalid cursor');
        return data.after;
      } catch { throw new MonitoringError('INVALID_EVENT', '分页游标已失效，请重新搜索。'); }
    },
  };
}
