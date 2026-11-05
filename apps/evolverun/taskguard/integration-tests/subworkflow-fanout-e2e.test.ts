import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import express from 'express';
import type { Server } from 'node:http';
import { fixture as sqlFixture } from '../../clawweb/public/modules/workflow/server/repositories/__tests__/fanout-state-fixture.js';
import { FlowRunRepository } from '../../clawweb/public/modules/workflow/server/repositories/flow-run-repository.js';
import { createInternalRunsRouter } from '../../clawweb/public/modules/workflow/server/routes/internal/runs.js';
import { createInternalBotWorkflowPermissionsRouter } from '../../clawweb/public/modules/workflow/server/routes/internal/bot-workflow-permissions.js';
import { BotWorkflowPermissionRepository } from '../../clawweb/public/shared/server/repositories/bot-workflow-permission-repository.js';
import { executeWorkflowForTest, type ControllerDeps } from '../src/controller.js';
import { createActionRegistry } from '../src/actions/registry.js';
import { ApiClient } from '../src/db/api-client.js';
import { configureFanoutApiHost } from '../src/platform/fanout-host.js';
import { digestWorkflowSpec } from '../src/workflow-dependencies.js';
import { defaults } from '../src/config/types.js';
import { setCachedConfig } from '../src/config/config-cache.js';
import type { FlowState, WorkflowSpec } from '../src/types.js';

setCachedConfig({database:{type:'sqlite',sqlitePath:':memory:'},app:structuredClone(defaults)});

function parentAdapter(initial:FlowState): ControllerDeps['boundTaskFlow'] {
 let revision=1;let stateJson=JSON.stringify(initial);let status='running';
 const read=()=>({flowId:'parent',revision,stateJson,status});
 const update=async(input:Record<string,unknown>,next:string)=>{
  assert.equal(input.expectedRevision,revision);revision++;stateJson=String(input.stateJson??stateJson);status=next;return {applied:true,flow:read()};
 };
 return {get:async()=>read(),findLatest:async()=>read(),list:async()=>({flows:[read()]}),
  createManaged:async()=>{throw new Error('fanout must not use legacy createManaged');},
  setWaiting:input=>update(input,'waiting'),resume:input=>update(input,'running'),
  finish:input=>update(input,'succeeded'),fail:input=>update(input,'failed'),runTask:async()=>{throw new Error('not used by this workflow');}};
}

