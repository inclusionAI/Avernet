/** Compatibility adapter: canvas/auto-heal spec edits use the same complete-Pack save journal. */
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import type { WorkflowSpec } from '../workflow.js';
import { normalizeWorkflowSpec, validateWorkflowSemantics } from '../workflow.js';
import { validateSpec } from '../validators/workflow-validator.js';
import { WorkflowSpecRepository } from '../repositories/workflow-spec-repository.js';
import type { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import type { ClawFlowsGitConfig, GitAuthor } from './clawflows-git-client.js';
import { validPackGitId, gitOidPattern } from './pack-git-prepared.js';
import { withPreparedWorkflowSpec } from './pack-spec-save.js';
import { persistPreparedSave, type SavedPackVersion } from './pack-version-remote.js';
import { PackOperationError, remoteIdentity } from './pack-operation-service.js';
export type AutoHealSaveInput = { workflowId: string; spec: WorkflowSpec; actorId: string; requestId?: string; createOnly?: boolean;
  sourceCommit?: string; expectedSavedHead?: string | null; packId?: string | null; diagnosisId?: string | null; note?: string | null };
export type AutoHealSaveResult = SavedPackVersion;
export class AutoHealSaveService {
  constructor(private db: IDatabase, private specRepo: WorkflowSpecRepository,
    _deployHistoryRepo: WorkflowDeployHistoryRepository | null,
    private botPermRepo: BotWorkflowPermissionRepository | null,
    private gitConfig: ClawFlowsGitConfig, private author: GitAuthor) {}
  async save(input: AutoHealSaveInput): Promise<AutoHealSaveResult> {
    if (!validPackGitId(input.workflowId) || /^__.+__$/.test(input.workflowId) || !input.spec
      || (input.createOnly !== undefined && typeof input.createOnly !== 'boolean')
      || (input.sourceCommit !== undefined && !gitOidPattern.test(input.sourceCommit))) throw new PackOperationError(400, 'INVALID_REQUEST');
    const validation = validateSpec(input.spec);
    if (!validation.valid || !validation.normalizedSpec) throw new PackOperationError(400, 'INVALID_WORKFLOW');
    const spec = normalizeWorkflowSpec(validation.normalizedSpec);
    try { validateWorkflowSemantics(spec); } catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
    if (spec.id !== input.workflowId) throw new PackOperationError(400, 'WORKFLOW_ID_MISMATCH');
    if (!input.actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');
    const row = await this.specRepo.findByWorkflowId(input.workflowId);
    if (!row && !input.createOnly) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    if (row && !await this.botPermRepo.hasEditPermission(input.workflowId, input.actorId)) throw new PackOperationError(403, 'FORBIDDEN');
    const packId = input.packId ?? row?.pack_id ?? input.workflowId;
    if (!validPackGitId(packId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    const initialize = input.createOnly ? async (tx: IDatabase) => {
      if ((await tx.query('SELECT id FROM workflow_specs WHERE workflow_id = ?', [input.workflowId])).length) throw new PackOperationError(409, 'WORKFLOW_ALREADY_EXISTS');
      try {
        const created = await tx.exec('INSERT INTO workflow_specs (workflow_id,pack_id,spec_json,title,owner_id,gmt_create,gmt_modified) VALUES (?,?,?,?,?,?,?)',
          [input.workflowId, packId, JSON.stringify(spec), spec.title || input.workflowId, input.actorId, tx.dialect.now(), tx.dialect.now()]);
        if (created.affectedRows !== 1) throw new Error('Workflow creation failed');
      } catch (error) {
        const code = (error as { code?: string }).code;
        if (code === 'ER_DUP_ENTRY' || code?.startsWith('SQLITE_CONSTRAINT')) throw new PackOperationError(409, 'WORKFLOW_ALREADY_EXISTS');
        throw error;
      }
      const grant = await new BotWorkflowPermissionRepository(tx).upsert({ bot_id: null, bot_owner_id: input.actorId, workflow_id: input.workflowId, can_view: 1, can_execute: 1, can_edit: 1 });
      if (!grant || grant.workflow_id !== input.workflowId || grant.bot_owner_id !== input.actorId || grant.can_edit !== 1) throw new Error('Workflow permission creation failed');
    } : undefined;
    return persistPreparedSave({ db: this.db, workflowId: input.workflowId, actorId: input.actorId, requestId: input.requestId,
      gitConfig: this.gitConfig, gitAuthor: this.author }, { workflowId: input.workflowId, packId, spec,
      createOnly: input.createOnly === true, remote: remoteIdentity(this.gitConfig), sourceCommit: input.sourceCommit ?? null, expectedSavedHead: input.expectedSavedHead ?? null },
    callback => withPreparedWorkflowSpec({ workflowId: input.workflowId, packId, spec, config: this.gitConfig, author: this.author,
      sourceCommit: input.sourceCommit, expectedSavedHead: input.expectedSavedHead }, callback), initialize);
  }
}
