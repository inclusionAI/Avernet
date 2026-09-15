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

/** SQLite exposes one connection; serialize Skill event units of work on that connection.
 * MySQL/ZDAS callers acquire row locks inside the transaction instead. */
export async function skillAuditTransaction<T>(db: IDatabase, work: (tx: IDatabase) => Promise<T>): Promise<T> {
  if (db.dbType !== 'sqlite') return db.transaction(work);
  const previous = sqliteTransactions.get(db) ?? Promise.resolve();
  const pending = previous.catch(() => {}).then(() => db.transaction(work));
  sqliteTransactions.set(db, pending);
  try { return await pending; }
  finally { if (sqliteTransactions.get(db) === pending) sqliteTransactions.delete(db); }
}

export type SkillEventType = 'registered' | 'diagnosis' | 'optimization';
export type SkillEventStatus = 'running' | 'waiting_user_input' | 'waiting_acceptance' | 'completed' | 'failed' | 'canceled';

export type SkillEventRow = {
  id: number; event_id: string; business_key: string; asset_id: string; owner_user_id: string;
  bot_id: string; ocb_skill_id: string; display_name: string; description: string | null;
  task_id: string | null; event_type: SkillEventType; status: SkillEventStatus; outcome: string | null;
  actor_id: string | null; actor_type: 'user' | 'system';
  version_from_id: string | null; version_from_no: number | null;
  version_to_id: string | null; version_to_no: number | null;
  waiting_interaction_id: string | null; summary: string | null; detail_json: string | null;
  started_at: number | string; completed_at: number | string | null;
  gmt_create: number | string; gmt_modified: number | string;
};

export type SkillTaskEventUpdate = {
  status?: SkillEventStatus;
  outcome?: string | null;
  actorId?: string;
  actorType?: 'user' | 'system';
  waitingInteractionId?: string | null;
  summary?: string | null;
  detail?: Record<string, unknown>;
  versionToId?: string | null;
  versionToNo?: number | null;
};

function eventId(key: string): string {
  return createHash('sha256').update(key).digest('hex');
}

function taskEventStatus(value: string): SkillEventStatus {
  if (value === 'waiting_acceptance') return value;
  if (value === 'completed' || value === 'failed' || value === 'canceled') return value;
  if (value === 'cancelled') return 'canceled';
  return 'running';
}

function jsonObject(value: string | null): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value ?? '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch { return {}; }
}

