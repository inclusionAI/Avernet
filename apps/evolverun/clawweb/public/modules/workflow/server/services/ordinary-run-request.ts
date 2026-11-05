import { createHash, randomBytes } from 'node:crypto';
import type { Request } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { isTrialRun } from '@avernet/clawweb-shared/server/services/run-purpose';
import { RepairBatchError } from '../contracts/repair-batch.js';
import { registerRunRequest } from './run-request-registration.js';
import { OrdinaryRunRequestRepository, assertOrdinaryRequestSchema, type OrdinaryRequestRow } from '../repositories/ordinary-run-request-repository.js';
import type { FlowRunInsert } from '../repositories/flow-run-repository.js';

export type OrdinaryPrincipal = { botId: string; ownerId: string };
export type OrdinaryRunIdentity = Readonly<{ originBotId: string | null; userId: string | null;
  originSessionKey: string | null; originSessionId: string | null }>;
export type OrdinaryRelation = Readonly<{ requestKey: string; parent: Readonly<{ flowId: string; workflowId: string }>;
  child: Readonly<{ flowId: string; workflowId: string; identity: OrdinaryRunIdentity }> }>;
/** Host must authenticate independently of request-controlled body, headers and run metadata,
 * and verify all proposed/stored identity and session fields against trusted authority. */
export type OrdinaryRequestAuth = {
  authenticate: (request: Request) => Promise<OrdinaryPrincipal | null>;
  authorize: (principal: OrdinaryPrincipal, relation: OrdinaryRelation, tx: IDatabase) => Promise<boolean>;
};
export class OrdinaryRequestError extends Error {
  constructor(readonly status: 400 | 404 | 409, message: string) { super(message); }
}
const invalid = () => new OrdinaryRequestError(400, 'Invalid ordinary request');
const conflict = () => new OrdinaryRequestError(409, 'Ordinary request identity conflict');
const absent = () => new OrdinaryRequestError(404, 'Not found');
const hash = (s: string) => createHash('sha256').update(s, 'utf8').digest('hex');
export const validOrdinaryKey = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(v);
// Limit indexed MySQL identifiers so old VARCHAR(190) schemas cannot truncate.
const validId = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(v);
const ordinaryFields = new Set(['type', 'ordinary_request_key', 'ordinary_parent_flow_id', 'flow_id', 'workflow_id',
  'workflow_title', 'status', 'state_json', 'triggered_by', 'params_json', 'input_json', 'node_count', 'identity_key', 'current_phase',
  'started_at', 'credentials_json', 'origin_session_key', 'origin_session_id', 'origin_bot_id', 'user_id', 'plugin_version',
  'engine', 'workflow_version', 'workflow_deploy_number']);
const trialKeys = ['run_request_id', 'run_request_parent_flow_id', 'run_request_bot_id', 'run_request_owner_id',
  'candidate_commit', 'pack_digest', 'input_digest'];
export const hasTrialFields = (body: Record<string, unknown>) => trialKeys.some(key => Object.hasOwn(body, key));

