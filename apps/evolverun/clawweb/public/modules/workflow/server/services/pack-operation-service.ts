/** ClawWeb's private save/publish journal. Formal deployment history contains releases only. */
import { createHash } from 'node:crypto';
import { parse as parseYaml } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { PackOperationRepository, type PackOperation } from '../repositories/pack-operation-repository.js';
import { normalizeWorkflowSpec, validateWorkflowSemantics } from '../workflow.js';
import { validateSpec } from '../validators/workflow-validator.js';
import { resumePreparedPackCandidate, type PreparedPackCandidate } from './pack-candidate-save.js';
import { resumePreparedDeployment, type PreparedDeployment } from './auto-heal-deploy-git.js';
import type { ClawFlowsGitConfig } from './clawflows-git-client.js';

export class PackOperationError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string,
    readonly branch?: string, readonly commit?: string) { super(code); this.name = 'PackOperationError'; }
}
export const operationHash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const remoteIdentity = (config: ClawFlowsGitConfig) => operationHash(config.remoteUrl);
export function requestIdentity(value: string | undefined, generate: () => string): string {
  if (value === undefined) return generate();
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(value)) throw new PackOperationError(400, 'INVALID_REQUEST');
  return value;
}
export type OperationPayload = {
  git: PreparedPackCandidate | PreparedDeployment;
  specJson: string;
  title: string;
  packId: string;
  remoteHash: string;
  version: number;
  note: string | null;
  /** A newer saved snapshot remains a draft even when the user deploys an older saved S. */
  preserveDraft: boolean;
  facadeCommands?: string[];
};
export function candidateProjection(workflowId: string, content: string, manifestContent: string) {
  let raw: unknown;
  try { raw = parseYaml(content); } catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
  const validation = validateSpec(raw);
  if (!validation.valid || !validation.normalizedSpec) throw new PackOperationError(400, 'INVALID_WORKFLOW');
  const spec = normalizeWorkflowSpec(validation.normalizedSpec);
  try { validateWorkflowSemantics(spec); } catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
  if (spec.id !== workflowId) throw new PackOperationError(400, 'WORKFLOW_ID_MISMATCH');
  const manifest = parseYaml(manifestContent) as { facades?: Array<{ command: string; aliases?: string[]; defaultWorkflow: string }> };
  let facadeCommands: string[] | undefined;
  if (manifest.facades !== undefined) {
    if (!Array.isArray(manifest.facades)) throw new PackOperationError(400, 'INVALID_MANIFEST');
    facadeCommands = [];
    for (const facade of manifest.facades) {
      if (!facade || typeof facade.defaultWorkflow !== 'string' || !Array.isArray(facade.aliases ?? [])) throw new PackOperationError(400, 'INVALID_MANIFEST');
      const commands = [facade.command, ...(facade.aliases ?? [])];
      if (commands.some(c => typeof c !== 'string' || !/^[a-z0-9][a-z0-9_-]{0,127}$/.test(c))) throw new PackOperationError(400, 'INVALID_MANIFEST');
      if (facade.defaultWorkflow === workflowId) facadeCommands.push(...commands);
    }
    if (new Set(facadeCommands).size !== facadeCommands.length) throw new PackOperationError(400, 'INVALID_MANIFEST');
  }
  return { specJson: JSON.stringify({ ...spec, content }), title: spec.title || workflowId, facadeCommands };
}
async function syncFacades(tx: IDatabase, op: PackOperation, payload: OperationPayload) {
  if (payload.facadeCommands === undefined) return;
  const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : '';
  for (const command of payload.facadeCommands) {
    const owners = await tx.query<{ workflow_id: string }>(`SELECT workflow_id FROM facade_bindings WHERE command = ?${lock}`, [command]);
    if (owners.some(owner => owner.workflow_id !== op.workflowId)) throw new PackOperationError(409, 'FACADE_CONFLICT', op.requestId);
  }
  await tx.exec('DELETE FROM facade_bindings WHERE workflow_id = ?', [op.workflowId]);
  for (const command of payload.facadeCommands) {
    const inserted = await tx.exec('INSERT INTO facade_bindings (command,workflow_id,pack_id,remark,gmt_create,gmt_modified) VALUES (?,?,?,NULL,?,?)',
      [command, op.workflowId, payload.packId, tx.dialect.now(), tx.dialect.now()]);
    if (inserted.affectedRows !== 1) throw new Error('Facade write failed');
  }
}