async function writeEvent(tx: IDatabase, input: {
  businessKey: string; asset: SkillAssetRow; taskId?: string | null; type: SkillEventType;
  status: SkillEventStatus; outcome?: string | null; actorId?: string | null; actorType: 'user' | 'system';
  versionFromId?: string | null; versionFromNo?: number | null;
  versionToId?: string | null; versionToNo?: number | null;
  waitingInteractionId?: string | null; summary?: string | null; detail?: Record<string, unknown>;
}): Promise<void> {
  const existing = (await tx.query<SkillEventRow>('SELECT * FROM ce_skill_events WHERE business_key = ?', [input.businessKey]))[0];
  const now = tx.dialect.now();
  const terminal = ['completed', 'failed', 'canceled'].includes(input.status);
  if (!existing) {
    await tx.exec(`INSERT INTO ce_skill_events
      (event_id, business_key, asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, description,
       task_id, event_type, status, outcome, actor_id, actor_type, version_from_id, version_from_no,
       version_to_id, version_to_no, waiting_interaction_id, summary, detail_json,
       started_at, completed_at, gmt_create, gmt_modified)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, [
      eventId(input.businessKey), input.businessKey, input.asset.asset_id, input.asset.owner_user_id,
      input.asset.bot_id, input.asset.ocb_skill_id, input.asset.display_name, input.asset.description,
      input.taskId ?? null, input.type, input.status, input.outcome ?? null, input.actorId ?? null, input.actorType,
      input.versionFromId ?? null, input.versionFromNo ?? null, input.versionToId ?? null, input.versionToNo ?? null,
      input.waitingInteractionId ?? null, input.summary ?? null,
      input.detail && Object.keys(input.detail).length ? JSON.stringify(input.detail) : null,
      now, terminal ? now : null, now, now,
    ]);
    return;
  }
  const mergedDetail = { ...jsonObject(existing.detail_json), ...(input.detail ?? {}) };
  await tx.exec(`UPDATE ce_skill_events SET
    status = ?, outcome = ?, actor_id = COALESCE(?, actor_id), actor_type = ?,
    version_from_id = COALESCE(version_from_id, ?), version_from_no = COALESCE(version_from_no, ?),
    version_to_id = ?, version_to_no = ?, waiting_interaction_id = ?, summary = ?, detail_json = ?,
    completed_at = ?, gmt_modified = ? WHERE business_key = ?`, [
    input.status, input.outcome ?? null, input.actorId ?? null, input.actorType,
    input.versionFromId ?? null, input.versionFromNo ?? null,
    input.versionToId ?? existing.version_to_id, input.versionToNo ?? existing.version_to_no,
    input.waitingInteractionId ?? null, input.summary ?? existing.summary,
    Object.keys(mergedDetail).length ? JSON.stringify(mergedDetail) : null,
    terminal ? now : null, now, input.businessKey,
  ]);
}

export async function recordSkillRegistration(tx: IDatabase, input: {
  assetId: string; versionId: string; versionNo: number; actorId: string;
}): Promise<void> {
  const asset = (await tx.query<SkillAssetRow>('SELECT * FROM ce_skill_assets WHERE asset_id = ?', [input.assetId]))[0];
  if (!asset) throw new Error('Cannot record an unknown Skill asset');
  await writeEvent(tx, {
    businessKey: `register:${input.assetId}`, asset, type: 'registered', status: 'completed', outcome: 'registered',
    actorId: input.actorId, actorType: 'user', versionFromId: input.versionId, versionFromNo: input.versionNo,
    summary: '登记 Skill',
  });
}

/** Update the single business event owned by a top-level Skill task. */
export async function recordSkillTaskEvent(tx: IDatabase, taskId: string, update: SkillTaskEventUpdate = {}): Promise<void> {
  const task = (await tx.query<{ task_type: string; task_name: string | null; status: string; config_json: string;
    created_by: string; user_id: string; bot_id: string; error_message: string | null }>(
    'SELECT task_type, task_name, status, config_json, created_by, user_id, bot_id, error_message FROM ce_tasks WHERE task_id = ?', [taskId]))[0];
  if (!task || !['diagnose', 'full'].includes(task.task_type)) return;
  const config = jsonObject(task.config_json) as { targetSkill?: {
    assetId?: string; baseline?: { versionId?: string; versionNo?: number };
  }; skillAuditTestBench?: unknown };
  if (!config.targetSkill?.assetId) return;
  const asset = (await tx.query<SkillAssetRow>('SELECT * FROM ce_skill_assets WHERE asset_id = ?', [config.targetSkill.assetId]))[0];
  if (!asset || asset.owner_user_id !== task.user_id || asset.bot_id !== task.bot_id) {
    throw new Error('Skill event target does not belong to this task');
  }
  const existing = (await tx.query<SkillEventRow>('SELECT * FROM ce_skill_events WHERE business_key = ?', [taskId]))[0];
  const { testBench: _ignored, ...safeDetail } = update.detail ?? {};
  const testBench = skillTestBenchSnapshot(config.skillAuditTestBench, taskId);
  await writeEvent(tx, {
    businessKey: taskId, asset, taskId,
    type: task.task_type === 'diagnose' ? 'diagnosis' : 'optimization',
    status: update.status ?? taskEventStatus(task.status),
    outcome: Object.prototype.hasOwnProperty.call(update, 'outcome') ? update.outcome : existing?.outcome,
    actorId: update.actorId ?? (existing ? null : task.created_by),
    actorType: update.actorType ?? existing?.actor_type ?? 'user',
    versionFromId: typeof config.targetSkill.baseline?.versionId === 'string' ? config.targetSkill.baseline.versionId : null,
    versionFromNo: Number.isSafeInteger(config.targetSkill.baseline?.versionNo) ? config.targetSkill.baseline!.versionNo! : null,
    versionToId: Object.prototype.hasOwnProperty.call(update, 'versionToId') ? update.versionToId : existing?.version_to_id,
    versionToNo: Object.prototype.hasOwnProperty.call(update, 'versionToNo') ? update.versionToNo : existing?.version_to_no,
    waitingInteractionId: Object.prototype.hasOwnProperty.call(update, 'waitingInteractionId')
      ? update.waitingInteractionId : existing?.waiting_interaction_id,
    summary: update.summary ?? task.error_message ?? task.task_name,
    detail: Object.keys(safeDetail).length || testBench
      ? { ...safeDetail, ...(testBench ? { testBench } : {}) } : undefined,
  });
}