function fingerprint(run: FlowRunInsert): string {
  // Flow ID and startedAt intentionally excluded: a delivery may propose a new
  // native ID or occur later. Every other persisted field is frozen, including
  // opaque state/credentials, and only the hash is retained in the association.
  const fields = [run.workflowId, run.workflowTitle ?? null, run.status, run.stateJson ?? null,
    run.paramsJson ?? null, run.inputJson ?? null, run.nodeCount ?? 0, run.triggeredBy ?? null,
    run.identityKey ?? null, run.credentialsJson ?? null, run.originSessionKey ?? null, run.originSessionId ?? null,
    run.originBotId ?? null, run.userId ?? null, run.pluginVersion ?? null, run.engine ?? null,
    run.workflowVersion ?? null, run.workflowDeployNumber ?? null];
  // Preserve pre-current_phase fingerprints for omitted/null values; a supplied
  // phase extends the identity and therefore conflicts with another phase.
  if (run.currentPhase != null) fields.push(run.currentPhase);
  return hash(JSON.stringify(fields));
}
function validate(body: Record<string, unknown>, run: FlowRunInsert): { key: string; parent: string } {
  if (hasTrialFields(body)) throw conflict();
  if (Object.keys(body).some(key => !ordinaryFields.has(key))) throw invalid();
  if (!validOrdinaryKey(body.ordinary_request_key) || !validId(body.ordinary_parent_flow_id)
    || !validId(run.flowId) || !validId(run.workflowId) || body.ordinary_parent_flow_id === run.flowId) throw invalid();
  if (run.status !== 'pending'
    || body.node_count != null && (!Number.isSafeInteger(body.node_count) || Number(body.node_count) < 0 || Number(body.node_count) > 2_147_483_647)
    || body.started_at != null && (!Number.isSafeInteger(body.started_at) || Number(body.started_at) < 0)) throw invalid();
  for (const field of ['workflow_title','state_json','params_json','input_json','triggered_by','identity_key','credentials_json',
    'origin_session_key','origin_session_id','origin_bot_id','user_id','plugin_version','engine','current_phase']) {
    if (body[field] != null && typeof body[field] !== 'string') throw invalid();
  }
  if (typeof run.originSessionKey === 'string' && run.originSessionKey.startsWith('agent:main:workflow-trial:')) throw conflict();
  for (const [field, json] of [['params', run.paramsJson], ['input', run.inputJson], ['credentials', run.credentialsJson]] as const) {
    if (json == null) continue;
    let parsed: unknown;
    try { parsed = JSON.parse(json); } catch { throw invalid(); }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw invalid();
    if (field === 'credentials' && Object.hasOwn(parsed, 'STAGE')) {
      const stage = (parsed as Record<string, unknown>).STAGE;
      if (typeof stage !== 'string' || ((parsed as Record<string, unknown>).BOT_TYPE !== 'personal'
        && !['draft', 'verify', 'online'].includes(stage.trim()))) throw invalid();
    }
  }
  return { key: body.ordinary_request_key, parent: body.ordinary_parent_flow_id };
}
class TrialRegistration extends Error {}
class DuplicateAssociation extends Error {}
const duplicate = (e: unknown) => ['SQLITE_CONSTRAINT_PRIMARYKEY', 'SQLITE_CONSTRAINT_UNIQUE', 'ER_DUP_ENTRY']
  .includes(String((e as { code?: unknown } | null)?.code));
async function readRun(tx: IDatabase, id: string, lock: boolean): Promise<{ flow_id: string; workflow_id: string; ordinary_run_marker: string | null } | null> {
  const rows = await tx.query<{ flow_id: string; workflow_id: string; ordinary_run_marker: string | null }>(
    `SELECT flow_id,workflow_id,ordinary_run_marker FROM flow_runs WHERE flow_id = ?${lock && tx.dbType === 'mysql' ? ' FOR UPDATE' : ''}`, [id]);
  const row = rows[0];
  if (!row) return null;
  // Case-insensitive MySQL comparisons must not silently identify another run.
  if (row.flow_id !== id || !validId(row.workflow_id)) throw conflict();
  return row;
}
type StoredIdentityRow = { flow_id: string; workflow_id: string; origin_bot_id: string | null; user_id: string | null;
  origin_session_key: string | null; origin_session_id: string | null; ordinary_run_marker: string | null };