async function execute(cancelAtSeal=false, recoverAfterSeal=false) {
 const f=sqlFixture(); const dir=await mkdtemp(join(tmpdir(),'fanout-e2e-')); let server:Server|undefined;
 try {
  f.raw.exec('CREATE TABLE bot_workflow_permissions (workflow_id TEXT, bot_id TEXT, bot_owner_id TEXT, can_execute INTEGER, can_view INTEGER, can_edit INTEGER)');
  f.raw.prepare('INSERT INTO bot_workflow_permissions VALUES (?, ?, ?, 1, 1, 0)').run('child-wf','bot','owner');
  const app=express();app.use(express.json({limit:'5mb'}));
  app.use('/api/internal/runs',createInternalRunsRouter(new FlowRunRepository(f.db)));
  app.use('/api/internal/bot-workflow-permissions',createInternalBotWorkflowPermissionsRouter(new BotWorkflowPermissionRepository(f.db)));
  await new Promise<void>(resolve=>{server=app.listen(0,'127.0.0.1',resolve);});
  const api=new ApiClient({baseUrl:`http://127.0.0.1:${(server.address() as {port:number}).port}`,maxRetries:1});
  const child:WorkflowSpec={id:'child-wf',title:'Single experiment',version:1,nodes:[{id:'finalize',title:'Finalize',phase:'P1',dependsOn:[],executor:{type:'done'}}],outputs:{result:{from:'nodeOutput.finalize'}}};
  const items=Array.from({length:5},(_,i)=>({id:`item-${i}`,fail:i===1}));
  const workflow:WorkflowSpec={id:'parent-wf',title:'Parent',version:7,
    subworkflowDependencies:{'child-wf':{workflowId:'child-wf',publishedVersion:1,releaseId:'release-child-1',digest:digestWorkflowSpec(child),spec:child}},
    nodes:[{id:'fanout',title:'Experiments',phase:'P5',dependsOn:[],executor:{type:'subworkflow',workflowId:'child-wf',version:'pinned',
      params:{id:'{{item.id}}',fail:'{{item.fail}}'},fanout:{schemaVersion:'fanout.v1',items,itemIdFrom:['id'],concurrency:2,successPolicy:'all_done',finishMode:'wait_all',resultPolicy:'collect_all',groupTimeoutSeconds:10,cancelGraceSeconds:0.1}}},
      {id:'summarize',title:'Summary',phase:'P6',dependsOn:['fanout'],triggerRule:'all_success',executor:{type:'done'}}]};
  const state:FlowState={workflowId:workflow.id,workflowVersion:7,params:{},executionMode:'private',businessStatus:'RUNNING',currentPhase:'P5',activeNodes:[],
    nodeStates:{fanout:{status:'pending',phase:'P5',executor:'subworkflow'},summarize:{status:'pending',phase:'P6',executor:'done'}},workflowData:{},actionOutputs:{},flowHooks:{},auditLog:[],originBotId:'bot:owner',runInitiator:{id:'reader',source:'delivery-context'}};
  let summaries=0;let active=0;let peak=0;const sessions:string[]=[];
  const deps:ControllerDeps={actionRegistry:createActionRegistry(),boundTaskFlow:parentAdapter(state),chatInject:async()=>{},sessionKey:'parent-session',skillRoot:dir,botId:'bot',ownerId:'owner',user:{id:'reader'},
    executeNode:async(_node,ctx)=>{summaries++;const result=ctx.nodeOutput?.fanout as Record<string,unknown>;assert.equal(result.total,5);assert.equal(result.failedCount,1);assert.equal(result.successCount,4);return {status:'succeeded',result:{summarized:5}};}};
  configureFanoutApiHost(deps,api,context=>async(_node,_ctx,current)=>{
    sessions.push(context.sessionKey);active++;peak=Math.max(peak,active);
    await new Promise(resolve=>setTimeout(resolve,15));active--;
    return current.params.fail==='true'
      ? {status:'failed',error:'confirmed experiment failure',result:{success:false,fanoutError:{category:'execution',code:'GATEWAY_CONNECT_FAILED',message:'confirmed not submitted',acceptanceKnown:true}}}
      : {status:'succeeded',result:{experimentId:current.params.id,reportId:`report-${current.params.id}`}};
  });
  if(cancelAtSeal){
    const original=deps.createFanoutRuntime!;
    deps.createFanoutRuntime=(...args)=>{const runtime=original(...args);const save=runtime.save.bind(runtime);runtime.save=async(group,expected)=>{await save(group,expected);if(group.status==='succeeded')f.raw.prepare("UPDATE flow_runs SET status='cancelled' WHERE flow_id='parent'").run();};return runtime;};
  }
  if(recoverAfterSeal){
    const original=deps.createFanoutRuntime!;let crashed=false;
    deps.createFanoutRuntime=(...args)=>{const runtime=original(...args);const save=runtime.save.bind(runtime);runtime.save=async(group,expected)=>{await save(group,expected);if(group.status==='succeeded'&&!crashed){crashed=true;throw Error('simulated lost seal acknowledgement');}};return runtime;};
  }
  await executeWorkflowForTest({deps,workflow,state,flowId:'parent',revision:1});
  if(recoverAfterSeal){
    assert.equal(summaries,0);
    const recovered=JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json) as FlowState;
    assert.equal(recovered.nodeStates.fanout.status,'running');
    deps.boundTaskFlow=parentAdapter(recovered);
    await executeWorkflowForTest({deps,workflow,state:recovered,flowId:'parent',revision:1});
    assert.equal(recovered.nodeStates.summarize.status,'succeeded');
  }
  const stored=JSON.parse(f.raw.prepare("SELECT state_json FROM flow_runs WHERE flow_id='parent'").get().state_json);
  const group=stored.subworkflowFanouts.fanout;
  assert.equal(group.status,'succeeded');assert.equal(group.result.total,5);assert.equal(group.result.failedCount,1);
  assert.equal(group.result.items[1].error.category,'execution');assert.equal(new Set(sessions).size,5);assert.ok(peak<=2);assert.equal(peak,2);
  const childRows=f.raw.prepare("SELECT * FROM flow_runs WHERE workflow_id='child-wf'").all();assert.equal(childRows.length,5);
  assert.equal(childRows.filter((r:any)=>r.status==='succeeded').length,4);assert.equal(childRows.filter((r:any)=>r.status==='failed').length,1);
  assert.equal(summaries,cancelAtSeal?0:1);
  if(!cancelAtSeal&&!recoverAfterSeal)assert.equal(state.nodeStates.summarize.status,'succeeded');
 } finally {if(server)await new Promise<void>(resolve=>server!.close(()=>resolve()));await f.db.close();await rm(dir,{recursive:true,force:true});}
}

test('real controller, HTTP and SQLite collect four successes and one confirmed failure before P6',()=>execute());
test('a parent cancellation after the durable group seal prevents P6 side effects',()=>execute(true));

test('recovery after a lost seal acknowledgement reuses the five durable children',()=>execute(false,true));