/** All business writes and the completed receipt commit together; no Git inside this transaction. */
export async function completePackOperation(db: IDatabase, op: PackOperation): Promise<Record<string, unknown>> {
  const payload = op.payloadJson ? JSON.parse(op.payloadJson) as OperationPayload : null;
  if (!payload?.git || !payload.specJson) throw new PackOperationError(409, 'OPERATION_NOT_PREPARED', op.requestId);
  const git = payload.git;
  const result: Record<string, unknown> = op.kind === 'save'
    ? { ok: true, saved: true, workflowId: op.workflowId, packId: payload.packId, requestId: op.requestId, branch: git.branch, commit: git.commit, version: payload.version }
    : { ok: true, deployed: true, workflowId: op.workflowId, packId: payload.packId, requestId: op.requestId,
      version: payload.version, deployNumber: (git as PreparedDeployment).deployNumber, tagName: (git as PreparedDeployment).tagName,
      commit: git.commit, candidateCommit: (git as PreparedDeployment).candidateCommit };
  const completed = await new PackOperationRepository(db).complete(op.requestId, JSON.stringify(result), async (tx, current) => {
    if (current.payloadJson !== op.payloadJson) throw new PackOperationError(409, 'OPERATION_TARGET_CHANGED', op.requestId);
    if (op.kind === 'save') {
      const update = await tx.exec('UPDATE workflow_specs SET spec_json = ?, pack_id = ?, title = ?, owner_id = ?, gmt_modified = ? WHERE workflow_id = ?',
        [payload.specJson, payload.packId, payload.title, op.actorId, tx.dialect.now(), op.workflowId]);
      if (update.affectedRows !== 1) throw new Error('Draft projection write failed');
    } else {
      const release = git as PreparedDeployment;
      // Do not change the public history/action contract or expose reservations to readers.
      const collisions = await tx.query('SELECT id FROM workflow_deploy_history WHERE workflow_id = ? AND (version = ? OR deploy_number = ?)',
        [op.workflowId, payload.version, release.deployNumber]);
      if (collisions.length) throw new PackOperationError(409, 'DEPLOYMENT_NUMBER_CONFLICT', op.requestId);
      const insertion = await tx.exec(`INSERT INTO workflow_deploy_history
        (pack_id,workflow_id,deploy_number,version,tag_name,action,from_deploy_number,spec_json,note,bot_id,owner_id,is_active,gmt_create,gmt_modified)
        VALUES (?,?,?,?,?,'deploy',NULL,?,?,?,?,0,?,?)`,
      [payload.packId, op.workflowId, release.deployNumber, payload.version, release.tagName, payload.specJson,
        payload.note, op.actorId, op.actorId, tx.dialect.now(), tx.dialect.now()]);
      if (insertion.affectedRows !== 1) throw new Error('Release history write failed');
      await tx.exec('UPDATE workflow_deploy_history SET is_active = 0 WHERE workflow_id = ? AND is_active = 1', [op.workflowId]);
      const remaining = await tx.query<{ n: number }>('SELECT COUNT(*) AS n FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [op.workflowId]);
      if (Number(remaining[0]?.n) !== 0) throw new Error('Release deactivation failed');
      const activation = await tx.exec("UPDATE workflow_deploy_history SET is_active = 1 WHERE workflow_id = ? AND pack_id = ? AND deploy_number = ? AND version = ? AND action = 'deploy'",
        [op.workflowId, payload.packId, release.deployNumber, payload.version]);
      if (activation.affectedRows !== 1) throw new Error('Release activation failed');
      const updated = payload.preserveDraft
        ? await tx.exec('UPDATE workflow_specs SET version = ?, gmt_modified = ? WHERE workflow_id = ?', [payload.version, tx.dialect.now(), op.workflowId])
        : await tx.exec('UPDATE workflow_specs SET version = ?,spec_json = ?,pack_id = ?,title = ?,gmt_modified = ? WHERE workflow_id = ?',
          [payload.version, payload.specJson, payload.packId, payload.title, tx.dialect.now(), op.workflowId]);
      if (updated.affectedRows !== 1) throw new Error('Workflow version write failed');
      await syncFacades(tx, op, payload);
    }
  }, op.attempt);
  return JSON.parse(completed.resultJson!) as Record<string, unknown>;
}

/** Authenticated callers see only a receipt, never YAML, credentials or the recovery bundle. */
export async function scopedOperation(db: IDatabase, requestId: string, workflowId: string, actorId: string) {
  const op = await new PackOperationRepository(db).get(requestId);
  if (!op || op.workflowId !== workflowId || op.actorId !== actorId) throw new PackOperationError(404, 'OPERATION_NOT_FOUND');
  return op;
}
export const operationReceipt = (op: PackOperation) => ({ requestId: op.requestId, kind: op.kind, state: op.phase,
  ...(op.phase === 'completed' ? { result: JSON.parse(op.resultJson!) } : {}) });

/** Replays only the persisted immutable target, with exact ref leases, never a new candidate/number. */
export async function reconcilePackOperation(db: IDatabase, config: ClawFlowsGitConfig, requestId: string, workflowId: string, actorId: string) {
  let op = await scopedOperation(db, requestId, workflowId, actorId);
  if (op.phase === 'completed' || op.phase === 'failed') return operationReceipt(op);
  if (op.phase === 'preparing') {
    return operationReceipt(await new PackOperationRepository(db).failBeforePush(requestId, JSON.stringify({ code: 'PREPARATION_CANCELLED' }), op.attempt));
  }
  op = await new PackOperationRepository(db).preflightRecovery(requestId, op.attempt);
  if (op.phase === 'completed') return operationReceipt(op);
  const payload = op.payloadJson ? JSON.parse(op.payloadJson) as OperationPayload : null;
  if (!payload || payload.remoteHash !== remoteIdentity(config)) throw new PackOperationError(409, 'OPERATION_NOT_RECOVERABLE', requestId);
  try {
    if (op.phase === 'prepared') {
      const claim = await new PackOperationRepository(db).markPushing(requestId, op.attempt);
      if (claim.operation.phase === 'completed') return operationReceipt(claim.operation);
      if (!claim.claimed && !['pushing', 'unknown'].includes(claim.operation.phase)) throw new PackOperationError(409, 'OPERATION_NOT_RECOVERABLE', requestId);
    }
    if (op.kind === 'save') await resumePreparedPackCandidate(payload.git as PreparedPackCandidate, config);
    else await resumePreparedDeployment(payload.git as PreparedDeployment, config);
    await completePackOperation(db, op);
  } catch {
    await recordUnknown(db, requestId, op.attempt);
    throw new PackOperationError(503, op.kind === 'save' ? 'SAVE_OUTCOME_UNKNOWN' : 'DEPLOY_OUTCOME_UNKNOWN', requestId, payload.git.branch, payload.git.commit);
  }
  op = await scopedOperation(db, requestId, workflowId, actorId);
  return operationReceipt(op);
}

/** Journal errors never become success; callers still return an explicit unknown receipt identity. */
export async function recordUnknown(db: IDatabase, requestId: string, expectedAttempt: number): Promise<void> {
  const repo = new PackOperationRepository(db);
  try {
    const current = await repo.get(requestId);
    if (current?.phase === 'completed') return;
    await repo.markUnknown(requestId, JSON.stringify({ code: 'REMOTE_OR_DATABASE_OUTCOME_UNKNOWN' }), expectedAttempt);
  } catch {
    // A failed DB acknowledgement may leave the durable phase as pushing. It
    // still holds the workflow lock; subsequent reconciliation reads that state.
    throw new PackOperationError(503, 'OPERATION_STATUS_UNAVAILABLE', requestId);
  }
}