function proposedIdentity(run: FlowRunInsert): OrdinaryRunIdentity {
  return Object.freeze({ originBotId: run.originBotId ?? null, userId: run.userId ?? null,
    originSessionKey: run.originSessionKey ?? null, originSessionId: run.originSessionId ?? null });
}
async function storedIdentity(tx: IDatabase, row: OrdinaryRequestRow): Promise<OrdinaryRunIdentity> {
  const rows = await tx.query<StoredIdentityRow>(`SELECT flow_id,workflow_id,origin_bot_id,user_id,origin_session_key,origin_session_id,ordinary_run_marker
    FROM flow_runs WHERE flow_id = ?${tx.dbType === 'mysql' ? ' FOR UPDATE' : ''}`, [row.child_flow_id]);
  const child = rows[0];
  if (!child || !row.child_run_marker || !/^[0-9a-f]{64}$/.test(row.child_run_marker) || child.ordinary_run_marker !== row.child_run_marker
    || child.flow_id !== row.child_flow_id || child.workflow_id !== row.child_workflow_id
    || [child.origin_bot_id, child.user_id, child.origin_session_key, child.origin_session_id]
      .some(value => value !== null && typeof value !== 'string')) throw new Error('Inconsistent ordinary request association');
  return Object.freeze({ originBotId: child.origin_bot_id, userId: child.user_id,
    originSessionKey: child.origin_session_key, originSessionId: child.origin_session_id });
}
async function allowed(tx: IDatabase, principal: OrdinaryPrincipal, auth: OrdinaryRequestAuth, key: string,
  parentId: string, childId: string, workflowId: string, identity: OrdinaryRunIdentity | OrdinaryRequestRow): Promise<boolean> {
  const parent = await readRun(tx, parentId, true);
  if (!parent || await isTrialRun(tx, parentId) || await isTrialRun(tx, childId)) return false;
  if ('request_key_hash' in identity && (!identity.parent_run_marker || !/^[0-9a-f]{64}$/.test(identity.parent_run_marker) || parent.ordinary_run_marker !== identity.parent_run_marker))
    throw new Error('Inconsistent ordinary request association');
  // Existing associations always take identity from the locked, persisted child,
  // not from a retry's supplied fields or from the association fingerprint.
  const verifiedIdentity = 'request_key_hash' in identity ? await storedIdentity(tx, identity) : identity;
  const relation: OrdinaryRelation = Object.freeze({ requestKey: key,
    parent: Object.freeze({ flowId: parent.flow_id, workflowId: parent.workflow_id }),
    child: Object.freeze({ flowId: childId, workflowId, identity: verifiedIdentity }) });
  return await auth.authorize(Object.freeze({ ...principal }), relation, tx) === true;
}

