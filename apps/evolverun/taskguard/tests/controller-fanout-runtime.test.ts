import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { executeWorkflowForTest, type ControllerDeps } from '../src/controller.js';
import { createActionRegistry } from '../src/actions/registry.js';
import { DatabaseTaskFlowAdapter } from '../src/platform/database-taskflow.js';
import { createControllerFanoutRuntime } from '../src/platform/controller-fanout-runtime.js';
import { FanoutPersistenceError, type FanoutPersistence, type FanoutRunRecord } from '../src/platform/fanout-persistence.js';
import type { FanoutChildRequest } from '../src/executors/subworkflow-fanout-types.js';
import type { FlowState, ExecutorResult } from '../src/types.js';
import { setCachedConfig } from '../src/config/config-cache.js';
import { defaults } from '../src/config/types.js';
setCachedConfig({database:{type:'sqlite',sqlitePath:':memory:'},app:structuredClone(defaults)});

function memoryStore() {
  const runs = new Map<string,FanoutRunRecord>();
  let parentStatus = 'running';
  const store: FanoutPersistence = {
    readGroup: async()=>({group:null,parentStatus}),
    saveGroup: async()=>{},
    readChild: async(_p,id)=>structuredClone(runs.get(id) ?? null),
    startChild: async(_p,request,stateJson)=>{
      if(runs.has(request.childFlowId)) return {created:false,run:structuredClone(runs.get(request.childFlowId)!)};
      const run={flowId:request.childFlowId,workflowId:request.childWorkflow.id,status:'running',sessionKey:request.sessionKey,stateJson,revision:1};
      runs.set(run.flowId,run);return {created:true,run:structuredClone(run)};
    },
    checkpointChild: async(_p,id,expected,stateJson,status)=>{
      const old=runs.get(id)!; if(old.revision!==expected) throw new Error('CAS conflict');
      const state=JSON.parse(stateJson); state._fanoutRevision=expected+1;
      const run={...old,stateJson:JSON.stringify(state),status,revision:expected+1}; runs.set(id,run);return structuredClone(run);
    },
  };
  return {store,runs,cancelParent:()=>{parentStatus='cancelled';}};
}
function request(): FanoutChildRequest {
  return {parentFlowId:'parent',parentNodeId:'fanout',groupExecutionId:'g-1',itemId:'scene/p',itemIndex:0,attemptNumber:1,childFlowId:'child-1',sessionKey:'fanout:child-1',stateDirRef:'state:child-1',params:{scene:'scene'},depth:1,
    childWorkflow:{id:'child',title:'child',version:1,nodes:[{id:'finalize',phase:'P1',dependsOn:[],executor:{type:'done'}}],outputs:{result:{from:'nodeOutput.finalize'}}}};
}
function parent(): FlowState {
  return {workflowId:'parent',workflowVersion:7,params:{initiator:'spoof'},executionMode:'private',businessStatus:'RUNNING',currentPhase:'P5',activeNodes:[],nodeStates:{},workflowData:{},actionOutputs:{},flowHooks:{},auditLog:[],runInitiator:{id:'real-user',name:'Real User'}} as FlowState;
}
async function fixture(run:(f:{runtime:ReturnType<typeof createControllerFanoutRuntime>;store:ReturnType<typeof memoryStore>;calls:string[];output:{value:ExecutorResult};root:string})=>Promise<void>) {
  const root=await mkdtemp(join(tmpdir(),'fanout-runtime-'));
  const memory=memoryStore(); const calls:string[]=[];
  const output={value:{status:'succeeded',result:{experimentId:'exp',reportId:'report'}} as ExecutorResult};
  const deps: ControllerDeps={actionRegistry:createActionRegistry(),boundTaskFlow:new DatabaseTaskFlowAdapter('parent'),chatInject:async()=>{},executeNode:async()=>{throw new Error('parent executor must not be reused');},sessionKey:'parent-session',skillRoot:root,user:{id:'real-user'}};
  const runtime=createControllerFanoutRuntime({deps,parentState:parent(),parentFlowId:'parent',store:memory.store,executeWorkflow:executeWorkflowForTest,
    createChildExecutor:({sessionKey})=>async(_n,ctx)=>{calls.push(sessionKey);assert.equal((ctx.initiator as {id:string})?.id,'real-user');return structuredClone(output.value);},authorizeChild:async()=>{},pollIntervalMs:5});
  try{await run({runtime,store:memory,calls,output,root});}finally{await rm(root,{recursive:true,force:true});}
}

