import type { TaskEscortWorkflowSummary } from '@/domain/taskEscortParity/types';
import { getWorkspaceWorkflowTypes } from '@/services/backendApi/harnessFlow/workspaceController';

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null);

/** Validate untrusted envelopes and required IDs; optional metadata remains explicitly unknown. */
export async function readTaskEscortWorkflows(
  userId?: string,
  signal?: AbortSignal,
): Promise<TaskEscortWorkflowSummary[]> {
  const response: unknown = await getWorkspaceWorkflowTypes(userId, signal);
  if (!response || typeof response !== 'object' || !('workflows' in response) || !Array.isArray(response.workflows))
    throw new Error('工作流列表响应格式无效');
  const seen = new Set<string>();
  return response.workflows.map((row: unknown) => {
    if (!row || typeof row !== 'object' || !('workflow_id' in row)) throw new Error('工作流列表缺少有效 ID');
    const id = text(row.workflow_id);
    if (!id || seen.has(id)) throw new Error('工作流列表包含无效或重复 ID');
    seen.add(id);
    const fields = row as Record<string, unknown>;
    return {
      id,
      title: text(fields.workflow_title) ?? id,
      runCount:
        typeof fields.run_count === 'number' && Number.isSafeInteger(fields.run_count) && fields.run_count >= 0
          ? fields.run_count
          : null,
      lastStatus: text(fields.last_status),
      ownerId: text(fields.owner_id),
    };
  });
}