export class OrdinaryRunRequestService {
  constructor(private readonly db: IDatabase, private readonly insert: (tx: IDatabase, run: FlowRunInsert) => Promise<void>) {}
  async create(body: Record<string, unknown>, run: FlowRunInsert, principal: OrdinaryPrincipal, auth: OrdinaryRequestAuth) {
    // Detach the persisted input from mutable caller objects before authorization.
    body = Object.freeze({ ...body });
    run = Object.freeze({ ...run });
    const { key, parent } = validate(body, run);
    const keyHash = hash(key), inputHash = fingerprint(run), identity = proposedIdentity(run);
    const perform = () => this.db.transaction(async tx => {
      await assertOrdinaryRequestSchema(tx);
      const repo = new OrdinaryRunRequestRepository(tx);
      const existing = await repo.find(keyHash);
      // Authorize the real winning relation on retry, or the proposed relation
      // on creation, before revealing any stored trial/conflict information.
      if (existing) {
        if (!await allowed(tx, principal, auth, key, existing.parent_flow_id, existing.child_flow_id, existing.child_workflow_id, existing)) throw absent();
      } else if (!await allowed(tx, principal, auth, key, parent, run.flowId, run.workflowId, identity)) throw absent();
      // Delegate legacy/exact trial detection to the same registration code.
      // Any attempted trial write aborts this entire transaction; it can never
      // be converted into an ordinary association.
      try {
        const trial = await registerRunRequest(tx, body, async () => { throw new TrialRegistration(); });
        if (trial !== null) throw new TrialRegistration();
      } catch (error) {
        if (error instanceof TrialRegistration || error instanceof RepairBatchError) throw conflict();
        throw error;
      }
      if (await isTrialRun(tx, parent) || await isTrialRun(tx, run.flowId)) throw conflict();
      // Validate any native relationship metadata against the same parent.
      try {
        const params = run.paramsJson != null ? JSON.parse(run.paramsJson) : null;
        const states: unknown[] = [run.stateJson ? JSON.parse(run.stateJson) : null, params?._clawmind_state];
        for (let state of states) {
          if (state == null) continue;
          if (typeof state === 'string') state = JSON.parse(state);
          if (!state || typeof state !== 'object' || Array.isArray(state)) throw invalid();
          const stateObject = state as Record<string, unknown>;
          const meta = stateObject.subworkflowMeta;
          if (meta != null && (typeof meta !== 'object' || Array.isArray(meta))) throw invalid();
          const trialStateKeys = ['trialId', 'trial_id', 'trialContext', 'trial_context', 'runRequestId',
            'runRequestParentFlowId', 'runRequestBotId', 'runRequestOwnerId'];
          const pending: unknown[] = [stateObject];
          let visited = 0;
          while (pending.length) {
            const value = pending.pop();
            if (!value || typeof value !== 'object') continue;
            if (++visited > 50_000) throw invalid();
            if (Array.isArray(value)) { for (const nested of value) pending.push(nested); continue; }
            const record = value as Record<string, unknown>;
            if (hasTrialFields(record) || trialStateKeys.some(key => Object.hasOwn(record, key))) throw conflict();
            if ([record.originSessionKey, record.origin_session_key].some(session =>
              typeof session === 'string' && session.startsWith('agent:main:workflow-trial:'))) throw conflict();
            for (const nested of Object.values(record)) pending.push(nested);
          }
          const nativeParent = (meta as { parentFlowId?: unknown } | undefined)?.parentFlowId;
          if (nativeParent != null && nativeParent !== parent) throw conflict();
        }
      } catch (error) { if (error instanceof OrdinaryRequestError) throw error; throw invalid(); }
      if (existing) {
        if (existing.parent_flow_id !== parent || existing.child_workflow_id !== run.workflowId || existing.request_fingerprint !== inputHash) throw conflict();
        return { created: false, parent_flow_id: existing.parent_flow_id, flow_id: existing.child_flow_id, workflow_id: existing.child_workflow_id };
      }
      if (await readRun(tx, run.flowId, true)) throw conflict();
      const parentRun = await readRun(tx, parent, true);
      if (!parentRun) throw absent();
      if (parentRun.ordinary_run_marker !== null && !/^[0-9a-f]{64}$/.test(parentRun.ordinary_run_marker))
        throw new Error('Invalid parent run marker');
      const parentMarker = parentRun.ordinary_run_marker ?? randomBytes(32).toString('hex');
      if (parentRun.ordinary_run_marker === null) {
        const updated = await tx.exec('UPDATE flow_runs SET ordinary_run_marker = ? WHERE flow_id = ? AND ordinary_run_marker IS NULL', [parentMarker, parent]);
        if (updated.affectedRows !== 1) throw new Error('Unable to bind parent run');
      }
      const childMarker = randomBytes(32).toString('hex');
      const row = { request_key_hash: keyHash, parent_flow_id: parent, child_flow_id: run.flowId,
        child_workflow_id: run.workflowId, request_fingerprint: inputHash,
        parent_run_marker: parentMarker, child_run_marker: childMarker };
      try { await repo.insert(row); } catch (error) { if (duplicate(error)) throw new DuplicateAssociation(); throw error; }
      await this.insert(tx, run);
      const bound = await tx.exec('UPDATE flow_runs SET ordinary_run_marker = ? WHERE flow_id = ? AND ordinary_run_marker IS NULL', [childMarker, run.flowId]);
      if (bound.affectedRows !== 1) throw new Error('Unable to bind child run');
      return { created: true, parent_flow_id: parent, flow_id: run.flowId, workflow_id: run.workflowId };
    });
    try { return await perform(); }
    catch (error) { if (error instanceof DuplicateAssociation) return perform(); throw error; }
  }
  async lookup(key: string, principal: OrdinaryPrincipal, auth: OrdinaryRequestAuth) {
    if (!validOrdinaryKey(key)) throw invalid();
    return this.db.transaction(async tx => {
      await assertOrdinaryRequestSchema(tx);
      const row = await new OrdinaryRunRequestRepository(tx).find(hash(key));
      if (!row || !await allowed(tx, principal, auth, key, row.parent_flow_id, row.child_flow_id, row.child_workflow_id, row)) throw absent();
      return { parent_flow_id: row.parent_flow_id, flow_id: row.child_flow_id, workflow_id: row.child_workflow_id };
    });
  }
}
