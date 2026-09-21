import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { MonitoringSummary, MonitoringTarget, MonitoringWindow } from '../services/monitoring/contracts.js';
import { targetKey, targetParams, targetWhere } from '../services/monitoring/target.js';
import { CHECKS_TABLE, DIAGNOSES_TABLE } from './monitoring-schema-check.js';

/** One batch and one snapshot; diagnoses without a check are deliberately not enrollment. */
export async function readMonitoringSummaries(db: IDatabase, targets: readonly MonitoringTarget[], window: MonitoringWindow): Promise<(MonitoringSummary | null)[]> {
  if (!targets.length) return [];
  if (targets.length > 50) throw new Error('Summary batch exceeds 50 targets');
  const predicate = targets.map(() => `(${targetWhere()})`).join(' OR ');
  const params: unknown[] = targets.flatMap(targetParams);
  const time: string[] = [];
  if (window.startMs !== null) { time.push('occurred_at_ms >= ?'); params.push(window.startMs); }
  if (window.endMs !== null) { time.push('occurred_at_ms < ?'); params.push(window.endMs); }
  const key = `CASE WHEN session_id IS NOT NULL AND session_id <> '' THEN ${db.dbType === 'sqlite'
    ? "engine || ':id:' || session_id" : "CAST(CONCAT(engine, ':id:', session_id) AS BINARY)"}
    WHEN session_key IS NOT NULL AND session_key <> '' THEN ${db.dbType === 'sqlite'
    ? "engine || ':key:' || session_key" : "CAST(CONCAT(engine, ':key:', session_key) AS BINARY)"} ELSE NULL END`;
  const rows = await db.query(`SELECT c.*, d.diagnosis_count, d.alert_count, d.session_count, d.missing_count
    FROM ${CHECKS_TABLE} c LEFT JOIN (
      SELECT bot_id, entity_id, env, COUNT(*) AS diagnosis_count,
        SUM(CASE WHEN decision = 'ALERT' THEN 1 ELSE 0 END) AS alert_count,
        COUNT(DISTINCT ${key}) AS session_count,
        SUM(CASE WHEN (${key}) IS NULL THEN 1 ELSE 0 END) AS missing_count
      FROM ${DIAGNOSES_TABLE} WHERE (${predicate})${time.length ? ` AND ${time.join(' AND ')}` : ''}
      GROUP BY bot_id, entity_id, env
    ) d ON c.bot_id = d.bot_id AND c.entity_id = d.entity_id AND c.env = d.env
    WHERE ${targets.map(() => `(${targetWhere('c.')})`).join(' OR ')}`, [...params, ...targets.flatMap(targetParams)]);
  const byTarget = new Map<string, MonitoringSummary>();
  const number = (value: unknown) => {
    const n = Number(value ?? 0);
    if (!Number.isSafeInteger(n) || n < 0) throw new Error('Invalid stored count');
    return n;
  };
  for (const row of rows) {
    if (!['HEALTHY', 'ERROR', 'UNKNOWN', 'PAUSED'].includes(String(row.status))) throw new Error('Invalid check status');
    const count = number(row.diagnosis_count), missing = number(row.missing_count);
    byTarget.set(targetKey({ botId: String(row.bot_id), entityId: String(row.entity_id), env: String(row.env) }), {
      status: row.status as MonitoringSummary['status'], checkedAt: new Date(number(row.checked_at_ms)).toISOString(),
      lastSuccessfulCheckAt: row.last_successful_check_at_ms == null ? null : new Date(number(row.last_successful_check_at_ms)).toISOString(),
      diagnosisCount: count, alertCount: number(row.alert_count),
      diagnosedSessionCount: count > 0 && missing === count ? null : number(row.session_count),
      unidentifiedSessionDiagnosisCount: missing,
    });
  }
  return targets.map(t => byTarget.get(targetKey(t)) ?? null);
}
