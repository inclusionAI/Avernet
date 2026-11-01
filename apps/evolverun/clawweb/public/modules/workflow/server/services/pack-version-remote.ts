import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveAutoHealGitSettings } from '../config/auto-heal-git-config.js';
import { PackOperationRepository } from '../repositories/pack-operation-repository.js';
import { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import { cloneClawflows, createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { readPackScriptSnapshot, type PackScriptVersion } from './pack-script-snapshot.js';
import { withPreparedPackCandidate, PackCandidateConflictError, type PackCandidateChange, type PreparedPackCandidate } from './pack-candidate-save.js';
import { candidateProjection, completePackOperation, operationHash, operationReceipt, PackOperationError,
  reconcilePackOperation, remoteIdentity, recordUnknown, requestIdentity, scopedOperation, type OperationPayload } from './pack-operation-service.js';

type Context = { db: IDatabase; gitConfig: ClawFlowsGitConfig; gitAuthor: GitAuthor; workflowId: string };
export type SavePackVersionInput = Context & { actorId: string; requestId?: string; sourceVersion: PackScriptVersion;
  sourceCommit: string; expectedSavedHead: string | null; changes: PackCandidateChange[] };
export type SavedPackVersion = { ok: true; saved: true; requestId: string; workflowId: string; packId: string; branch: string; commit: string; version: number };
export async function readPackVersionFromRemote(input: Context & { packId: string; version: PackScriptVersion }) {
  const { gitConfig } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
  return withGitOperation({ workflowId: input.workflowId, operation: 'pack_version_read' }, async () => {
    const dir = await createTempRepo();
    try {
      await cloneClawflows(dir, gitConfig);
      return await readPackScriptSnapshot(dir, input.workflowId, input.packId, input.version, { includeVersionFiles: true });
    } finally { await removeTempRepo(dir); }
  });
}

/** Shared pipeline for file and canvas saves. The host supplies a verified editor actor. */
export async function persistPreparedSave(input: Context & { actorId: string; requestId?: string }, hashInput: unknown,
  prepare: (callback: (prepared: PreparedPackCandidate, push: () => Promise<unknown>) => Promise<SavedPackVersion>) => Promise<SavedPackVersion>,
  initialize?: (tx: IDatabase) => Promise<void>): Promise<SavedPackVersion> {
  if (!input.actorId?.trim()) throw new PackOperationError(403, 'FORBIDDEN');
  const requestId = requestIdentity(input.requestId, randomUUID);
  const repo = new PackOperationRepository(input.db);
  const identity = { requestId, workflowId: input.workflowId, actorId: input.actorId, kind: 'save' as const, inputHash: operationHash(hashInput) };
  let started = initialize && !await repo.get(requestId)
    ? await input.db.transaction(async tx => { await initialize(tx); return new PackOperationRepository(tx).begin(identity); })
    : await repo.begin(identity);
  if (!started.created && started.operation.phase === 'failed') started = await repo.restartFailed(identity, started.operation.attempt);
  if (!started.created) {
    if (started.operation.phase === 'completed') return JSON.parse(started.operation.resultJson!) as SavedPackVersion;
    throw new PackOperationError(409, 'OPERATION_IN_PROGRESS', requestId);
  }
  let pushed = false;
  let preparedGit: PreparedPackCandidate | undefined;
  try {
    return await prepare(async (git, push) => {
      preparedGit = git;
      const projection = candidateProjection(input.workflowId, git.content, git.manifestContent);
      const payload: OperationPayload = { git, ...projection, packId: git.packId, remoteHash: remoteIdentity(input.gitConfig),
        version: await new WorkflowDeployHistoryRepository(input.db).getLatestVersion(input.workflowId) + 1, note: null, preserveDraft: false };
      await repo.prepare(requestId, JSON.stringify(payload), started.operation.attempt);
      if (!(await repo.markPushing(requestId, started.operation.attempt)).claimed) throw new PackOperationError(409, 'OPERATION_IN_PROGRESS', requestId);
      pushed = true;
      await push();
      return await completePackOperation(input.db, (await repo.get(requestId))!) as SavedPackVersion;
    });
  } catch (error) {
    // The flag is set before any remote update. Never turn an uncertain push/DB acknowledgement into a clean failure.
    if (pushed) {
      await recordUnknown(input.db, requestId, started.operation.attempt);
      throw new PackOperationError(503, 'SAVE_OUTCOME_UNKNOWN', requestId, preparedGit?.branch, preparedGit?.commit);
    }
    await repo.failBeforePush(requestId, JSON.stringify({ code: 'PREPARATION_FAILED' }), started.operation.attempt);
    if (error instanceof PackCandidateConflictError) throw new PackOperationError(409, 'VERSION_CONFLICT', requestId);
    throw error;
  }
}
export async function savePackVersionToRemote(input: SavePackVersionInput): Promise<SavedPackVersion> {
  const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
  const context = { ...input, gitConfig, gitAuthor };
  return persistPreparedSave(context, { workflowId: input.workflowId, remote: remoteIdentity(gitConfig), sourceVersion: input.sourceVersion,
    sourceCommit: input.sourceCommit, expectedSavedHead: input.expectedSavedHead, changes: input.changes }, callback =>
    withPreparedPackCandidate({ workflowId: input.workflowId, sourceVersion: input.sourceVersion, sourceCommit: input.sourceCommit,
      expectedSavedHead: input.expectedSavedHead, changes: input.changes, config: gitConfig }, callback));
}
export async function readPackOperationFromRemote(input: Context & { requestId: string; actorId: string }) {
  return operationReceipt(await scopedOperation(input.db, input.requestId, input.workflowId, input.actorId));
}
export async function reconcilePackOperationFromRemote(input: Context & { requestId: string; actorId: string }) {
  const { gitConfig } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
  return reconcilePackOperation(input.db, gitConfig, input.requestId, input.workflowId, input.actorId);
}
