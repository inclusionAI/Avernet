import type { ApiResponse, IApiClient } from '../db/api-client/types.js';
import type { FlowState } from '../types.js';
import type { FanoutChildRequest, FanoutState } from '../executors/subworkflow-fanout-types.js';

export type FanoutRunRecord = {
  flowId: string;
  workflowId: string;
  status: string;
  sessionKey: string;
  stateJson: string;
  revision: number;
};

export type FanoutOrigin = { originBotId?: string; userId?: string };

/** Durable operations: transport errors are never interpreted as absent state. */
export interface FanoutPersistence {
  readGroup(parentFlowId: string, nodeId: string): Promise<{group: FanoutState | null; parentStatus: string}>;
  saveGroup(group: FanoutState, expectedRevision: number | null, parentState: FlowState): Promise<void>;
  readChild(parentFlowId: string, childFlowId: string): Promise<FanoutRunRecord | null>;
  startChild(parentFlowId: string, request: FanoutChildRequest, stateJson: string, origin: FanoutOrigin): Promise<{created: boolean; run: FanoutRunRecord}>;
  checkpointChild(parentFlowId: string, childFlowId: string, expectedRevision: number, stateJson: string, status: string, currentStep?: string): Promise<FanoutRunRecord>;
}

export class FanoutPersistenceError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = 'FanoutPersistenceError'; }
}

function requireData<T>(response: ApiResponse<T>, operation: string): T {
  if (!response.ok || response.data == null) {
    throw new FanoutPersistenceError(`${operation}: HTTP ${response.status ?? 'unknown'} ${response.error ?? 'missing response'}`, response.status);
  }
  return response.data;
}

function validateRun(value: FanoutRunRecord): FanoutRunRecord {
  if (!value || typeof value.flowId !== 'string' || typeof value.workflowId !== 'string'
    || typeof value.status !== 'string' || typeof value.sessionKey !== 'string'
    || typeof value.stateJson !== 'string' || !Number.isSafeInteger(value.revision) || value.revision < 1) {
    throw new FanoutPersistenceError('Invalid durable child response');
  }
  return value;
}

const runPath = (id: string) => `/api/internal/runs/${encodeURIComponent(id)}`;
const childPath = (parent: string, child: string) => `${runPath(parent)}/fanout-children/${encodeURIComponent(child)}`;

/** IApiClient is injected by the composition root; no config/environment access here. */
export class FanoutApiPersistence implements FanoutPersistence {
  constructor(private readonly api: IApiClient) {}

  async readGroup(parentFlowId: string, nodeId: string): Promise<{group: FanoutState | null; parentStatus: string}> {
    const value = requireData(await this.api.get<{group: FanoutState | null; parentStatus: string}>(
      `${runPath(parentFlowId)}/fanouts/${encodeURIComponent(nodeId)}`,
    ), 'Read fanout');
    if (typeof value.parentStatus !== 'string' || !Object.hasOwn(value, 'group')) throw new FanoutPersistenceError('Invalid fanout response');
    return value;
  }

  async saveGroup(group: FanoutState, expectedRevision: number | null, parentState: FlowState): Promise<void> {
    const response = await this.api.put(`${runPath(group.parentFlowId)}/fanouts/${encodeURIComponent(group.groupNodeId)}`, {
      group, expectedRevision, ...(expectedRevision === null ? {parentState} : {}),
    });
    if (!response.ok) throw new FanoutPersistenceError(`Save fanout: HTTP ${response.status ?? 'unknown'} ${response.error ?? 'write rejected'}`, response.status);
  }

  async readChild(parentFlowId: string, childFlowId: string): Promise<FanoutRunRecord | null> {
    const response = await this.api.get<FanoutRunRecord>(childPath(parentFlowId, childFlowId));
    if (response.status === 404) return null;
    return validateRun(requireData(response, 'Read fanout child'));
  }

  async startChild(parentFlowId: string, request: FanoutChildRequest, stateJson: string, origin: FanoutOrigin): Promise<{created: boolean; run: FanoutRunRecord}> {
    const result = requireData(await this.api.put<{created: boolean; run: FanoutRunRecord}>(childPath(parentFlowId, request.childFlowId), {
      nodeId: request.parentNodeId,
      groupExecutionId: request.groupExecutionId,
      itemId: request.itemId,
      attemptNumber: request.attemptNumber,
      workflowId: request.childWorkflow.id,
      workflowTitle: request.childWorkflow.title,
      sessionKey: request.sessionKey,
      stateJson,
      ...origin,
    }), 'Start fanout child');
    if (typeof result.created !== 'boolean') throw new FanoutPersistenceError('Invalid child start response');
    return {created: result.created, run: validateRun(result.run)};
  }

  async checkpointChild(parentFlowId: string, childFlowId: string, expectedRevision: number, stateJson: string, status: string, currentStep?: string): Promise<FanoutRunRecord> {
    return validateRun(requireData(await this.api.put<FanoutRunRecord>(`${childPath(parentFlowId, childFlowId)}/state`, {
      expectedRevision, stateJson, status, ...(currentStep ? {currentStep} : {}),
    }), 'Checkpoint fanout child'));
  }
}
