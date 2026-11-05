import { createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import type { ControllerDeps, executeWorkflowForTest } from '../controller.js';
import type { FlowState, WorkflowNode, WorkflowSpec, ExecutorResult } from '../types.js';
import { buildChildFlowState } from '../executors/subworkflow.js';
import { FanoutRuntimeError } from '../executors/subworkflow-fanout-types.js';
import type { FanoutRuntime, FanoutChildRequest, FanoutChildCompletion, FanoutError } from '../executors/subworkflow-fanout-types.js';
import { FanoutPersistenceError, type FanoutPersistence, type FanoutRunRecord } from './fanout-persistence.js';
import { DurableFanoutTaskFlow } from './fanout-taskflow.js';

export type FanoutChildExecutorContext = {
  sessionKey: string;
  workflow: WorkflowSpec;
  signal: AbortSignal;
  getSubworkflowDeps: () => ControllerDeps['subworkflowDeps'];
};
export type FanoutChildExecutorFactory = (context: FanoutChildExecutorContext) => ControllerDeps['executeNode'];
export type ControllerFanoutRuntimeOptions = {
  deps: ControllerDeps;
  parentState: FlowState;
  parentFlowId: string;
  store: FanoutPersistence;
  executeWorkflow: typeof executeWorkflowForTest;
  createChildExecutor: FanoutChildExecutorFactory;
  authorizeChild: (request: FanoutChildRequest) => Promise<void>;
  pollIntervalMs?: number;
};
type ChildState = FlowState & {
  _fanoutRevision?: number;
  _fanoutStarted?: boolean;
  _fanoutRequestDigest?: string;
  _fanoutCancelRequested?: boolean;
  _fanoutNodeResults?: Record<string, ExecutorResult>;
};
type OwnedChild = {digest: string; controller: AbortController; promise: Promise<FanoutChildCompletion>};
const ownedChildren = new Map<string, OwnedChild>();
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out']);
const confirmedCategories = new Set(['business', 'execution']);

function parsed(record: FanoutRunRecord): ChildState {
  const state: unknown = JSON.parse(record.stateJson);
  if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error('Invalid durable child state');
  return state as ChildState;
}
function unknown(message: string): FanoutRuntimeError {
  return new FanoutRuntimeError({category:'orchestration',code:'CHILD_OUTCOME_UNKNOWN',message,retryable:false},'unknown');
}
function persistenceUnknown(error: unknown): FanoutRuntimeError {
  const message = error instanceof Error ? error.message : String(error);
  return new FanoutRuntimeError({
    category: 'persistence',
    code: 'CHILD_OUTCOME_PERSISTENCE_UNKNOWN',
    message: `Child outcome persistence is uncertain: ${message}`,
    retryable: false,
  }, 'unknown');
}
function requestDigest(request: FanoutChildRequest): string {
  return createHash('sha256').update(JSON.stringify(request)).digest('hex');
}
function isCapacityBusy(error: unknown): boolean {
  return error instanceof FanoutPersistenceError && error.status === 409 && /(?:^|\s|:)CAPACITY_BUSY(?:$|\s)/.test(error.message);
}
function isTransientPersistence(error: unknown): boolean {
  return error instanceof FanoutPersistenceError && (error.status === 500 || error.status === 503);
}
function startUnknown(message: string): FanoutRuntimeError {
  return new FanoutRuntimeError({category:'persistence',code:'CHILD_START_UNKNOWN',message,retryable:true},'unknown');
}
function validateIdentity(record: FanoutRunRecord, request: FanoutChildRequest): ChildState {
  const state = parsed(record);
  if (record.flowId !== request.childFlowId || record.workflowId !== request.childWorkflow.id
    || record.sessionKey !== request.sessionKey || state.subworkflowMeta?.parentFlowId !== request.parentFlowId
    || state.subworkflowMeta?.fanout?.groupExecutionId !== request.groupExecutionId
    || state._fanoutRequestDigest !== requestDigest(request)) {
    throw new FanoutRuntimeError({category:'orchestration',code:'CHILD_IDENTITY_CONFLICT',message:'Child identity or input digest conflict'},'rejected');
  }
  return state;
}
function completion(record: FanoutRunRecord, request: FanoutChildRequest): FanoutChildCompletion | null {
  const state = validateIdentity(record, request);
  if (!terminal.has(record.status)) return null;
  if (record.status === 'cancelled' || record.status === 'timed_out') return {status: record.status};
  if (record.status === 'succeeded') return {status:'succeeded', outputs: state.workflowData?.outputs as Record<string,unknown> | undefined};
  const failed = Object.entries(state._fanoutNodeResults ?? {})
    .filter(([, result]) => result.status === 'failed');
  if (failed.length === 0) throw unknown('Child failed without a recorded failed node result');

  const confirmed = failed.map(([nodeId, result]) => {
    const explicit = result.result?.fanoutError;
    if (!explicit || typeof explicit !== 'object' || Array.isArray(explicit)) return null;
    const detail = explicit as Record<string, unknown>;
    if (detail.acceptanceKnown !== true || !confirmedCategories.has(String(detail.category))
      || typeof detail.code !== 'string' || typeof detail.message !== 'string') return null;
    return {
      category: detail.category as 'business' | 'execution',
      code: detail.code,
      message: detail.message,
      retryable: detail.retryable === true,
      sourceNodeId: nodeId,
    } satisfies FanoutError;
  });

  // A local script exiting does not prove a remote experiment was not accepted.
  // Every failed/side-effecting node must be confirmed before this child can
  // converge to a confirmed failure; one unknown result keeps the child in
  // reconciliation and prevents the parent from releasing its barrier.
  if (confirmed.some(error => error === null)) {
    const unknownNodes = failed
      .filter(([, _result], index) => confirmed[index] === null)
      .map(([nodeId, result]) => `${nodeId}: ${result.error ?? 'acceptance unknown'}`)
      .join('; ');
    throw unknown(unknownNodes || 'Child failed without a confirmed action outcome');
  }
  const firstConfirmed = confirmed[0];
  if (!firstConfirmed) throw unknown('Child failed without a confirmed action outcome');
  return {status: 'failed', error: firstConfirmed};
}

export function createControllerFanoutRuntime(options: ControllerFanoutRuntimeOptions): FanoutRuntime {
  const {deps,parentState,parentFlowId,store,executeWorkflow,createChildExecutor} = options;
  const interval = options.pollIntervalMs ?? 250;

  async function observe(request: FanoutChildRequest, signal: AbortSignal): Promise<FanoutChildCompletion> {
    for (;;) {
      const record = await store.readChild(parentFlowId,request.childFlowId);
      if (record) {
        const done=completion(record,request);
        if (done) return done;
      }
      if (signal.aborted) throw unknown('Observation stopped before child termination was confirmed');
      await delay(interval,undefined,{signal}).catch(() => undefined);
    }
  }

  async function executeOwned(request: FanoutChildRequest, record: FanoutRunRecord, controller: AbortController): Promise<FanoutChildCompletion> {
    const state = parsed(record);
    const adapter = new DurableFanoutTaskFlow(store,parentFlowId,record,deps.boundTaskFlow);
    const childDeps: ControllerDeps = {...deps,sessionKey:request.sessionKey,sessionId:undefined,
      boundTaskFlow:adapter,abortSignal:controller.signal,subworkflowDeps:undefined};
    const dispatch = createChildExecutor({sessionKey:request.sessionKey,workflow:request.childWorkflow,
      signal:controller.signal,getSubworkflowDeps:()=>childDeps.subworkflowDeps});
    let postSideEffectPersistenceUncertain: FanoutRuntimeError | undefined;
    childDeps.executeNode = async (node,ctx,current,flowId) => {
      if (controller.signal.aborted) throw unknown('Child cancellation requested before node dispatch');
      const currentState = current as ChildState;
      await adapter.checkpoint(currentState); // write-ahead intent before any external side effect
      const result = await dispatch(node,ctx,current,flowId);
      currentState._fanoutNodeResults ??= {};
      // Do not persist Error objects or other non-JSON transport internals.
      currentState._fanoutNodeResults[node.id] = {status:result.status,result:result.result,error:result.error};
      try {
        await adapter.checkpoint(currentState);
      } catch (error) {
        // The external action has already run, but its durable outcome is
        // uncertain. Return waiting rather than throwing into the controller's
        // ordinary retry loop, which would redispatch the same side effect.
        postSideEffectPersistenceUncertain = persistenceUnknown(error);
        return {
          status: 'waiting',
          waitConfig: {
            waitKind: 'fanout-reconciliation',
            prompt: postSideEffectPersistenceUncertain.message,
          },
          rawError: postSideEffectPersistenceUncertain,
        };
      }
      return result;
    };
    await mkdir(state.fanoutRuntimeContext!.stateDir,{recursive:true});
    try {
      await executeWorkflow({deps:childDeps,workflow:request.childWorkflow,state,flowId:request.childFlowId,revision:1,runStartHooks:true});
    } catch (error) {
      // A failed waiting-state write can itself be the lost acknowledgement of
      // the post-side-effect checkpoint. Once that boundary is uncertain, only
      // durable observation is safe; never re-enter controller execution.
      if (postSideEffectPersistenceUncertain) return observe(request,controller.signal);
      throw error;
    }
    const final=await store.readChild(parentFlowId,request.childFlowId);
    if (!final) throw unknown('Child run disappeared after execution');
    // An aborted observer or a controller that returned "waiting" is not a
    // remote termination acknowledgement. Only a durable terminal fact may
    // release this child; otherwise preserve uncertainty/cleanup responsibility.
    const done=completion(final,request);
    return done ?? await observe(request,controller.signal);
  }

  async function startOrGetChild(request: FanoutChildRequest, signal: AbortSignal): Promise<FanoutChildCompletion> {
    const digest=requestDigest(request);
    const existing=ownedChildren.get(request.childFlowId);
    if (existing) {
      if (existing.digest!==digest) throw new Error('Child input digest conflict');
      if (signal.aborted) return observe(request,signal);
      return existing.promise;
    }
    const known=await store.readChild(parentFlowId,request.childFlowId);
    if (known) {
      const done=completion(known,request);
      if(done) return done;
      if(parsed(known)._fanoutStarted) return observe(request,signal);
    } else if(signal.aborted) {
      // The durable group cancellation fences late creates in the persistence port.
      return {status:'cancelled'};
    }
    if(signal.aborted) return observe(request,signal);
    await options.authorizeChild(request);
    const params=Object.fromEntries(Object.entries(request.params).map(([k,v])=>[k,typeof v==='string'?v:JSON.stringify(v)]));
    const state=buildChildFlowState(request.childWorkflow,params,parentState,request.parentNodeId,parentFlowId,request.depth) as ChildState;
    state.workflowSnapshot=structuredClone(request.childWorkflow);
    state.subworkflowDependencies=structuredClone(request.childWorkflow.subworkflowDependencies);
    state.runInitiator=structuredClone(parentState.runInitiator ?? {...deps.user,source:"delivery-context" as const});
    state.subworkflowMeta!.fanout={groupExecutionId:request.groupExecutionId,itemId:request.itemId,itemIndex:request.itemIndex,
      attemptNumber:request.attemptNumber,childFlowId:request.childFlowId};
    const directory=join(deps.workflowLogDir ?? deps.skillRoot,'.taskguard-runs',createHash('sha256').update(request.childFlowId).digest('hex').slice(0,32));
    state.fanoutRuntimeContext={sessionKey:request.sessionKey,stateDir:directory,
      launchIdempotencyKey:`${parentFlowId}:${request.groupExecutionId}:${request.itemId}`};
    state._fanoutRequestDigest=digest;state._fanoutRevision=1;state._fanoutStarted=false;
    const origin = {
      originBotId:parentState.originBotId ?? (deps.botId && deps.ownerId ? `${deps.botId}:${deps.ownerId}`:undefined),
      userId:parentState.runInitiator?.id ?? deps.user?.id,
    };
    let response: {created: boolean; run: FanoutRunRecord};
    if (known) {
      response = {created:false,run:known};
    } else {
      let capacityAttempts = 0;
      let persistenceAttempts = 0;
      for (;;) {
        if (signal.aborted) return {status:'cancelled'};
        try {
          response = await store.startChild(parentFlowId,request,JSON.stringify(state),origin);
          break;
        } catch (error) {
          // CAPACITY_BUSY is the one admission response that is safe to wait
          // on: the server guarantees no row was created. Retry the exact same
          // durable child ID, never a new item/attempt identity.
          if (isCapacityBusy(error)) {
            if (++capacityAttempts > 8) throw startUnknown('Fanout host capacity remained full while starting the child');
            const admission = await store.readGroup(parentFlowId, request.parentNodeId);
            const parentStatus = admission.parentStatus.toLowerCase();
            if (parentStatus === 'cancelled' || parentStatus === 'cancelling' || admission.group?.status === 'cancelling') return {status:'cancelled'};
            if (admission.group && Date.now() >= Date.parse(admission.group.deadlineAt)) return {status:'timed_out'};
            const backoff = Math.min(250, 25 * 2 ** (capacityAttempts - 1));
            try { await delay(backoff, undefined, {signal}); }
            catch { return {status:'cancelled'}; }
            continue;
          }
          // A transport/5xx response may have arrived after the server
          // inserted the row. Re-read before retrying, then retry only the same
          // idempotent request when absence is authoritative.
          if (isTransientPersistence(error)) {
            if (++persistenceAttempts > 3) throw startUnknown('Child start persistence remained unavailable after bounded retries');
            let current: FanoutRunRecord | null;
            try {
              current = await store.readChild(parentFlowId, request.childFlowId);
            } catch (readError) {
              throw startUnknown(`Unable to reconcile child start after a persistence failure: ${readError instanceof Error ? readError.message : String(readError)}`);
            }
            if (current) { response = {created:false,run:current}; break; }
            if (signal.aborted) return {status:'cancelled'};
            try { await delay(Math.min(250, 50 * 2 ** persistenceAttempts), undefined, {signal}); }
            catch { return {status:'cancelled'}; }
            continue;
          }
          throw error;
        }
      }
    }
    const durable=validateIdentity(response.run,request);
    if(durable._fanoutStarted) return observe(request,signal);
    durable._fanoutStarted=true;
    let claimed: FanoutRunRecord;
    try { claimed=await store.checkpointChild(parentFlowId,request.childFlowId,response.run.revision,JSON.stringify(durable),'running'); }
    catch(error) {
      const current=await store.readChild(parentFlowId,request.childFlowId);
      if(current && validateIdentity(current,request)._fanoutStarted) return observe(request,signal);
      throw error;
    }
    const controller=new AbortController();
    const abort=()=>controller.abort(signal.reason);
    signal.addEventListener('abort',abort,{once:true});
    if(signal.aborted) abort();
    const promise=executeOwned(request,claimed,controller).finally(()=>{signal.removeEventListener('abort',abort);ownedChildren.delete(request.childFlowId);});
    ownedChildren.set(request.childFlowId,{digest,controller,promise});
    return promise;
  }

  return {
    load:async(flowId,nodeId)=>(await store.readGroup(flowId,nodeId)).group,
    save:async(state,expected)=>{await store.saveGroup(state,expected,parentState);parentState.subworkflowFanouts??={};parentState.subworkflowFanouts[state.groupNodeId]=structuredClone(state);},
    parentStatus:async()=> (await store.readGroup(parentFlowId,Object.keys(parentState.subworkflowFanouts ?? {})[0] ?? '__parent__')).parentStatus,
    startOrGetChild,
    cancelChild:async request=>{
      ownedChildren.get(request.childFlowId)?.controller.abort(new Error('Fanout cancellation requested'));
      // Cross-process owners see parent/group cancellation at their admission and
      // checkpoint boundaries. A request is not a confirmation of remote termination.
      const record=await store.readChild(parentFlowId,request.childFlowId);
      if(!record || terminal.has(record.status)) return;
      const state=validateIdentity(record,request);state._fanoutCancelRequested=true;
      await store.checkpointChild(parentFlowId,request.childFlowId,record.revision,JSON.stringify(state),record.status);
    },
  };
}
