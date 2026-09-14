import { createHash } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import type { SkillAssetRow } from './skill-asset-repository.js';

const sqliteTransactions = new WeakMap<IDatabase, Promise<unknown>>();

export type SkillTestBenchSnapshot = {
  taskId: string; stepId: string; round: number;
  scoreComparison: { name: string | null; baseline: number | null; candidate: number | null; delta: number | null } | null;
};

/** Public whitelist, shared by persistence and the read projection. Never expose
 * arbitrary report/config/detail fields, and never infer a missing score. */
export function skillTestBenchSnapshot(value: unknown, taskId: string | null): SkillTestBenchSnapshot | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (!taskId || item.taskId !== taskId || typeof item.stepId !== 'string' || !item.stepId
    || !Number.isSafeInteger(item.round) || Number(item.round) < 1) return null;
  const raw = item.scoreComparison;
  if (raw == null) return { taskId, stepId: item.stepId, round: Number(item.round), scoreComparison: null };
  if (typeof raw !== 'object' || Array.isArray(raw)) return null;
  const comparison = raw as Record<string, unknown>;
  const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null;
  return { taskId, stepId: item.stepId, round: Number(item.round), scoreComparison: {
    name: typeof comparison.name === 'string' ? comparison.name : null,
    baseline: number(comparison.baseline), candidate: number(comparison.candidate), delta: number(comparison.delta),
  } };
}

export function skillEventTestBench(detailJson: string | null, taskId: string | null): SkillTestBenchSnapshot | null {
  try { return skillTestBenchSnapshot(JSON.parse(detailJson ?? '{}')?.testBench, taskId); }
  catch { return null; }
}

/** SQLite exposes one connection; serialize audit units of work on that connection.
 * MySQL/ZDAS callers acquire row locks inside the transaction instead. */
export async function skillAuditTransaction<T>(db: IDatabase, work: (tx: IDatabase) => Promise<T>): Promise<T> {
  if (db.dbType !== 'sqlite') return db.transaction(work);
  const previous = sqliteTransactions.get(db) ?? Promise.resolve();
  const pending = previous.catch(() => {}).then(() => db.transaction(work));
  sqliteTransactions.set(db, pending);
  try { return await pending; }
  finally { if (sqliteTransactions.get(db) === pending) sqliteTransactions.delete(db); }
}

export type SkillAuditInput = {
  key: string;
  assetId: string;
  taskId?: string;
  versionId?: string;
  versionNo?: number;
  type: 'registered' | 'evolution_started' | 'evolution_finished' | 'candidate_accepted' | 'candidate_rejected' | 'version_applied' | 'version_apply_failed';
  actorId?: string;
  actorType: 'user' | 'system';
  result: string;
  detail?: Record<string, unknown>;
};

export type SkillAuditRow = {
  event_id: string; asset_id: string; owner_user_id: string; bot_id: string; ocb_skill_id: string;
  display_name: string; description: string | null; task_id: string | null;
  version_id: string | null; version_no: number | null; event_type: SkillAuditInput['type'];
  actor_id: string | null; actor_type: 'user' | 'system'; result: string; detail_json: string | null;
  gmt_create: number | string;
};

/** Invoke inside the business write's transaction. The read API never reconstructs history. */
export async function appendSkillAudit(tx: IDatabase, input: SkillAuditInput): Promise<void> {
  const key = createHash('sha256').update(input.key).digest('hex');
  if ((await tx.query('SELECT event_id FROM ce_skill_audit_events WHERE idempotency_key = ?', [key])).length) return;
  const asset = (await tx.query<SkillAssetRow>('SELECT * FROM ce_skill_assets WHERE asset_id = ?', [input.assetId]))[0];
  if (!asset) throw new Error('Cannot audit an unknown Skill asset');
  const conflict = tx.dbType === 'mysql' || tx.dbType === 'zdas'
    ? 'ON DUPLICATE KEY UPDATE idempotency_key = idempotency_key' : 'ON CONFLICT (idempotency_key) DO NOTHING';
  await tx.exec(`INSERT INTO ce_skill_audit_events
    (event_id, idempotency_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
     task_id, version_id, version_no, event_type, actor_id, actor_type, result, detail_json, gmt_create)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ${conflict}`,
  [key, key, asset.asset_id, asset.owner_user_id, asset.bot_id, asset.ocb_skill_id, asset.display_name, asset.description,
    input.taskId ?? null, input.versionId ?? null, input.versionNo ?? null, input.type, input.actorId ?? null,
    input.actorType, input.result, input.detail ? JSON.stringify(input.detail) : null, tx.dialect.now()]);
}

/** Called by a task mutation, never by a GET or a historical reconciliation scan. */
export async function auditTaskOperation(tx: IDatabase, taskId: string, input: Omit<SkillAuditInput, 'assetId' | 'taskId' | 'key'> & { key?: string }): Promise<void> {
  const task = (await tx.query<{ task_type: string; config_json: string; created_by: string; user_id: string; bot_id: string }>(
    'SELECT task_type, config_json, created_by, user_id, bot_id FROM ce_tasks WHERE task_id = ?', [taskId]))[0];
  if (!task || task.task_type === 'stage_test') return;
  const config = JSON.parse(task.config_json) as { targetSkill?: { assetId?: string }; skillAuditTestBench?: unknown };
  if (!config.targetSkill?.assetId) return;
  const asset = (await tx.query<SkillAssetRow>('SELECT * FROM ce_skill_assets WHERE asset_id = ?', [config.targetSkill.assetId]))[0];
  if (!asset || asset.owner_user_id !== task.user_id || asset.bot_id !== task.bot_id) throw new Error('Skill audit target does not belong to this task');
  const run = (await tx.query<{ event_id: string }>(
    "SELECT event_id FROM ce_skill_audit_events WHERE task_id = ? AND event_type = 'evolution_started' ORDER BY id DESC LIMIT 1", [taskId]))[0];
  const testBench = input.type === 'evolution_started' || input.type === 'registered'
    ? null : skillTestBenchSnapshot(config.skillAuditTestBench, taskId);
  const { testBench: _untrustedAssociation, ...detail } = input.detail ?? {};
  await appendSkillAudit(tx, { ...input, assetId: config.targetSkill.assetId, taskId,
    detail: Object.keys(detail).length || testBench ? { ...detail, ...(testBench ? { testBench } : {}) } : undefined,
    key: input.key ?? `${taskId}:${run?.event_id ?? 'current-operation'}:${input.type}`,
    actorId: input.actorType === 'user' ? input.actorId ?? task.created_by : undefined });
}
