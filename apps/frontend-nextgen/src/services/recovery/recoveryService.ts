import { recoveryApi } from '@/services/backendApi/clawinsight/recoveryController';

export const recoveryService = {
  list: (params: Omit<Parameters<typeof recoveryApi.list>[0], 'includePresentation'>, signal?: AbortSignal) =>
    recoveryApi.list({ ...params, includePresentation: 'true' }, signal),
  detail: (taskId: string, signal?: AbortSignal) => recoveryApi.detail(taskId, { includeDiagnosis: 'true' }, signal),
  bots: (spaceId: string, q = '', cursor?: string, signal?: AbortSignal) =>
    recoveryApi.bots({ spaceId, scope: 'mine', q, limit: 20, start: 'all', end: 'all', cursor }, signal),
  configure: recoveryApi.configure,
  decide: recoveryApi.decide,
  confirmOwner: recoveryApi.confirmOwner,
  terminate: recoveryApi.terminate,
};
