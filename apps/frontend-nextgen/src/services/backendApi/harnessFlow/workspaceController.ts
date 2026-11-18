/** Workflow workspace protocol only. Authorization remains with the existing Gateway adapter. */
import { backendRequest } from '../httpClient';

export interface WorkspaceWorkflowTypeDto {
  workflow_id: string;
  workflow_title?: string | null;
  run_count?: number | null;
  last_status?: string | null;
  owner_id?: string | null;
}
export interface WorkspaceWorkflowTypesDto {
  workflows: WorkspaceWorkflowTypeDto[];
  total?: number;
}

export function getWorkspaceWorkflowTypes(
  botOwnerId?: string,
  signal?: AbortSignal,
): Promise<WorkspaceWorkflowTypesDto> {
  return backendRequest('/openapi/v1/harnessflow/api/runs/workflow-types', {
    method: 'GET',
    params: { limit: 500, ...(botOwnerId ? { botOwnerId } : {}) },
    signal,
  });
}
