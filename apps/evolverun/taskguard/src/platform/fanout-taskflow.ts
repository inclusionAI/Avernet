import type { ControllerDeps } from '../controller.js';
import type { FlowState } from '../types.js';
import { canonicalWorkflowJson } from '../workflow-dependencies.js';
import { FanoutPersistenceError, type FanoutPersistence, type FanoutRunRecord } from './fanout-persistence.js';

type TaskFlow = ControllerDeps['boundTaskFlow'];
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'completed']);

function objectState(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Child state must be a JSON object');
  return value as Record<string, unknown>;
}

/**
 * A managed child uses the durable run row as its authority, not the legacy
 * process-local TaskFlow cache. The controller's token counts its transitions;
 * storage revision also counts write-ahead node checkpoints. They are deliberately
 * separate, with every underlying write still protected by the durable CAS.
 */
export class DurableFanoutTaskFlow implements TaskFlow {
  private engineRevision = 1;
  private writes: Promise<unknown> = Promise.resolve();
  private record: FanoutRunRecord;

  constructor(
    private readonly store: FanoutPersistence,
    private readonly parentFlowId: string,
    record: FanoutRunRecord,
    private readonly fallback: TaskFlow,
  ) { this.record = structuredClone(record); }

  private enqueue<T>(work: () => Promise<T>): Promise<T> {
    const result = this.writes.then(work);
    this.writes = result.catch(() => undefined);
    return result;
  }

  private asFlow(): Record<string, unknown> {
    const state = objectState(this.record.stateJson);
    return { ...this.record, revision: this.engineRevision, currentStep: state.currentPhase ?? 'start' };
  }

  private async commit(state: Record<string, unknown>, status: string, currentStep?: string): Promise<void> {
    if (terminal.has(this.record.status) && status !== this.record.status) throw new Error('Child terminal state cannot be resurrected');
    const priorRecord = this.record;
    const prior = objectState(priorRecord.stateJson);
    const buildMerged = (base: Record<string, unknown>, cancelRequested: boolean) => ({
      ...base, ...state,
      subworkflowMeta: prior.subworkflowMeta,
      fanoutRuntimeContext: prior.fanoutRuntimeContext,
      runInitiator: prior.runInitiator,
      _fanoutRevision: this.record.revision,
      ...(cancelRequested ? {_fanoutCancelRequested: true} : {}),
    });
    const merged = buildMerged(prior, prior._fanoutCancelRequested === true);
    try {
      this.record = await this.store.checkpointChild(this.parentFlowId, priorRecord.flowId,
        priorRecord.revision, JSON.stringify(merged), status, currentStep);
      return;
    } catch (error) {
      // Cancellation intentionally bumps the durable revision to fence new work.
      // It is safe to retry a known completion exactly once when that was the
      // only concurrent mutation; never merge an unrelated newer state.
      if (!(error instanceof FanoutPersistenceError) || error.status !== 409) throw error;
      const fresh = await this.store.readChild(this.parentFlowId, priorRecord.flowId);
      if (!fresh || fresh.status !== priorRecord.status || fresh.revision !== priorRecord.revision + 1) throw error;
      const freshState = objectState(fresh.stateJson);
      const stripIntent = (value: Record<string, unknown>) => {
        const copy = {...value};
        delete copy._fanoutRevision;
        delete copy._fanoutCancelRequested;
        return copy;
      };
      if (freshState._fanoutCancelRequested !== true
        || canonicalWorkflowJson(stripIntent(freshState)) !== canonicalWorkflowJson(stripIntent(prior))) throw error;
      this.record = fresh;
      const retry = buildMerged(freshState, true);
      this.record = await this.store.checkpointChild(this.parentFlowId, fresh.flowId,
        fresh.revision, JSON.stringify(retry), status, currentStep);
    }
  }

  /** Persist execution intent/results without changing the controller token. */
  checkpoint(state: FlowState | Record<string, unknown>): Promise<void> {
    const snapshot = structuredClone(state) as Record<string, unknown>;
    return this.enqueue(() => this.commit(snapshot, this.record.status));
  }

  private transition(params: Record<string, unknown>, status: string): Promise<{applied: boolean; flow: Record<string, unknown>}> {
    const input = typeof params.stateJson === 'string' ? objectState(params.stateJson) : {};
    const snapshot = structuredClone(input);
    return this.enqueue(async () => {
      const expected = params.expectedRevision;
      if (expected !== undefined && expected !== this.engineRevision) throw new Error(`状态更新冲突，请重试 (expected ${String(expected)}, current ${this.engineRevision})`);
      await this.commit(snapshot, status, typeof params.currentStep === 'string' ? params.currentStep : undefined);
      this.engineRevision++;
      return {applied: true, flow: this.asFlow()};
    });
  }

  async get(token: string): Promise<Record<string, unknown> | null> {
    if (token !== this.record.flowId) return this.fallback.get(token);
    await this.writes;
    const fresh = await this.store.readChild(this.parentFlowId, token);
    if (!fresh) return null;
    this.record = fresh;
    return this.asFlow();
  }

  async createManaged(params: Record<string, unknown>): Promise<Record<string, unknown>> { return this.fallback.createManaged(params); }
  async setWaiting(params: Record<string, unknown>) {
    if (params.flowId !== this.record.flowId) return this.fallback.setWaiting(params);
    return this.transition(params, 'waiting');
  }
  async resume(params: Record<string, unknown>) {
    if (params.flowId !== this.record.flowId) return this.fallback.resume(params);
    return this.transition(params, typeof params.status === 'string' ? params.status : 'running');
  }
  async finish(params: Record<string, unknown>): Promise<{applied: boolean; flow: Record<string, unknown>} | unknown> {
    if (params.flowId !== this.record.flowId) return this.fallback.finish(params);
    return this.transition(params, 'succeeded');
  }
  async fail(params: Record<string, unknown>): Promise<unknown> {
    if (params.flowId !== this.record.flowId) return this.fallback.fail(params);
    return this.transition(params, typeof params.status === 'string' ? params.status : 'failed');
  }
  async list(): Promise<{flows: Array<Record<string, unknown>>}> {
    const flow = await this.get(this.record.flowId); return {flows: flow ? [flow] : []};
  }
  async findLatest(): Promise<Record<string, unknown> | null> { return this.get(this.record.flowId); }
  async runTask(params: Record<string, unknown>): Promise<Record<string, unknown>> { return this.fallback.runTask(params); }
}
