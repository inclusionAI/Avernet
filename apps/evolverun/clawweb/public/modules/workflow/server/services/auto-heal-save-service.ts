/** Save a validated workflow draft in workflow_specs; publication is separate. */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import type { WorkflowSpec } from '../workflow.js';
import { normalizeWorkflowSpec, validateWorkflowSemantics } from '../workflow.js';
import { validateSpec } from '../validators/workflow-validator.js';
import { WorkflowSpecRepository } from '../repositories/workflow-spec-repository.js';
import type { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import type { ClawFlowsGitConfig, GitAuthor } from './clawflows-git-client.js';
import { validPackGitId, gitOidPattern } from './pack-git-prepared.js';
import { PackOperationError } from './pack-operation-service.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';
import { withPreparedWorkflowSpec } from './pack-spec-save.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError } from './pack-candidate-save.js';

export type AutoHealSaveInput = { workflowId: string; spec: WorkflowSpec; actorId: string; requestId?: string; createOnly?: boolean;
  sourceCommit?: string; expectedSavedHead?: string | null; packId?: string | null; diagnosisId?: string | null; note?: string | null };
/** DB-only edits have no Git identity; creation also initializes the saved ref. */
export type AutoHealSaveResult = { ok: true; saved: true; workflowId: string; packId: string; branch?: string; commit?: string };

export class AutoHealSaveService {
  constructor(private db: IDatabase, private specRepo: WorkflowSpecRepository,
    _deployHistoryRepo: WorkflowDeployHistoryRepository | null,
    private botPermRepo: BotWorkflowPermissionRepository | null,
    private gitConfig?: ClawFlowsGitConfig, private author?: GitAuthor) {}

  async save(input: AutoHealSaveInput): Promise<AutoHealSaveResult> {
    if (!validPackGitId(input.workflowId) || /^__.+__$/.test(input.workflowId) || !input.spec
      || (input.createOnly !== undefined && typeof input.createOnly !== 'boolean')
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

    const row = await this.specRepo.findByWorkflowId(input.workflowId);
    if (!row) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    if (row && !await this.botPermRepo.hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
    await assertNoUnresolvedLegacyPackOperation(this.db, input.workflowId);
    const packId = input.packId ?? row?.pack_id ?? input.workflowId;
    if (!validPackGitId(packId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    // Store the actual document. `content` is a legitimate user-authored field,
    // not a reserved projection slot. The export route can serialize this JSON.
    const specJson = JSON.stringify(submitted);

    await this.db.transaction(async tx => {
      const updated = await tx.exec('UPDATE workflow_specs SET spec_json = ?, pack_id = ?, title = ?, owner_id = ?, gmt_modified = ? WHERE workflow_id = ? AND id = ?',
        [specJson, packId, spec.title || input.workflowId, input.actorId, tx.dialect.now(), input.workflowId, row.id]);
      if (updated.affectedRows !== 1) throw new Error('Workflow update failed');
    });
    return { ok: true, saved: true, workflowId: input.workflowId, packId };
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
    try {
      await withPreparedWorkflowSpec({ workflowId: input.workflowId, packId, spec, config: this.gitConfig,
        author: this.author, createOnly: true, expectedSavedHead: null }, async (_prepared, push) => {
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
          // Git and DB cannot commit atomically. A lost push or DB commit response
          // is reported unknown, never retried or compensated by deleting a ref.
          initialized = await push();
        });
      });
    } catch (error) {
      if (error instanceof PackCandidateConflictError) throw conflict();
      if (error instanceof PackCandidateOutcomeUnknownError) throw new PackOperationError(503, 'CREATE_OUTCOME_UNKNOWN', input.requestId, error.branch, error.commit);
      throw error;
    }
    // Only saved is leased: an independent actor can create wf/<id> after the
    // absence check. Operators must reconcile that unavoidable cross-ref race.
    if (!initialized) throw new Error('Workflow initialization did not complete');
    return { ok: true, saved: true, workflowId: input.workflowId, packId, ...initialized };
  }

}
