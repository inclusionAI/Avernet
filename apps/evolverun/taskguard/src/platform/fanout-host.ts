import type { ControllerDeps } from '../controller.js';
import type { IApiClient } from '../db/api-client/types.js';
import { FanoutRuntimeError } from '../executors/subworkflow-fanout-types.js';
import { FanoutApiPersistence } from './fanout-persistence.js';
import { createControllerFanoutRuntime, type FanoutChildExecutorFactory } from './controller-fanout-runtime.js';

/** Fail closed; the legacy permission repository intentionally fails open and is
 * therefore not suitable for starting durable child runs. This hook is installed
 * by the platform composition root, independently of attribution/template data. */
export async function authorizeFanoutExecution(api: IApiClient, botId: string | undefined, ownerId: string | undefined, workflowId: string): Promise<void> {
  if (!botId || !ownerId) throw new FanoutRuntimeError({category:'authorization',code:'CHILD_EXECUTION_DENIED',message:'Fanout execution requires an authenticated bot and owner'},'rejected');
  const response=await api.post<{allowed:boolean}>('/api/internal/bot-workflow-permissions/check',{
    botId,botOwnerId:ownerId,workflowId,permission:'execute',
  });
  if (!response.ok || response.data?.allowed !== true) throw new FanoutRuntimeError({category:'authorization',code:'CHILD_EXECUTION_DENIED',message:'Child workflow execution permission was denied or could not be established'},'rejected');
}

/** Explicit composition root attachment. Existing single-subworkflow behavior is unchanged. */
export function configureFanoutApiHost(deps: ControllerDeps, api: IApiClient, createChildExecutor: FanoutChildExecutorFactory): void {
  const store=new FanoutApiPersistence(api);
  deps.createFanoutRuntime=(parentState,parentFlowId,executeWorkflow)=>createControllerFanoutRuntime({
    deps,parentState,parentFlowId,store,executeWorkflow,createChildExecutor,
    authorizeChild:request=>authorizeFanoutExecution(api,deps.botId,deps.ownerId,request.childWorkflow.id),
  });
}
