import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DurableFanoutTaskFlow } from '../src/platform/fanout-taskflow.js';
import { DatabaseTaskFlowAdapter } from '../src/platform/database-taskflow.js';
import { FanoutPersistenceError, type FanoutPersistence, type FanoutRunRecord } from '../src/platform/fanout-persistence.js';

function fixture() {
  let record: FanoutRunRecord = {flowId:'child',workflowId:'wf',sessionKey:'fanout:child',status:'running',revision:1,stateJson:JSON.stringify({_fanoutRevision:1,subworkflowMeta:{parentFlowId:'parent',parentNodeId:'group',depth:1},nodeStates:{}})};
  let fail = false;
  const store = {
    readChild: async () => structuredClone(record),
    checkpointChild: async (_p:string,_c:string,expected:number,stateJson:string,status:string,currentStep?:string) => {
      if(fail) throw new Error('disk unavailable');
      if(expected !== record.revision) throw new FanoutPersistenceError('CAS conflict',409);
      if(record.status==='cancelled' && status!=='cancelled') throw new Error('terminal conflict');
      const data=JSON.parse(stateJson); data._fanoutRevision=expected+1;
      record={...record,stateJson:JSON.stringify(data),status,revision:expected+1}; return structuredClone(record);
    },
  } as FanoutPersistence;
  const adapter=new DurableFanoutTaskFlow(store,'parent',record,new DatabaseTaskFlowAdapter('legacy'));
  return {adapter,read:()=>record,setFail:(v:boolean)=>{fail=v;},cancelExternally:()=>{const state=JSON.parse(record.stateJson);state._fanoutCancelRequested=true;state._fanoutRevision=record.revision+1;record={...record,revision:record.revision+1,stateJson:JSON.stringify(state)};}};
}

test('child taskflow commits checkpoints before reporting transitions', async () => {
  const f=fixture(); await f.adapter.resume({flowId:'child',expectedRevision:1,stateJson:'{"nodeStates":{"first":{"status":"succeeded"}}}',currentStep:'first'});
  assert.equal(f.read().revision,2); assert.equal(JSON.parse(f.read().stateJson).nodeStates.first.status,'succeeded');
  assert.equal(JSON.parse(f.read().stateJson).subworkflowMeta.parentFlowId,'parent');
  assert.equal((await f.adapter.get('child'))?.revision,2);
});

test('child taskflow propagates failed persistence without moving the local revision', async () => {
  const f=fixture(); f.setFail(true);
  await assert.rejects(f.adapter.finish({flowId:'child',expectedRevision:1,stateJson:'{}'}),/disk unavailable/);
  assert.equal(f.read().revision,1); assert.equal((await f.adapter.get('child'))?.status,'running');
});

test('node execution checkpoints do not invalidate the controller revision token', async () => {
  const f=fixture(); await f.adapter.checkpoint({nodeStates:{first:{status:'running'}}} as never);
  assert.equal(f.read().revision,2);
  const result=await f.adapter.finish({flowId:'child',expectedRevision:1,stateJson:'{"nodeStates":{"first":{"status":"succeeded"}}}'});
  assert.equal(result.applied,true); assert.equal(f.read().status,'succeeded'); assert.equal(f.read().revision,3);
  assert.equal((await f.adapter.get('child'))?.revision,2);
});

test('stale controller writes and terminal resurrection are rejected', async () => {
  const f=fixture(); await f.adapter.resume({flowId:'child',expectedRevision:1,stateJson:'{}'});
  await assert.rejects(f.adapter.resume({flowId:'child',expectedRevision:1,stateJson:'{}'}),/conflict|冲突/);
  await f.adapter.fail({flowId:'child',expectedRevision:2,status:'cancelled',stateJson:'{}'});
  await assert.rejects(f.adapter.resume({flowId:'child',expectedRevision:3,stateJson:'{}'}),/terminal|终态/);
});


test('a cancellation-intent-only race preserves a later confirmed completion rather than leaking an active row', async () => {
 const f=fixture();f.cancelExternally();
 await f.adapter.finish({flowId:'child',expectedRevision:1,stateJson:'{"nodeStates":{"first":{"status":"succeeded"}},"workflowData":{"outputs":{"result":{"reportId":"late-report"}}}}'});
 assert.equal(f.read().status,'succeeded');
 const state=JSON.parse(f.read().stateJson);
 assert.equal(state._fanoutCancelRequested,true);assert.equal(state.workflowData.outputs.result.reportId,'late-report');
 assert.equal(f.read().revision,3);
});
