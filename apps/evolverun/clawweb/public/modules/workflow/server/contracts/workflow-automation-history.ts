import type { AutomationRecordQuery } from './workflow-automation.js';
export class InvalidAutomationRecordQuery extends Error {
  constructor() { super('执行记录筛选或分页参数无效'); }
}
export function automationRecordQuery(value: Record<string, unknown>): AutomationRecordQuery {
  const period = value.period ?? 'all', status = value.status ?? 'all';
  const limit = value.limit === undefined ? 8 : typeof value.limit === 'string' && /^\d+$/.test(value.limit) ? Number(value.limit) : value.limit;
  if (!['all', 'today', '7d'].includes(period as string)
    || !['all', 'pending', 'dispatching', 'waiting', 'retryable', 'uncertain', 'done', 'blocked', 'attention'].includes(status as string)
    || value.kind !== undefined && !['all', 'analysis', 'optimization', 'notification'].includes(value.kind as string)
    || typeof limit !== 'number' || !Number.isSafeInteger(limit) || limit < 1 || limit > 20
    || value.cursor !== undefined && (typeof value.cursor !== 'string' || !value.cursor || value.cursor.length > 512)) throw new InvalidAutomationRecordQuery();
  return { period: period as AutomationRecordQuery['period'], status: status as AutomationRecordQuery['status'], limit,
    ...(value.kind !== undefined ? { kind: value.kind as AutomationRecordQuery['kind'] } : {}),
    ...(value.cursor !== undefined ? { cursor: value.cursor as string } : {}) };
}
