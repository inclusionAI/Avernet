import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveAutoHealGitSettings } from '../config/auto-heal-git-config.js';
import { cloneClawflows, createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { readPackScriptSnapshot, type PackScriptSnapshot, type PackScriptVersion } from './pack-script-snapshot.js';

export type PackScriptReadInput = {
  db: IDatabase;
  gitConfig: ClawFlowsGitConfig;
  gitAuthor: GitAuthor;
  workflowId: string;
  packId: string;
  version?: PackScriptVersion;
  includeVersionFiles?: boolean;
};

// These fixed reader errors are needed by the caller to distinguish conflicts
// and missing snapshots. Never propagate arbitrary filesystem or provider errors.
const safeMessages = new Set([
  'Workflow ref conflict: histories diverged', 'Pack manifest conflict',
  'Workflow snapshot not found', 'Pack manifest not found', 'Shared pack requires separate authorization',
]);
const safeCodes = new Set(['GIT_OPERATION_TIMEOUT', 'GIT_TIMEOUT', 'GIT_QUEUE_FULL', 'GIT_OUTPUT_LIMIT']);

/** Read a snapshot for a caller that has already checked authorization. */
export async function readPackScriptsFromRemote(input: PackScriptReadInput): Promise<PackScriptSnapshot> {
  try {
    const { gitConfig } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
    return await withGitOperation({ workflowId: input.workflowId, operation: 'pack_script_read' }, async () => {
      const repoDir = await createTempRepo();
      try {
        await cloneClawflows(repoDir, gitConfig);
        return input.includeVersionFiles
          ? await readPackScriptSnapshot(repoDir, input.workflowId, input.packId, input.version, { includeVersionFiles: true })
          : await readPackScriptSnapshot(repoDir, input.workflowId, input.packId, input.version);
      } finally {
        await removeTempRepo(repoDir);
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : '';
    if (safeMessages.has(message)) throw new Error(message);
    const code = (error as { code?: unknown } | null)?.code;
    if (typeof code === 'string' && safeCodes.has(code)) throw Object.assign(new Error(code), { code });
    throw new Error('Pack script snapshot unavailable');
  }
}
