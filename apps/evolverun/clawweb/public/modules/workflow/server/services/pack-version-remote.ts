import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveAutoHealGitSettings } from '../config/auto-heal-git-config.js';
import { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import { createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { fetchPackVersionRefs, preparePackVersionRepo } from './pack-version-git-read.js';
import { packVersionPhase } from './pack-version-observability.js';
import { readPackScriptSnapshot, type PackScriptVersion } from './pack-script-snapshot.js';
import { withPreparedPackCandidate, PackCandidateConflictError, PackWorkflowContentChangedError, type PackCandidateChange } from './pack-candidate-save.js';
import { PackOperationError, requestIdentity } from './pack-operation-service.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';

type Context = { db: IDatabase; gitConfig: ClawFlowsGitConfig; gitAuthor: GitAuthor; workflowId: string };
export type SavePackVersionInput = Context & { actorId: string; requestId?: string; sourceVersion: PackScriptVersion;
  sourceCommit: string; expectedSavedHead: string | null; changes: PackCandidateChange[] };
export type SavedPackVersion = { ok: true; saved: true; requestId: string; workflowId: string; packId: string; branch: string; commit: string; version: number };
export async function readPackVersionFromRemote(input: Context & { packId: string; version: PackScriptVersion }) {
  const { gitConfig } = await packVersionPhase('git_config', () => resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor));
  return withGitOperation({ workflowId: input.workflowId, operation: 'pack_version_read' }, async () => {
    const dir = await packVersionPhase('git_prepare', async () => {
      const temp = await createTempRepo();
      try { await preparePackVersionRepo(temp, gitConfig, input.workflowId); return temp; }
      catch (error) { await removeTempRepo(temp); throw error; }
    });
    try {
      const verifyRefs = await packVersionPhase('git_fetch', () => fetchPackVersionRefs(dir, gitConfig, input.workflowId));
      try {
        return await packVersionPhase('snapshot', () => readPackScriptSnapshot(dir, input.workflowId, input.packId, input.version, { includeVersionFiles: true }));
      } finally {
        await packVersionPhase('git_verify', verifyRefs);
      }
    } finally { await removeTempRepo(dir); }
  });
}

/** Saves version files to Git without a database operation journal. Callers must
 * treat an uncertain push as unknown; a request ID is correlation only, not
 * durable idempotency or a recovery receipt. */
export async function savePackVersionToRemote(input: SavePackVersionInput): Promise<SavedPackVersion> {
  if (!input.actorId?.trim()) throw new PackOperationError(403, 'FORBIDDEN');
  const requestId = requestIdentity(input.requestId, randomUUID);
  await assertNoUnresolvedLegacyPackOperation(input.db, input.workflowId);
  const { gitConfig } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
  // Read version before pushing: a later database failure must not be presented
  // as a definite Git failure. This value is informational, not a reservation.
  const version = await new WorkflowDeployHistoryRepository(input.db).getLatestVersion(input.workflowId) + 1;
  try {
    return await withPreparedPackCandidate({ workflowId: input.workflowId, sourceVersion: input.sourceVersion,
      sourceCommit: input.sourceCommit, expectedSavedHead: input.expectedSavedHead, changes: input.changes, config: gitConfig, preserveWorkflowContent: true },
    async (git, push) => {
      // Re-read after candidate preparation, immediately before pushing. Legacy
      // NULL pack_id means workflow_id, as it does for Pack reads; it does not
      // authorize an arbitrary rename or cause an implicit database update.
      const rows = await input.db.query<{ workflow_id: string; pack_id: string | null }>(
        'SELECT workflow_id, pack_id FROM workflow_specs WHERE workflow_id = ?', [input.workflowId]);
      const row = rows[0];
      if (!row || row.workflow_id !== input.workflowId) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND', requestId);
      if ((row.pack_id ?? row.workflow_id) !== git.packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH', requestId);
      await push();
      return { ok: true, saved: true, requestId, workflowId: input.workflowId, packId: git.packId,
        branch: git.branch, commit: git.commit, version };
    });
  } catch (error) {
    if (error instanceof PackWorkflowContentChangedError) throw new PackOperationError(400, 'WORKFLOW_YAML_REQUIRES_DB_SAVE', requestId);
    if (error instanceof PackCandidateConflictError) throw new PackOperationError(409, 'VERSION_CONFLICT', requestId);
    throw error;
  }
}

/** No durable journal exists; do not claim status or reconciliation support. */
export async function readPackOperationFromRemote(_input: Context & { requestId: string; actorId: string }): Promise<never> {
  throw new PackOperationError(503, 'OPERATION_RECOVERY_UNAVAILABLE');
}
export async function reconcilePackOperationFromRemote(_input: Context & { requestId: string; actorId: string }): Promise<never> {
  throw new PackOperationError(503, 'OPERATION_RECOVERY_UNAVAILABLE');
}
