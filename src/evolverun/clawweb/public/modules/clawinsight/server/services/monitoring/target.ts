import { MonitoringError, type MonitoringTarget } from './contracts.js';
import { id } from './validation.js';

export function target(input: MonitoringTarget): MonitoringTarget {
  const valid = (s: unknown, max: number): s is string => typeof s === 'string'
    && s.trim() === s && s.length > 0 && [...s].length <= max && !/[\u0000-\u001f\u007f\ud800-\udfff]/u.test(s);
  if (!input || !valid(input.entityId, 128) || !valid(input.env, 20)) {
    throw new MonitoringError('TARGET_UNRESOLVED', 'Bot 目录身份或环境无效。');
  }
  return { botId: id(input.botId), entityId: input.entityId, env: input.env };
}
export const targetKey = (t: MonitoringTarget): string => JSON.stringify([t.botId, t.entityId, t.env]);
export const targetParams = (t: MonitoringTarget): string[] => [t.botId, t.entityId, t.env];
export const targetWhere = (prefix = ''): string => ['bot_id', 'entity_id', 'env'].map(c => `${prefix}${c} = ?`).join(' AND ');