test('controller runtime executes an isolated managed child and persists explicit exports',async()=>{
 await fixture(async f=>{
  const result=await f.runtime.startOrGetChild(request(),new AbortController().signal);
  assert.equal(result.status,'succeeded');assert.deepEqual(result.outputs,{result:{experimentId:'exp',reportId:'report'}});
  assert.deepEqual(f.calls,['fanout:child-1']);
  const state=JSON.parse(f.store.runs.get('child-1')!.stateJson);
  assert.equal(state.subworkflowMeta.parentFlowId,'parent');assert.equal(state.subworkflowMeta.fanout.groupExecutionId,'g-1');
  assert.match(state.fanoutRuntimeContext.stateDir,/child-1|[a-f0-9]{16}/);
 });
});

test('rejoining a terminal child never re-executes its nodes',async()=>{
 await fixture(async f=>{
  const first=await f.runtime.startOrGetChild(request(),new AbortController().signal);
  const second=await f.runtime.startOrGetChild(request(),new AbortController().signal);
  assert.deepEqual(second,first);assert.equal(f.calls.length,1);
 });
});

test('same child ID with a different input is rejected rather than reusing an unrelated run',async()=>{
 await fixture(async f=>{
  await f.runtime.startOrGetChild(request(),new AbortController().signal);
  await assert.rejects(f.runtime.startOrGetChild({...request(),params:{scene:'other'}},new AbortController().signal),/identity|digest|conflict/);
  assert.equal(f.calls.length,1);
 });
});

test('an already claimed nonterminal child is observed, not blindly replayed after recovery',async()=>{
 await fixture(async f=>{
  const req=request(); await f.runtime.startOrGetChild(req,new AbortController().signal);
  const record=f.store.runs.get('child-1')!; const state=JSON.parse(record.stateJson);
  state.nodeStates.finalize.status='running'; delete state.workflowData.outputs;
  f.store.runs.set('child-1',{...record,status:'running',stateJson:JSON.stringify(state)});
  const abort=new AbortController();setTimeout(()=>abort.abort(),20);
  await assert.rejects(f.runtime.startOrGetChild(req,abort.signal), e => (e as {disposition?:string}).disposition==='unknown');assert.equal(f.calls.length,1);
 });
});

test('aborted observation does not create a new child',async()=>{
 await fixture(async f=>{const abort=new AbortController();abort.abort();const r=await f.runtime.startOrGetChild(request(),abort.signal);assert.equal(r.status,'cancelled');assert.equal(f.store.runs.size,0);});
});


test('capacity busy retries the same durable child ID without redispatching', async()=>{
 await fixture(async f=>{
  let attempts=0;
  const originalStart=f.store.store.startChild.bind(f.store.store);
  f.store.store.readGroup=async()=>({group:{status:'running',deadlineAt:new Date(Date.now()+1000).toISOString()} as never,parentStatus:'running'});
  f.store.store.startChild=async(...args)=>{
   attempts++;
   if(attempts===1) throw new FanoutPersistenceError('HTTP 409 CAPACITY_BUSY',409);
   return originalStart(...args);
  };
  const result=await f.runtime.startOrGetChild(request(),new AbortController().signal);
  assert.equal(result.status,'succeeded');assert.equal(attempts,2);assert.deepEqual(f.calls,['fanout:child-1']);
  assert.equal(f.store.runs.has('child-1'),true);
 });
});
