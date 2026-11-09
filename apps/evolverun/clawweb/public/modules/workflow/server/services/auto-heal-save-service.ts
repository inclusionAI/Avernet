/** Save a validated workflow draft in workflow_specs and its full Git Pack. */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import type { WorkflowSpec } from '../workflow.js';
import { normalizeWorkflowSpec, validateWorkflowSemantics } from '../workflow.js';
import { validateSpec } from '../validators/workflow-validator.js';
import type { WorkflowSpecRepository } from '../repositories/workflow-spec-repository.js';
import type { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import type { ClawFlowsGitConfig, GitAuthor } from './clawflows-git-client.js';
import { validPackGitId, gitOidPattern } from './pack-git-prepared.js';
import { PackOperationError } from './pack-operation-service.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';
import { withPreparedWorkflowSpec, WorkflowSaveRequiredError } from './pack-spec-save.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError } from './pack-candidate-save.js';
import { editorActiveDeploymentToken, InvalidEditorActiveDeploymentTokenInputError, type EditorActiveDeploymentTokenRow } from './editor-active-deployment-token.js';
import { fingerprintWorkflowSnapshotSaveRequest, type WorkflowSnapshotSaveRequest } from './workflow-snapshot-save-request.js';
import type { WorkflowUnifiedSaveResult, WorkflowUnifiedSaveService } from './workflow-unified-save-service.js';

export type AutoHealSaveInput = { workflowId: string; spec: WorkflowSpec; actorId: string; requestId?: string; createOnly?: boolean; expectedDraftAbsent?: true; expectedActiveDeployNumber?: number; expectedActiveToken?: string;
  sourceCommit?: string; expectedSavedHead?: string | null; packId?: string | null; diagnosisId?: string | null; note?: string | null };
/** Explicit protocol for an existing draft. Baselines are the caller's observed
 * snapshot, not a request to replace them with the server's latest values. */
export type AutoHealUnifiedSaveInput = Pick<WorkflowSnapshotSaveRequest, 'requestId' | 'workflowId' | 'packId' |
  'sourceVersion' | 'sourceCommit' | 'expectedSavedHead' | 'expectedDraftToken' | 'specJson'> & { actorId: string };
/** Trusted host binding, never derived from client grants. The resolver must
 * authenticate/edit-authorize this actor and workflow, bind the actual Pack and
 * repository, and independently establish the source commit and saved-ref head.
 * Its service must enforce the exact-candidate Pack policy and edit ACL, and the
 * host must quiesce legacy writers, protect all Pack paths, and enforce the
 * unified service's transport, secrecy, revocation and operational requirements.
 * The baseline is checked against the caller's observed snapshot, never refreshed
 * on their behalf. No existing composition currently provides these guarantees. */
export type AutoHealUnifiedSaveBinding = Readonly<{
  packId: string; repositoryId: string; sourceVersion: 'current' | 'draft'; sourceCommit: string;
  expectedSavedHead: string | null; expectedDraftToken: string;
  service: Pick<WorkflowUnifiedSaveService, 'save'>;
}>;
export type AutoHealUnifiedSaveResolver = (workflowId: string, actorId: string) => Promise<AutoHealUnifiedSaveBinding | null>;
export type AutoHealUnifiedSaveResponse = Readonly<{
  requestId: string; outcome: WorkflowUnifiedSaveResult['outcome'];
  evidence: WorkflowUnifiedSaveResult['evidence']; reconciliationRequired: boolean;
}>;

/** Ordinary saves include a Git identity; the Editor first-draft stage is DB-only. */
export type AutoHealSaveResult = { ok: true; saved: true; workflowId: string; packId: string; branch?: string; commit?: string };

// Cast numeric identities and timestamps in SQL, before a driver can round a
// BIGINT or truncate a fractional timestamp to a JavaScript Date. Compare the
// exact persisted document and all columns returned by the draft repository.
type DraftSnapshot = { id: string; workflow_id: string; pack_id: string | null; spec_json: string;
  version: string | null; title: string | null; owner_id: string | null; gmt_create: string; gmt_modified: string };
const draftFields: (keyof DraftSnapshot)[] = ['id', 'workflow_id', 'pack_id', 'spec_json', 'version', 'title', 'owner_id', 'gmt_create', 'gmt_modified'];
const snapshotColumns = `CAST(id AS CHAR) AS id, workflow_id, pack_id, spec_json, CAST(version AS CHAR) AS version,
  title, owner_id, CAST(gmt_create AS CHAR) AS gmt_create, CAST(gmt_modified AS CHAR) AS gmt_modified`;
async function readDraftSnapshot(db: IDatabase, workflowId: string, lock = false): Promise<DraftSnapshot | null> {
  const suffix = lock && (db.dbType === 'mysql' || db.dbType === 'zdas') ? ' FOR UPDATE' : '';
  const rows = await db.query<DraftSnapshot>(`SELECT ${snapshotColumns} FROM workflow_specs WHERE workflow_id = ?${suffix}`, [workflowId]);
  if (rows.length > 1) throw new Error('Workflow storage is ambiguous');
  const row = rows[0];
  if (!row) return null;
  if (typeof row.id !== 'string' || typeof row.workflow_id !== 'string' || typeof row.spec_json !== 'string'
    || typeof row.gmt_create !== 'string' || typeof row.gmt_modified !== 'string'
    || ![row.pack_id, row.version, row.title, row.owner_id].every(value => value === null || typeof value === 'string')) {
    throw new Error('Workflow storage has an invalid snapshot');
  }
  return row;
}
const sameDraft = (a: DraftSnapshot, b: DraftSnapshot): boolean => draftFields.every(field => a[field] === b[field]);

// Only known token input failures are conflicts. DB, transaction, crypto and
// unexpected programming failures must still propagate.
function matchesActiveToken(workflowId: string, row: EditorActiveDeploymentTokenRow, expected: string): boolean {
  try {
    return editorActiveDeploymentToken(workflowId, row) === expected.toLowerCase();
  } catch (error) {
    if (error instanceof InvalidEditorActiveDeploymentTokenInputError) {
      throw new PackOperationError(409, 'BASELINE_STALE');
    }
    throw error;
  }
}

export class AutoHealSaveService {
  constructor(private db: IDatabase, _specRepo: WorkflowSpecRepository,
    private deployHistoryRepo: WorkflowDeployHistoryRepository | null,
    private botPermRepo: BotWorkflowPermissionRepository | null,
    private gitConfig?: ClawFlowsGitConfig, private author?: GitAuthor) {}

  /** Separate from legacy save/create/first-draft: no silent DB-only fallback.
   * requestId is correlation only, not idempotency; never automatically retry. */
  async saveUnified(input: AutoHealUnifiedSaveInput, resolve?: AutoHealUnifiedSaveResolver): Promise<AutoHealUnifiedSaveResponse> {
    if (!resolve) throw new PackOperationError(503, 'UNIFIED_SAVE_UNAVAILABLE');
    let binding: AutoHealUnifiedSaveBinding | null;
    try { binding = await resolve(input.workflowId, input.actorId); }
    catch { throw new PackOperationError(503, 'UNIFIED_SAVE_UNAVAILABLE'); }
    if (!binding) throw new PackOperationError(503, 'UNIFIED_SAVE_UNAVAILABLE');
    if (input.packId !== binding.packId || input.sourceVersion !== binding.sourceVersion
      || input.sourceCommit !== binding.sourceCommit || input.expectedSavedHead !== binding.expectedSavedHead
      || input.expectedDraftToken !== binding.expectedDraftToken) throw new PackOperationError(409, 'BASELINE_STALE');
    let snapshot: ReturnType<typeof fingerprintWorkflowSnapshotSaveRequest>;
    try {
      snapshot = fingerprintWorkflowSnapshotSaveRequest({ requestId: input.requestId, actorId: input.actorId,
        workflowId: input.workflowId, packId: binding.packId, repositoryId: binding.repositoryId,
        sourceVersion: binding.sourceVersion, sourceCommit: binding.sourceCommit, expectedSavedHead: binding.expectedSavedHead,
        expectedDraftToken: binding.expectedDraftToken, specJson: input.specJson, changes: [] });
    } catch { throw new PackOperationError(400, 'INVALID_REQUEST'); }
    await assertNoUnresolvedLegacyPackOperation(this.db, input.workflowId);
    const unknown = (): AutoHealUnifiedSaveResponse => ({ requestId: input.requestId, outcome: 'unknown',
      evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });
    try {
      const result = await binding.service.save(snapshot.request, input.actorId);
      const e = result?.evidence, outcome = result?.outcome;
      const validPhases = (outcome === 'confirmed' && e?.db === 'confirmed' && e.git === 'confirmed')
        || (outcome === 'rejected' && e?.db === 'not_started' && e.git === 'not_started')
        || (outcome === 'partial' && e?.db === 'confirmed' && e.git === 'not_started')
        || (outcome === 'unknown' && ((e?.db === 'unknown' && e.git === 'not_started')
          || (e?.db === 'confirmed' && e.git === 'unknown')));
      const validFields = (e?.inputHash === undefined || e.inputHash === snapshot.inputHash)
        && (e?.commit === undefined || (typeof e.commit === 'string' && gitOidPattern.test(e.commit)))
        && (e?.draftToken === undefined || (typeof e.draftToken === 'string' && /^[a-f0-9]{64}$/.test(e.draftToken)));
      const complete = e?.db !== 'confirmed'
        || (e?.inputHash === snapshot.inputHash && e.commit !== undefined && e.draftToken !== undefined);
      if (!validPhases || !validFields || !complete) return unknown();
      return { requestId: input.requestId, outcome, evidence: { db: e.db, git: e.git,
        ...(e.inputHash !== undefined ? { inputHash: e.inputHash } : {}),
        ...(e.db === 'confirmed' && e.commit !== undefined ? { commit: e.commit } : {}),
        ...(e.db === 'confirmed' && e.draftToken !== undefined ? { draftToken: e.draftToken } : {}) },
        reconciliationRequired: outcome === 'partial' || outcome === 'unknown' };
    } catch { return unknown(); }
  }

  async save(input: AutoHealSaveInput): Promise<AutoHealSaveResult> {
    return this.saveValidated(input, false);
  }

  /** Editor-only intermediate stage, never selected from request data. It is
   * restricted to an absent draft and a fully observed active publication. */
  async saveEditorFirstDraft(input: AutoHealSaveInput): Promise<AutoHealSaveResult> {
    if (input.expectedDraftAbsent !== true || input.createOnly !== undefined
      || !Number.isSafeInteger(input.expectedActiveDeployNumber) || input.expectedActiveDeployNumber! <= 0
      || typeof input.expectedActiveToken !== 'string' || !/^[0-9a-f]{64}$/.test(input.expectedActiveToken)) {
      throw new PackOperationError(400, 'INVALID_REQUEST');
    }
    return this.saveValidated(input, true);
  }

  private async saveValidated(input: AutoHealSaveInput, editorFirstDraft: boolean): Promise<AutoHealSaveResult> {
    if (!validPackGitId(input.workflowId) || /^__.+__$/.test(input.workflowId) || !input.spec
      || (input.createOnly !== undefined && typeof input.createOnly !== 'boolean')
      || (input.expectedDraftAbsent !== undefined && (input.expectedDraftAbsent !== true || input.createOnly !== undefined))
      || (input.expectedActiveDeployNumber !== undefined && (input.expectedDraftAbsent !== true || !Number.isSafeInteger(input.expectedActiveDeployNumber) || input.expectedActiveDeployNumber <= 0))
      || (input.expectedDraftAbsent === true && input.expectedActiveDeployNumber === undefined)
      || (input.expectedActiveToken !== undefined && (input.expectedDraftAbsent !== true || input.expectedActiveDeployNumber === undefined
        || typeof input.expectedActiveToken !== 'string' || !/^[0-9a-f]{64}$/i.test(input.expectedActiveToken)))
      || (input.requestId !== undefined && (typeof input.requestId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(input.requestId)))
      || (input.sourceCommit !== undefined && !gitOidPattern.test(input.sourceCommit))
      || (input.expectedSavedHead !== undefined && input.expectedSavedHead !== null && !gitOidPattern.test(input.expectedSavedHead))) {
      throw new PackOperationError(400, 'INVALID_REQUEST');
    }
    if (input.sourceCommit !== undefined || (input.expectedSavedHead !== undefined && !(input.createOnly && input.expectedSavedHead === null))) throw new PackOperationError(400, 'UNSUPPORTED_GIT_PRECONDITION');
    // Validate and persist the same JSON document, not a normalized projection.
    let submitted: WorkflowSpec;
    try { submitted = JSON.parse(JSON.stringify(input.spec)) as WorkflowSpec; }
    catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
    const validation = validateSpec(submitted);
    if (!validation.valid || !validation.normalizedSpec) throw new PackOperationError(400, 'INVALID_WORKFLOW');
    const spec = normalizeWorkflowSpec(validation.normalizedSpec);
    try { validateWorkflowSemantics(spec); } catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
    if (spec.id !== input.workflowId) throw new PackOperationError(400, 'WORKFLOW_ID_MISMATCH');
    if (!input.actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');

    if (input.createOnly) {
      await assertNoUnresolvedLegacyPackOperation(this.db, input.workflowId);
      return this.create(input, submitted);
    }

    const row = await readDraftSnapshot(this.db, input.workflowId);
    if (!row) return this.saveFirstDraft(input, submitted, spec.title || input.workflowId, editorFirstDraft);
    if (row && !await this.botPermRepo.hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
    if (input.expectedDraftAbsent) throw new PackOperationError(409, 'DRAFT_ALREADY_EXISTS');
    await assertNoUnresolvedLegacyPackOperation(this.db, input.workflowId);
    const packId = input.packId ?? row.pack_id ?? input.workflowId;
    if (!validPackGitId(packId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    // Store the actual document. `content` is a legitimate user-authored field,
    // not a reserved projection slot. The export route can serialize this JSON.
    const specJson = JSON.stringify(submitted);

    if (!['sqlite', 'mysql', 'zdas'].includes(this.db.dbType)) throw new PackOperationError(503, 'DRAFT_STORAGE_UNAVAILABLE');
    return this.saveDraftWithGit(input, submitted, packId, async tx => {
      if (tx.dbType !== this.db.dbType) throw new PackOperationError(503, 'DRAFT_STORAGE_UNAVAILABLE');
      // Publication also locks the draft first. Lock grants before any consistent
      // permission read so MySQL repeatable-read sees committed revocations.
      const current = await readDraftSnapshot(tx, input.workflowId, true);
      const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : '';
      await tx.query(`SELECT id FROM bot_workflow_permissions WHERE workflow_id = ? ORDER BY id${lock}`, [input.workflowId]);
      if (!await this.botPermRepo!.withDatabase(tx).hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
      if (!current || row.workflow_id !== input.workflowId || current.workflow_id !== input.workflowId || !sameDraft(row, current)) {
        throw new PackOperationError(409, 'DRAFT_CONFLICT');
      }
      // This remains a retirement preflight, not a fence against legacy writers.
      await assertNoUnresolvedLegacyPackOperation(tx, input.workflowId);
      const title = spec.title || input.workflowId;
      const now = tx.dialect.now();
      const updated = await tx.exec('UPDATE workflow_specs SET spec_json = ?, pack_id = ?, title = ?, owner_id = ?, gmt_modified = ? WHERE workflow_id = ? AND id = ?',
        [specJson, packId, title, input.actorId, now, input.workflowId, current.id]);
      if (updated.affectedRows !== 1) throw new Error('Workflow update failed');
      const written = await readDraftSnapshot(tx, input.workflowId, true);
      // A DATETIME/TIMESTAMP column with fractional precision can render the
      // whole-second bound value with a zero fractional suffix on MySQL/ZDAS.
      const modifiedMatches = written && (written.gmt_modified === String(now)
        || (tx.dbType !== 'sqlite' && written.gmt_modified.startsWith(`${now}.`)
          && /^0+$/.test(written.gmt_modified.slice(String(now).length + 1))));
      if (!written || !modifiedMatches || !sameDraft(written, { ...current, spec_json: specJson, pack_id: packId, title,
        owner_id: input.actorId, gmt_modified: written.gmt_modified })) throw new Error('Workflow update failed');
      return written;
    });
  }

  /** Restore only an already-published workflow, never create a new Pack identity. */
  private async saveFirstDraft(input: AutoHealSaveInput, submitted: WorkflowSpec, title: string, editorFirstDraft: boolean): Promise<AutoHealSaveResult> {
    const active = await this.deployHistoryRepo?.findActiveByWorkflowId(input.workflowId);
    if (!active) throw new PackOperationError(input.expectedActiveToken === undefined ? 404 : 409,
      input.expectedActiveToken === undefined ? 'WORKFLOW_NOT_FOUND' : 'BASELINE_STALE');
    if (!await this.botPermRepo!.hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
    await assertNoUnresolvedLegacyPackOperation(this.db, input.workflowId);
    if (input.expectedActiveToken !== undefined && (active.deploy_number !== input.expectedActiveDeployNumber
      || !matchesActiveToken(input.workflowId, active, input.expectedActiveToken))) {
      throw new PackOperationError(409, 'BASELINE_STALE');
    }
    const packId = active.pack_id;
    if (!validPackGitId(packId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (input.packId != null && input.packId !== packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH');
    const conflict = () => new PackOperationError(409, input.expectedDraftAbsent ? 'DRAFT_ALREADY_EXISTS' : 'WORKFLOW_ALREADY_EXISTS');

    const write = async (tx: IDatabase): Promise<DraftSnapshot> => {
      const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : '';
      // Lock the existing active publication first to serialize first-draft saves,
      // rather than sharing a missing-draft gap lock before competing for active.
      // Re-read the same publication projection as the repository.
      // SQLite's BEGIN IMMEDIATE already serializes writers; managed DBs need locks.
      const current = (await tx.query<NonNullable<typeof active>>(`SELECT pack_id, deploy_number, version, tag_name, action,
        spec_json, bot_id, owner_id, gmt_create, gmt_modified FROM workflow_deploy_history
        WHERE workflow_id = ? AND is_active = 1 ORDER BY deploy_number DESC LIMIT 1${lock}`, [input.workflowId]))[0];
      if (!current) throw new PackOperationError(input.expectedActiveToken === undefined ? 404 : 409,
        input.expectedActiveToken === undefined ? 'WORKFLOW_NOT_FOUND' : 'BASELINE_STALE');
      if (input.expectedDraftAbsent && current.deploy_number !== input.expectedActiveDeployNumber) throw new PackOperationError(409, 'BASELINE_STALE');
      if (input.expectedActiveToken !== undefined
        && !matchesActiveToken(input.workflowId, current, input.expectedActiveToken)) {
        throw new PackOperationError(409, 'BASELINE_STALE');
      }
      if (JSON.stringify(current) !== JSON.stringify(active)) throw new PackOperationError(409, 'BASELINE_STALE');
      if (input.packId != null && input.packId !== current.pack_id) throw new PackOperationError(409, 'PACK_ID_MISMATCH');
      // Run the permission policy on the transaction connection so grants and
      // BotDirectory membership are checked as late as possible before insertion,
      // not from an earlier pool read. External directory state is not lockable by
      // this database transaction; the active row lock only serializes saves that
      // already reached this same check.
      await tx.query(`SELECT id FROM bot_workflow_permissions WHERE workflow_id = ?${lock}`, [input.workflowId]);
      // The first snapshot read follows both locks, so RR sees earlier save
      // winners and current grants. Do not lock draft here: publication locks
      // draft before active, and an inverse row-lock order could deadlock.
      if ((await tx.query('SELECT id FROM workflow_specs WHERE workflow_id = ?', [input.workflowId])).length) throw conflict();
      if (!await this.botPermRepo!.withDatabase(tx).hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
      await assertNoUnresolvedLegacyPackOperation(tx, input.workflowId);
      try {
        const now = tx.dialect.now();
        const inserted = await tx.exec('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?)',
          [input.workflowId, packId, JSON.stringify(submitted), title, input.actorId, now, now]);
        if (inserted.affectedRows !== 1) throw new Error('Workflow creation failed');
      } catch (error) {
        const code = (error as { code?: string }).code;
        if (code === 'ER_DUP_ENTRY' || code?.startsWith('SQLITE_CONSTRAINT_UNIQUE')) throw conflict();
        throw error;
      }
      const created = await readDraftSnapshot(tx, input.workflowId);
      if (!created || created.workflow_id !== input.workflowId || created.pack_id !== packId
        || created.spec_json !== JSON.stringify(submitted) || created.title !== title || created.owner_id !== input.actorId) {
        throw new Error('Workflow creation failed');
      }
      return created;
    };
    if (!editorFirstDraft) return this.saveDraftWithGit(input, submitted, packId, write);
    let completed = false;
    try {
      await this.db.transaction(async tx => { await write(tx); completed = true; });
    } catch (error) {
      if (completed) throw new PackOperationError(503, 'SAVE_DB_OUTCOME_UNKNOWN', input.requestId);
      throw error;
    }
    return { ok: true, saved: true, workflowId: input.workflowId, packId };
  }

  /** Prepare before the existing transaction; push only after its acknowledgement
   * and a fresh full-row and edit-permission check. This is not an atomic
   * cross-store transaction: partial and uncertain outcomes require review. */
  private async saveDraftWithGit(input: AutoHealSaveInput, spec: WorkflowSpec, packId: string,
    write: (tx: IDatabase) => Promise<DraftSnapshot>): Promise<AutoHealSaveResult> {
    if (!this.gitConfig?.remoteUrl?.trim() || !this.author) {
      throw new PackOperationError(503, 'SAVE_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED', input.requestId);
    }
    let result: { branch: string; commit: string } | undefined;
    let candidate: { branch: string; commit: string } | undefined;
    let dbConfirmed = false, pushStarted = false;
    try {
      await withPreparedWorkflowSpec({ workflowId: input.workflowId, packId, spec, config: this.gitConfig,
        author: this.author }, async (prepared, push) => {
        // Neither an existing DB document nor active history authorizes a
        // YAML-only replacement. Only create() may initialize a new empty Pack;
        // ordinary saves/restores require wf, with no fallback to another ref.
        if (prepared.sourceVersion === 'initial') {
          throw new PackOperationError(409, 'SAVE_REQUIRED', input.requestId);
        }
        candidate = { branch: prepared.branch, commit: prepared.commit };
        let completed = false;
        let committed: DraftSnapshot | undefined;
        try {
          await this.db.transaction(async tx => { committed = await write(tx); completed = true; });
        } catch (error) {
          if (completed) throw new PackOperationError(503, 'SAVE_DB_OUTCOME_UNKNOWN', input.requestId);
          throw error;
        }
        dbConfirmed = true;
        const current = await readDraftSnapshot(this.db, input.workflowId);
        if (!committed || !current || !sameDraft(committed, current)
          || !await this.botPermRepo!.hasEditPermission(input.workflowId, input.actorId)) {
          throw new Error('Committed workflow changed before push');
        }
        pushStarted = true;
        result = await push();
      });
    } catch (error) {
      if (dbConfirmed) {
        if (pushStarted) throw new PackOperationError(503, 'SAVE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', input.requestId,
          error instanceof PackCandidateOutcomeUnknownError ? error.branch : candidate?.branch,
          error instanceof PackCandidateOutcomeUnknownError ? error.commit : candidate?.commit);
        throw new PackOperationError(503, 'SAVE_DB_CONFIRMED_GIT_NOT_STARTED', input.requestId);
      }
      if (error instanceof WorkflowSaveRequiredError) throw new PackOperationError(409, 'SAVE_REQUIRED', input.requestId);
      if (error instanceof PackCandidateConflictError) throw new PackOperationError(409, 'BASELINE_STALE', input.requestId);
      throw error;
    }
    if (!result) throw new Error('Workflow save did not complete');
    return { ok: true, saved: true, workflowId: input.workflowId, packId, ...result };
  }

  private async create(input: AutoHealSaveInput, spec: WorkflowSpec): Promise<AutoHealSaveResult> {
    const packId = input.packId ?? input.workflowId;
    if (!validPackGitId(packId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!this.gitConfig?.remoteUrl || !this.author) throw new PackOperationError(503, 'CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED');
    const conflict = () => new PackOperationError(409, 'WORKFLOW_ALREADY_EXISTS');
    // Preflight also avoids contacting Git for a known identity; the transaction
    // repeats these checks and relies on the workflow_id unique index for inserts.
    const assertUnused = async (db: IDatabase) => {
      if ((await db.query('SELECT id FROM workflow_specs WHERE workflow_id = ? OR pack_id = ? LIMIT 1', [input.workflowId, packId])).length
        || (await db.query('SELECT id FROM bot_workflow_permissions WHERE workflow_id = ? LIMIT 1', [input.workflowId])).length
        || (await db.query('SELECT id FROM workflow_deploy_history WHERE workflow_id = ? OR pack_id = ? LIMIT 1', [input.workflowId, packId])).length) throw conflict();
    };
    await assertUnused(this.db);
    let initialized: { branch: string; commit: string } | undefined;
    let candidate: { branch: string; commit: string } | undefined;
    let dbConfirmed = false, pushStarted = false;
    try {
      await withPreparedWorkflowSpec({ workflowId: input.workflowId, packId, spec, config: this.gitConfig,
        author: this.author, createOnly: true, expectedSavedHead: null }, async (prepared, push) => {
        candidate = { branch: prepared.branch, commit: prepared.commit };
        let transactionBodyCompleted = false;
        let created: DraftSnapshot | null = null;
        try {
          await this.db.transaction(async tx => {
            await assertUnused(tx);
            try {
              const inserted = await tx.exec('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?)',
                [input.workflowId, packId, JSON.stringify(spec), spec.title || input.workflowId, input.actorId, tx.dialect.now(), tx.dialect.now()]);
              if (inserted.affectedRows !== 1) throw new Error('Workflow creation failed');
            } catch (error) {
              const code = (error as { code?: string }).code;
              if (code === 'ER_DUP_ENTRY' || code?.startsWith('SQLITE_CONSTRAINT_UNIQUE')) throw conflict();
              throw error;
            }
            const permissions = new BotWorkflowPermissionRepository(tx);
            await permissions.upsert({ bot_id: null, bot_owner_id: input.actorId, workflow_id: input.workflowId, can_view: 1, can_execute: 1, can_edit: 1 });
            const grants = await tx.query<{ bot_id: string | null; bot_owner_id: string; can_view: number; can_execute: number; can_edit: number }>(
              'SELECT bot_id, bot_owner_id, can_view, can_execute, can_edit FROM bot_workflow_permissions WHERE workflow_id = ?', [input.workflowId]);
            if (grants.length !== 1 || grants[0].bot_id !== null || grants[0].bot_owner_id !== input.actorId
              || grants[0].can_view !== 1 || grants[0].can_execute !== 1 || grants[0].can_edit !== 1
              || !await permissions.hasEditPermission(input.workflowId, input.actorId)) throw new Error('Workflow permission creation failed');
            created = await readDraftSnapshot(tx, input.workflowId);
            if (!created || created.workflow_id !== input.workflowId || created.pack_id !== packId
              || created.spec_json !== JSON.stringify(spec) || created.title !== (spec.title || input.workflowId)
              || created.owner_id !== input.actorId) throw new Error('Workflow creation failed');
            transactionBodyCompleted = true;
          });
        } catch (error) {
          // A transaction error after its callback returned may mean COMMIT was
          // applied but its acknowledgement was lost. Do not start a Git push.
          if (transactionBodyCompleted) throw new PackOperationError(503, 'CREATE_DB_OUTCOME_UNKNOWN', input.requestId);
          throw error;
        }
        dbConfirmed = true;
        // Recheck the committed identity and complete document, plus the host's
        // edit policy. This is a preflight, not a lock: they may change again
        // between this read and the independent Git push.
        const current = await readDraftSnapshot(this.db, input.workflowId);
        if (!created || !current || !sameDraft(created, current)
          || !await this.botPermRepo!.hasEditPermission(input.workflowId, input.actorId)) {
          throw new Error('Committed workflow changed before push');
        }
        pushStarted = true;
        initialized = await push();
      });
    } catch (error) {
      // Neither a failed recheck nor a failed push undoes the DB commit.
      // Keep the old 503 behavior while exposing the independently known phases.
      if (dbConfirmed) {
        if (pushStarted) throw new PackOperationError(503, 'CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', input.requestId,
          error instanceof PackCandidateOutcomeUnknownError ? error.branch : candidate?.branch,
          error instanceof PackCandidateOutcomeUnknownError ? error.commit : candidate?.commit);
        throw new PackOperationError(503, 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED', input.requestId);
      }
      if (error instanceof PackCandidateConflictError) throw conflict();
      throw error;
    }
    if (!initialized) throw new Error('Workflow initialization did not complete');
    return { ok: true, saved: true, workflowId: input.workflowId, packId, ...initialized };
  }

}
