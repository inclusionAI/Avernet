import type { FlowEventInsert } from '../event-repository.js';
export function manualAuditEvent(extra: Partial<FlowEventInsert> = {}): FlowEventInsert {
  return {id:'aistudio_manual_watch-1_200',flowId:'flow-1',workflowId:'wf-1',nodeId:'train',type:'node_progress',attempt:1,time:201,
    data:{source:'aistudio-train',manual:true,pollSource:'manual',userId:'owner',watchId:'watch-1',requestedAt:200,rawStatus:'running'},...extra};
}
