import { taskEscortService } from '@/services/taskEscort';
import type { TaskEscortWorkspaceApi } from './types';

/** Only reads are exposed. Importing this adapter does not issue a request. */
export const defaultWorkspaceApi: TaskEscortWorkspaceApi = {
  listWorkflows: () => taskEscortService.listWorkflows(),
  listFlowRuns: (params) => taskEscortService.listFlowRuns(params),
  getFlowRun: (flowId) => taskEscortService.getFlowRun(flowId),
};
