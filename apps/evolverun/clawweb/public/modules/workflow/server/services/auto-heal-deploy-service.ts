/** Publish an immutable saved Pack; ClawWeb operation state never enters public deployment history. */
import { isDeepStrictEqual } from 'node:util';
import { parse as parseYaml } from 'yaml';
import { normalizeWorkflowSpec } from '../workflow.js';
import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository } from '../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import { PackOperationRepository } from '../repositories/pack-operation-repository.js';
import { cloneClawflows, createTempRepo, getMaxDeployNumberFromGitTags, removeTempRepo,
  type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { prepareDeployCandidate, pushPreparedDeployment, type PreparedDeployment } from './auto-heal-deploy-git.js';
import { gitOidPattern, readPreparedRemoteRefs, validPackGitId } from './pack-git-prepared.js';
import { candidateProjection, completePackOperation, operationHash, PackOperationError, remoteIdentity,
  recordUnknown, requestIdentity, type OperationPayload } from './pack-operation-service.js';

export type AutoHealDeployInput = { workflowId: string; actorId: string; candidateCommit?: string; requestId?: string;
  packId?: string | null; diagnosisId?: string | null; sourceFlowId?: string | null; note?: string | null; autoRun?: boolean };
export type AutoHealDeployResult = { ok: boolean; deployed: boolean; workflowId: string; packId: string; deployNumber: number;
  version: number; tagName: string; requestId?: string; commit?: string; candidateCommit?: string; messageId?: string | null };

export class AutoHealDeployService {
  constructor(private db: IDatabase, private specRepo: WorkflowSpecRepository, private deployHistoryRepo: WorkflowDeployHistoryRepository,
    private botPermRepo: BotWorkflowPermissionRepository | null, private gitConfig: ClawFlowsGitConfig, private author: GitAuthor,
    private triggerRun?: (workflowId: string, actorId: string, sourceFlowId?: string | null) => Promise<string | null>) {}

  async deploy(input: AutoHealDeployInput): Promise<AutoHealDeployResult> {
    const { workflowId, actorId } = input;
    if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId)
      || (input.candidateCommit !== undefined && !gitOidPattern.test(input.candidateCommit))) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');
    const [canExecute, canEdit] = await Promise.all([this.botPermRepo.hasExecutePermission(workflowId, actorId), this.botPermRepo.hasEditPermission(workflowId, actorId)]);
    if (!canExecute && !canEdit) throw new PackOperationError(403, 'FORBIDDEN');
    const specRow = await this.specRepo.findByWorkflowId(workflowId);
    if (!specRow) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    const requestId = requestIdentity(input.requestId, randomUUID);
    const repo = new PackOperationRepository(this.db);
    const identity = { requestId, workflowId, actorId, kind: 'deploy' as const,
      inputHash: operationHash({ workflowId, remote: remoteIdentity(this.gitConfig), candidateCommit: input.candidateCommit ?? null,
        packId: input.packId ?? null, note: input.note ?? null, autoRun: input.autoRun ?? true, sourceFlowId: input.sourceFlowId ?? null }) };
    let started = await repo.begin(identity);
    if (!started.created && started.operation.phase === 'failed') started = await repo.restartFailed(identity, started.operation.attempt);
    if (!started.created) {
      if (started.operation.phase === 'completed') return JSON.parse(started.operation.resultJson!) as AutoHealDeployResult;
      throw new PackOperationError(409, 'OPERATION_IN_PROGRESS', requestId);
    }
    let pushing = false;
    let prepared: PreparedDeployment | undefined;
    let result: AutoHealDeployResult;
    try {
      result = await withGitOperation({ workflowId, operation: 'autoheal_deploy' }, async () => {
        const dir = await createTempRepo();
        try {
          await cloneClawflows(dir, this.gitConfig);
          const savedRef = `refs/heads/saved/${workflowId}`, activeRef = `refs/heads/wf/${workflowId}`;
          const refs = await readPreparedRemoteRefs(dir, [savedRef, activeRef]);
          const savedHead = refs.get(savedRef) ?? null;
          // Compatibility for legacy callers: capture saved once, never rebuild a release from mutable DB YAML.
          const candidate = input.candidateCommit ?? savedHead;
          if (!candidate) throw new PackOperationError(409, 'SAVE_REQUIRED', requestId);
          if (candidate !== savedHead && !await repo.findSavedCandidate(workflowId, candidate)) {
            throw new PackOperationError(409, 'CANDIDATE_NOT_SAVED', requestId);
          }
          const version = await this.deployHistoryRepo.getLatestVersion(workflowId) + 1;
          const maxRows = await this.db.query<{ n: number | null }>('SELECT MAX(deploy_number) AS n FROM workflow_deploy_history WHERE workflow_id = ?', [workflowId]);
          const deployNumber = Math.max(Number(maxRows[0]?.n ?? 0), await getMaxDeployNumberFromGitTags(dir, workflowId)) + 1;
          prepared = await prepareDeployCandidate(dir, workflowId, candidate, refs.get(activeRef) ?? '', deployNumber, version, this.author);
          if (input.packId && input.packId !== prepared.packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH', requestId);
          const projection = candidateProjection(workflowId, prepared.content, prepared.manifestContent);
          let matchesDraft = false;
          try {
            const draft = JSON.parse(started.operation.baseline.workflow.spec_json) as Record<string, unknown>;
            matchesDraft = isDeepStrictEqual(normalizeWorkflowSpec(typeof draft.content === 'string' ? parseYaml(draft.content) : draft),
              normalizeWorkflowSpec(parseYaml(prepared.content)));
          } catch { /* Preserve an unparseable draft rather than replacing it with a release. */ }
          const payload: OperationPayload = { git: prepared, ...projection, packId: prepared.packId,
            remoteHash: remoteIdentity(this.gitConfig), version, note: input.note ?? 'auto-heal deploy', preserveDraft: savedHead !== candidate || !matchesDraft };
          await repo.prepare(requestId, JSON.stringify(payload), started.operation.attempt);
          // Best-effort preflight only: ownership can still change before completion.
          for (const command of projection.facadeCommands ?? []) {
            const owners = await this.db.query<{ workflow_id: string }>('SELECT workflow_id FROM facade_bindings WHERE command = ?', [command]);
            if (owners.some(owner => owner.workflow_id !== workflowId)) throw new PackOperationError(409, 'FACADE_CONFLICT', requestId);
          }
          if (!(await repo.markPushing(requestId, started.operation.attempt)).claimed) throw new PackOperationError(409, 'OPERATION_IN_PROGRESS', requestId);
          pushing = true;
          await pushPreparedDeployment(dir, prepared);
          return await completePackOperation(this.db, (await repo.get(requestId))!) as AutoHealDeployResult;
        } finally { await removeTempRepo(dir); }
      });
    } catch (error) {
      if (pushing) {
        await recordUnknown(this.db, requestId, started.operation.attempt);
        throw new PackOperationError(503, 'DEPLOY_OUTCOME_UNKNOWN', requestId, prepared?.branch, prepared?.commit);
      }
      await repo.failBeforePush(requestId, JSON.stringify({ code: 'PREPARATION_FAILED' }), started.operation.attempt);
      throw error;
    }
    // Release completion is independent of the optional run. An idempotent replay does not send another run.
    if (input.autoRun !== false && this.triggerRun) {
      try { result.messageId = await this.triggerRun(workflowId, actorId, input.sourceFlowId ?? null); }
      catch { result.messageId = null; }
    }
    return result;
  }
}
