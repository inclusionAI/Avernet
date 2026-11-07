import { startWorkflowBackgroundRun } from '@avernet/clawweb-shared/server/services/workflow-background-log';

type Phase = 'git_config' | 'git_prepare' | 'git_fetch' | 'snapshot' | 'git_verify';
const gitFailures = new Set(['GIT_OPERATION_TIMEOUT', 'GIT_QUEUE_FULL', 'GIT_TIMEOUT', 'GIT_OUTPUT_LIMIT', 'GIT_COMMAND_FAILED', 'PACK_REFS_CHANGED']);

/** Fixed metadata only: never log provider errors, input values or command arguments. */
export async function packVersionPhase<T>(phase: Phase, action: () => Promise<T>): Promise<T> {
  const end = startWorkflowBackgroundRun('pack_version_read_phase', 'pack_version_read', { phase });
  try {
    const result = await action();
    end('succeeded');
    return result;
  } catch (error) {
    const code = (error as { code?: unknown } | null)?.code;
    const message = error instanceof Error ? error.message : '';
    const errorCode = typeof code === 'string' && gitFailures.has(code) ? code
      : message === 'Workflow snapshot not found' || message === 'Pack manifest not found' ? 'SNAPSHOT_NOT_FOUND'
      : 'PACK_VERSION_STAGE_FAILED';
    end('failed', { errorCode });
    throw error;
  }
}
